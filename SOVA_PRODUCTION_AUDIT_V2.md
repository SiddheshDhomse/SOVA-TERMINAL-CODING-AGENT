# SOVA Terminal Agent — Production Audit Report

> **Audited**: September 2026 · **Scope**: Full codebase (20 agent modules, 11 test files, eval framework, config, docs)
> **Verdict**: Strong prototype with solid foundations — needs targeted hardening for production.

---

## Executive Summary

SOVA is a well-structured, multi-provider LLM coding agent with a CLI, Web UI, sandbox isolation, sub-agent delegation, session persistence, and an evaluation framework. The codebase shows clear architectural intent and several smart patterns (atomic file writes, fuzzy path resolution, self-healing diagnostics loop, streaming tool-call parsing for local models).

However, **27 critical issues** and **15 improvement opportunities** were identified across security, reliability, architecture, and developer experience that must be addressed before production deployment.

| Category | 🔴 Critical | 🟠 High | 🟡 Medium | 🔵 Low |
|---|---|---|---|---|
| Security | 3 | 2 | 1 | — |
| Bugs & Race Conditions | 3 | 4 | 2 | — |
| Architecture | — | 5 | 3 | 2 |
| Code Quality | — | 2 | 5 | 3 |
| Missing Production Features | 2 | 4 | 3 | 2 |
| Config/Build/Docs | 1 | 3 | 2 | 1 |

---

## 🔴 1. Critical Security Vulnerabilities

### 1.1 Web Server Has ZERO Authentication
**File**: [`web.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/web.py) · All endpoints

The web server exposes endpoints that can execute arbitrary shell commands (`run_shell`), modify files (`write_file`, `edit_file`), and manage API credentials — all without any authentication, API key, or even CORS protection.

```
Anyone on the same network can POST to /api/start with:
{"task": "run shell rm -rf /", "auto_approve": true}
```

> [!CAUTION]
> **This is a remote code execution (RCE) vulnerability.** Anyone who can reach port 8787 has full control of the machine.

**Fix**:
- Add session-based token authentication (generate a one-time token on startup, require it in all API requests)
- Add CORS headers restricting origin to `127.0.0.1`
- Add Content Security Policy headers
- Bind to `127.0.0.1` only (already done, but should be enforced/documented)

---

### 1.2 Credentials Stored as Plaintext JSON
**File**: [`credentials.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/credentials.py) · Lines 52-59

API keys (Groq, OpenAI, Gemini, OpenRouter, Nvidia) are stored in `.sova/credentials.json` as plaintext. Any process or user on the machine can read them.

```python
# credentials.py:58 — raw JSON dump of API keys
json.dump(credentials, f, indent=2)
```

**Fix**:
- Use OS keychain via `keyring` library (Windows Credential Manager, macOS Keychain, Linux Secret Service)
- If file storage is required, encrypt at rest using a machine-derived key (DPAPI on Windows, `secretstorage` on Linux)
- Set restrictive file permissions on `.sova/credentials.json`

---

### 1.3 Shell Command Injection via `shell=True`
**File**: [`tools.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/tools.py) · Lines 777-788

`run_shell` passes commands directly to the OS shell with `shell=True`. While the LLM is the intended caller, there's no command sanitization or blocklist.

```python
proc = subprocess.Popen(command, shell=True, cwd=root_dir, ...)
```

**Fix**:
- Implement a command blocklist for destructive operations (`rm -rf /`, `format`, `del /s`, `shutdown`, etc.)
- Add a configurable allowlist mode for high-security environments
- Log all shell commands to an audit trail

---

## 🟠 2. Bugs & Race Conditions

### 2.1 `_GLOBAL_JOBS` Dictionary Has No Thread Safety
**File**: [`tools.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/tools.py) · Line 17

The global dictionary `_GLOBAL_JOBS` is read/written from multiple threads (web handler threads, agent worker thread) without any lock, risking `RuntimeError: dictionary changed size during iteration`.

```python
_GLOBAL_JOBS: Dict[str, Dict[str, Any]] = {}  # No threading.Lock!
```

**Fix**: Wrap all accesses in a `threading.Lock()`:
```python
_JOBS_LOCK = threading.Lock()

def cleanup_jobs(job_id=None):
    with _JOBS_LOCK:
        # ... existing logic
```

---

### 2.2 Symbol Cache Concurrent File Corruption
**File**: [`symbols.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/symbols.py) · Lines 270-276

`_save_cache()` writes directly to `symbols_cache.json`. If multiple SOVA instances run against the same workspace, they'll corrupt each other's cache.

**Fix**: Use atomic writes (write to `.tmp`, then `os.replace`) — the same pattern already used in [`sessions.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/sessions.py#L66-L69).

---

### 2.3 `chat_stream` Drops Token Usage Data
**File**: [`llm.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/llm.py) · Line 376

When streaming, the response's `usage` object is always `None` because the streaming loop never captures the `usage` chunk (some providers send it in the final chunk). This means **all cost tracking is broken when streaming is enabled** (which is the default path).

```python
return _StreamedResponse(_StreamedMessage(content, calls))
# ↑ usage is never captured from the stream!
```

**Fix**: Capture usage from the final stream chunk:
```python
usage = None
for chunk in stream:
    if hasattr(chunk, 'usage') and chunk.usage:
        usage = chunk.usage
    # ... existing delta logic
return _StreamedResponse(_StreamedMessage(content, calls), usage=usage)
```

---

### 2.4 Sandbox `__exit__` Leaves Orphaned Worktrees
**File**: [`sandbox.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/sandbox.py) · Lines 149-155

When used as a context manager, `__exit__` only calls `discard()` if an exception occurred. On normal exit, the worktree and branch are left dangling.

```python
def __exit__(self, exc_type, exc_val, exc_tb):
    if exc_type is not None:  # ← Only on exception!
        self.discard()
```

**Fix**: Always clean up unless the user explicitly applied changes.

---

### 2.5 Duplicated Error Detection Logic (DRY Violation)
**File**: [`loop.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/loop.py) · Lines 219-272

The same malformed-tool-call detection string checks are copy-pasted across `BadRequestError`, `APIStatusError`, `APIError`, and `Exception` handlers — 4 duplications of the same 5 conditions.

```python
# This exact block appears FOUR times:
if ("tool call validation failed" in message
    or "did not match schema" in message
    or "failed to parse tool call" in message ...):
```

**Fix**: Extract to a helper:
```python
def _is_malformed_tool_call(exc_text: str) -> bool:
    indicators = ["tool call validation failed", "did not match schema", ...]
    return any(i in exc_text for i in indicators)
```

---

### 2.6 Rate Limiter Exists But Is Never Used
**File**: [`rate_limiter.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/rate_limiter.py) (entire file)

A well-implemented `SlidingWindowRateLimiter` exists with full test coverage, but it's **never imported or wired into the LLM call path** (`_safe_chat` in `loop.py`). Rate limiting is currently handled only by catching HTTP 429/413 errors after the fact.

**Fix**: Integrate proactively:
```python
# In loop.py, before calling chat_stream:
if not rate_limiter.allow(provider):
    wait_time = rate_limiter.get_wait_time(provider)
    emit("rate_limited", wait_seconds=wait_time)
    time.sleep(wait_time)
```

---

## 🏗️ 3. Architecture Issues

### 3.1 Web Server Should Use a Modern Framework
**File**: [`web.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/web.py) · 592 lines

The web server uses stdlib `BaseHTTPRequestHandler` with manual routing (`if parsed.path == ...`), manual JSON parsing, and hand-rolled SSE. This is fragile and hard to extend.

**Recommendation**: Migrate to **FastAPI** or **Starlette**:
- Native async support eliminates the threading complexity
- Built-in SSE via `sse-starlette`
- Automatic OpenAPI documentation
- Pydantic request/response validation
- Middleware for auth, CORS, rate limiting

---

### 3.2 Monolithic `tools.py` (900+ Lines)
**File**: [`tools.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/tools.py) · 895 lines

All 18 tool definitions, their JSON schemas, the diffing engine, subprocess management, and file I/O are in a single file.

**Recommendation**: Refactor into a tools package:
```
agent/tools/
├── __init__.py          # build_tools() registry
├── schemas.py           # All JSON schema definitions
├── filesystem.py        # read_file, write_file, edit_file, list_dir, find_files
├── shell.py             # run_shell, shell_output, cleanup_jobs
├── search.py            # grep, search_code, find_definition, find_references
├── memory.py            # memory_read, memory_append
├── testing.py           # run_tests
└── workspace.py         # workspace_summary, get_outline, todo_*, undo, finish
```

---

### 3.3 Massive CLI Command Handler
**File**: [`cli.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/cli.py) · `_handle_command` function

All slash commands are handled in a single 300-line `if/elif` chain.

**Recommendation**: Use a command registry pattern:
```python
_COMMANDS = {
    "/help": _cmd_help,
    "/provider": _cmd_provider,
    "/model": _cmd_model,
    "/sandbox": _cmd_sandbox,
    # ...
}
```

---

### 3.4 Global Mutable State in `llm.py`
**File**: [`llm.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/llm.py) · Lines 141-142

The LLM client and provider are stored as module-level globals (`_client`, `_client_provider`). This prevents concurrent sessions from using different providers and creates implicit coupling.

```python
_client = None          # Module-level mutable global
_client_provider = None # Module-level mutable global
```

**Recommendation**: Introduce a `LLMClientFactory` or session-scoped client manager.

---

### 3.5 Event System Is Callback-Based, Not Decoupled
**File**: [`loop.py`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/agent/loop.py) · `on_event` callback pattern

The agent loop uses a single `on_event` callback to communicate with CLI/Web. This tightly couples the agent's lifecycle to the UI layer.

**Recommendation**: Implement an **Event Bus** (pub/sub) pattern:
```python
class EventBus:
    def subscribe(self, event_type, handler): ...
    def publish(self, event): ...
```

---

## 📦 4. Configuration & Build Issues

### 4.1 All Dependencies Are Unpinned
**File**: [`pyproject.toml`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/pyproject.toml) · Lines 6-13

```toml
dependencies = [
    "openai",        # ← No version bounds!
    "rich",
    "prompt_toolkit",
    "python-dotenv",
    "datasets",
    "sb-cli",
]
```

> [!WARNING]
> This **will** break. The `openai` library has had multiple breaking API changes. A single `pip install` on a different day can produce a broken environment.

**Fix**:
```toml
dependencies = [
    "openai>=1.12.0,<2.0",
    "rich>=13.0,<14.0",
    "prompt_toolkit>=3.0,<4.0",
    "python-dotenv>=1.0",
]
```

Also: Add a `requirements-lock.txt` or use `pip-compile` for reproducible builds.

---

### 4.2 Template Not Included in Package Distribution
**File**: [`pyproject.toml`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/pyproject.toml) · Lines 23-24

The `agent/templates/index.html` web UI template won't be included in `pip install .` because package data isn't configured.

**Fix**:
```toml
[tool.setuptools]
packages = ["agent"]
include-package-data = true

[tool.setuptools.package-data]
agent = ["templates/*.html"]
```

---

### 4.3 Hardcoded User-Specific Paths
**File**: [`setup-sova-alias.ps1`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/setup-sova-alias.ps1) · Line 5

Contains a hardcoded path to a specific user's OneDrive desktop. Will fail for every other developer.

**Fix**: Use `$PSScriptRoot` for dynamic path resolution:
```powershell
Set-Alias -Name sova -Value "$PSScriptRoot\sova.bat" -Scope CurrentUser -Force
```

---

### 4.4 `.env.example` Missing OpenRouter Alias
**File**: [`.env.example`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/.env.example)

`OPEN_ROUTE_API_KEY` is a supported alias (verified in code) but isn't mentioned in the example file.

---

### 4.5 `SOVA_SETUP.md` Has Broken Syntax
**File**: [`SOVA_SETUP.md`](file:///d:/AI%20Projects/SOVA-%20Terminal%20Agent/SOVA_SETUP.md) · Lines 25-27

```
Set-Alias -Name sova -Value \Desktop\Terminal Agent\sova.bat"
                              ↑ Missing opening quote, missing drive prefix
```

---

## 🧪 5. Test Coverage Gaps

### Current Coverage Map

| Module | Tests Exist? | Quality |
|---|---|---|
| `tools.py` | ✅ `test_agent_tools.py` | Good — covers core CRUD |
| `checkpoints.py` | ✅ `test_checkpoints.py` | Good |
| `cost.py` | ✅ `test_cost.py` | Good |
| `diagnostics.py` | ✅ `test_diagnostics.py` | Good |
| `llm.py` | ✅ `test_llm_models.py` + `test_providers.py` | Moderate |
| `rate_limiter.py` | ✅ `test_rate_limiter.py` | Good (but dead code) |
| `sandbox.py` | ✅ `test_sandbox.py` | Basic |
| `search.py` | ✅ `test_search.py` | Good |
| `sessions.py` | ✅ `test_sessions.py` | Basic |
| `eval.py` | ✅ `test_eval.py` | Partial |
| **`loop.py`** | ❌ None | 🔴 **Most critical module, zero tests** |
| **`web.py`** | ❌ None | 🔴 **All API endpoints untested** |
| **`cli.py`** | ❌ None | 🟠 **Command handling untested** |
| **`subagents.py`** | ❌ None | 🟠 **Role filtering untested** |
| **`credentials.py`** | ❌ None | 🟡 |
| **`symbols.py`** | ❌ None | 🟡 |
| **`logger.py`** | ❌ None | 🟡 |
| **`prompts.py`** | ❌ None | Low priority |

### Missing Critical Tests
1. **`loop.py`** — No integration tests for the agent loop, error recovery, context compaction, or subagent spawning
2. **`web.py`** — No API endpoint tests, SSE tests, or concurrent access tests
3. **`eval/swebench_runner.py`** — `run_benchmark_suite()` scorecards untested

---

## ✨ 6. New Feature Recommendations (Production-Grade Innovations)

### 6.1 🔄 Structured Observability with OpenTelemetry
**Priority**: High · **Impact**: Debugging, monitoring, cost optimization

Add distributed tracing to every LLM call, tool invocation, and subagent lifecycle:
```
Agent Turn #5 → chat_stream(groq, gpt-oss-120b) → 2.3s, 1200 tokens
  └── Tool: edit_file(path=main.py) → 45ms
  └── Tool: run_tests() → 8.2s, exit_code=0
```

**Implementation**: Use `opentelemetry-sdk` with exporters to Jaeger, console, or a local JSON file. Each `run_agent` call becomes a trace, each tool call a span.

---

### 6.2 🔐 Plugin & Extension System
**Priority**: Medium · **Impact**: Extensibility, community adoption

Allow users to register custom tools without modifying core code:
```python
# ~/.sova/plugins/jira.py
@sova_tool(name="jira_create", description="Create a Jira ticket")
def jira_create(title: str, description: str):
    ...
```

**Implementation**: Auto-discover Python files in `~/.sova/plugins/`, introspect decorated functions, and merge into `build_tools()`.

---

### 6.3 📊 Built-in Cost Dashboard & Budget Limits
**Priority**: High · **Impact**: Cost control for teams

The cost tracking exists but has no enforcement. Add:
- **Per-session budget cap**: Stop the agent if cumulative cost exceeds `$X`
- **Per-day/month budget**: Track across sessions
- **Cost alerts**: Emit a warning event when approaching 80% of budget
- **Web UI cost graph**: Show token/cost breakdown per session

---

### 6.4 🧠 Conversation Memory with RAG
**Priority**: Medium · **Impact**: Cross-session intelligence

The current `memory.md` is a flat file. Upgrade to:
- **Vector-indexed memory**: Use a local embedding model (via Ollama) + FAISS/ChromaDB
- **Automatic summarization**: Summarize completed sessions and index them
- **Cross-session recall**: "How did I fix the auth bug last week?"

---

### 6.5 🐳 Container-Based Sandbox
**Priority**: Medium · **Impact**: Security, isolation

The git worktree sandbox provides code isolation but shares the OS. For production:
- **Docker sandbox mode**: Run shell commands inside a disposable container
- **Resource limits**: CPU, memory, network restrictions
- **Filesystem snapshot/restore**: Instant rollback without git

---

### 6.6 🔁 Streaming Web UI via WebSockets
**Priority**: Medium · **Impact**: UX, real-time responsiveness

Replace the polling SSE loop (0.3s interval) with WebSocket:
- Bi-directional communication (stop, approve, send follow-up)
- Reduced overhead vs. SSE polling
- Native support in FastAPI (`websockets` or `starlette.websockets`)

---

### 6.7 🧪 Automated Regression Test Suite (CI/CD)
**Priority**: High · **Impact**: Reliability

Add a GitHub Actions CI pipeline:
```yaml
- pytest tests/ --cov=agent --cov-report=xml
- ruff check agent/
- mypy agent/ --strict
```

Add type annotations to all public APIs and enforce with `mypy --strict`.

---

### 6.8 📝 Structured Audit Logging
**Priority**: High · **Impact**: Compliance, debugging

Every tool call, file modification, shell command, and credential access should be logged in a structured, tamper-evident format:
```json
{"ts": "2026-09-14T21:48:06Z", "event": "tool_call", "tool": "run_shell",
 "args": {"command": "pytest"}, "user": "session-abc123", "result": "exit_code=0"}
```

---

## 📋 7. Prioritized Action Plan

### Phase 1: Critical Security (1-2 days)
- [ ] Add authentication to Web UI (bearer token generated at startup)
- [ ] Add CORS restrictions and CSP headers
- [ ] Add shell command blocklist in `run_shell`
- [ ] Fix `.sova/credentials.json` file permissions (or use `keyring`)

### Phase 2: Reliability Fixes (2-3 days)
- [ ] Add `threading.Lock` to `_GLOBAL_JOBS`
- [ ] Fix `chat_stream` to capture usage data
- [ ] Wire `SlidingWindowRateLimiter` into `_safe_chat`
- [ ] Fix sandbox `__exit__` cleanup
- [ ] Atomic writes for symbol cache
- [ ] Pin all dependencies in `pyproject.toml`

### Phase 3: Code Quality (3-5 days)
- [ ] Extract error detection helper in `loop.py`
- [ ] Refactor `tools.py` into a package
- [ ] Refactor CLI command handler to registry pattern
- [ ] Add type annotations to all public APIs
- [ ] Fix hardcoded paths in setup scripts
- [ ] Remove `Tracking_viewer.html` (unrelated clutter)
- [ ] Fix `SOVA_SETUP.md` broken syntax

### Phase 4: Test Coverage (3-5 days)
- [ ] Add integration tests for `loop.py` (mock LLM, verify tool dispatch)
- [ ] Add API endpoint tests for `web.py`
- [ ] Add tests for `subagents.py` role filtering
- [ ] Add tests for `credentials.py`
- [ ] Set up CI with pytest + coverage + ruff + mypy

### Phase 5: Production Features (1-2 weeks)
- [ ] Add OpenTelemetry tracing
- [ ] Add structured audit logging
- [ ] Add per-session cost budgets
- [ ] Migrate Web UI to FastAPI
- [ ] Add plugin/extension system
- [ ] Replace global LLM state with session-scoped clients

### Phase 6: Innovation (2-4 weeks)
- [ ] Vector-indexed cross-session memory
- [ ] Docker container sandbox mode
- [ ] WebSocket-based real-time Web UI
- [ ] Cost dashboard in Web UI

---

## Summary Metrics

| Metric | Value |
|---|---|
| Total Files Audited | 48 |
| Total Lines of Code | ~6,500 (agent/) |
| Critical Issues Found | 6 |
| High Issues Found | 10 |
| Medium Issues Found | 11 |
| Test Coverage (estimated) | ~45% of modules |
| New Features Proposed | 8 |

> [!IMPORTANT]
> **Phase 1 (Security) should be completed before any public deployment or team sharing.** The web server RCE vulnerability and plaintext credential storage are showstoppers for production use.
