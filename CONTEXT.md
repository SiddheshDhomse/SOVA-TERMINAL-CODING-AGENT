# SOVA — Production Coding Agent & Benchmark Harness: Project Context

> **Audience**: AI Coding Agents (Antigravity, Claude Code, Cursor, Aider, Devin) and Human Maintainers.  
> **Purpose**: Single source of truth for the codebase architecture, conventions, interfaces, runtime execution flows, and technical roadmap. Any agent modifying this repository **must read and adhere to this document**.

---

## 1. Executive Summary & Vision

**SOVA** is an autonomous terminal- and web-driven coding agent framework designed to solve real-world software engineering tasks (SWE-bench style) while supporting cost-effective/local models (Groq, Ollama, Nvidia NIM, OpenAI-compatible APIs). 

### Target Evolution: From Prototype Agent to Production Coding Harness
Currently, SOVA contains a functional agentic loop, tool runtime, CLI, Web UI, and an evaluation prototype. The target end-state is a **production-grade coding agent harness** comparable to SWE-agent, OpenHands, and Claude Code Harness, featuring:
1. **Isolated Sandboxing**: Secure execution environments (Docker/Podman/microVM) preventing host corruption and dependency collisions.
2. **Deterministic Evaluation Pipeline**: Full SWE-bench benchmark execution with automated environment provisioning, conda/pip setup, gold test evaluation, and patch scoring.
3. **Resilient Agent Loop**: Token-budget-aware context compaction, fuzzy/AST-based multi-line editing, structured output repair, and robust subagent orchestration.
4. **Comprehensive Observability**: Full token/cost accounting, trajectory recording (SWE-bench / OpenTelemetry compliant), and reproducible run replay.

---

## 2. Directory Layout & Module Responsibilities

```
SOVA- Terminal Agent/
├── .env.example              # Sample environment configuration
├── pyproject.toml            # Project packaging and entrypoints (sova, sova-web)
├── requirements.txt          # Python dependencies
├── CONTEXT.md                # [THIS FILE] Project context for all developing agents
├── agent/                    # Core agent engine
│   ├── __init__.py           # Package marker
│   ├── cli.py                # Rich + prompt_toolkit terminal interface
│   ├── llm.py                # OpenAI-compatible API client, model alias normalization, streaming
│   ├── loop.py               # Central ReAct tool-calling loop, compaction, subagent spawner
│   ├── prompts.py            # System prompts and behavior guidelines
│   ├── sessions.py           # Session persistence (.sova/sessions/*.json)
│   ├── tools.py              # Tool schemas, path sandbox validation, and implementations
│   └── web.py                # ThreadingHTTPServer + SSE web interface ("Control Room")
├── eval/                     # Evaluation harness
│   ├── __init__.py           # Package marker
│   ├── swebench_runner.py    # SWE-bench Lite runner (generates predictions.jsonl for sb-cli)
│   ├── explore_swebench.py   # Dataset exploration utility
│   └── toy_benchmark/        # Local lightweight eval suite (no Docker needed)
│       ├── __init__.py
│       ├── tasks.py          # Synthetic challenge definitions
│       └── run_toy_eval.py   # Local runner asserting task completion
├── .sova/                    # Runtime artifacts (gitignored)
│   ├── history               # CLI prompt_toolkit input history
│   ├── memory.md             # Cross-session persistent notes
│   ├── jobs/                 # Logs for background shell jobs
│   └── sessions/             # Saved session JSON files
└── tests / test_*.py         # Unit tests (test_agent_tools.py, test_llm_models.py, test_providers.py)
```

---

## 3. Architecture & Runtime Execution Flow

### 3.1 The Core Agent Loop (`agent/loop.py`)
1. **Classification Turn**: `_is_chat()` queries the LLM with `_CLASSIFY_SYSTEM` to detect if the prompt is purely conversational (`CHAT`) or requires file operations/inspections (`TASK`).
2. **Context Compaction**: `_maybe_compact()` monitors message count. If `> 40`, older messages are summarized via LLM and replaced with a single system message, leaving the latest 16 intact.
3. **LLM Invocation**: `_safe_chat()` calls `chat_stream()`, streaming thinking tokens to UI callbacks while accumulating tool calls.
4. **Tool Execution**:
   - Parses arguments and enforces permission check via `on_permission()` for sensitive tools (`write_file`, `edit_file`, `run_shell`).
   - Executes local tool implementations (`agent/tools.py`).
   - Traps repeated identical tool failures (aborts after 3 consecutive failures to avoid infinite loops).
5. **Multi-turn / Subagent Recursion**:
   - `spawn_subagent`: Invokes a fresh `run_agent()` loop with `allow_subagents=False` (1 level deep) and reduced max iterations (`max_iterations=8`).
6. **Completion**: Either calls `finish(summary=...)` or reaches `max_iterations`.

### 3.2 Tool Runtime & Sandboxing (`agent/tools.py`)
- **Path Resolution**: `_resolve(path)` ensures file paths are children of `root_dir` (rejects relative escapes like `../../`).
- **File Mutation**:
  - `read_file`: Line-numbered output (`1: ...`).
  - `write_file`: Overwrites file, auto-checks Python syntax with `compile()`, generates unified diff.
  - `edit_file`: Strict single-occurrence `old_str` replacement. Fails if `count != 1` or `old_str == ""`.
- **Search & Discovery**:
  - `grep`: Uses `ripgrep` (`rg`) if installed on the host system; falls back to pure Python `os.walk()`.
  - `find_files`: Uses `rg --files` or `fnmatch` fallback.
- **Process Management**:
  - `run_shell`: Foreground (blocking, with timeout) or background (`background=True`, writes to `.sova/jobs/{job_id}.log`).
  - `shell_output`: Polls process status and reads tail of log file.

### 3.3 Persistence & Memory
- **Project Memory** (`.sova/memory.md`): Long-term bullet-point memory read on initial agent launch and appended via `memory_append`.
- **Sessions** (`agent/sessions.py`): Saved as `.sova/sessions/<timestamp>-<hash>.json`. Saves the full OpenAI message history list for resumption via `/resume <id>` or web UI.

---

## 4. Interfaces & Contracts

### 4.1 Event Stream Contract (`on_event` callback)
All frontends (CLI `cli.py`, Web `web.py`, Eval `swebench_runner.py`) consume events emitted by `run_agent()`:
- `{"type": "thinking"}`: LLM stream initialized.
- `{"type": "thinking_delta", "text": "..."}`: Streamed response tokens.
- `{"type": "tool_call", "name": "...", "args": {...}}`: Tool call initiated.
- `{"type": "tool_result", "name": "...", "result": "...", "diff": "..."}`: Tool output + optional unified diff.
- `{"type": "todo", "todos": [{"content": "...", "status": "..."}]}`: Updated task checklist.
- `{"type": "answer", "text": "...", "verified": bool}`: Final assistant response. `verified=True` only if `finish` was called.
- `{"type": "error", "message": "..."}`: Error message.

### 4.2 LLM Providers (`agent/llm.py`)
- `SOVA_PROVIDER`: `groq` (default), `ollama`, or `nvidia`.
- `SOVA_MODEL`: Overrides model. Normalized through `MODEL_ALIASES` to resolve deprecated or shorthand names (e.g. `llama-3.3-70b-versatile` -> `openai/gpt-oss-120b`).
- Base URLs:
  - Groq: `https://api.groq.com/openai/v1`
  - Ollama: `http://localhost:11434/v1`
  - Nvidia: `https://integrate.api.nvidia.com/v1`

---

## 5. Development & Testing Conventions

### 5.1 Environment Setup
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
cp .env.example .env
```

### 5.2 Running the Systems
- **CLI**: `python -m agent.cli` or `sova`
- **Web UI**: `python -m agent.web` or `sova-web` (default: http://127.0.0.1:8787)
- **Unit Tests**: `python -m unittest discover -s . -p "test_*.py"`
- **Toy Benchmark**: `python -m eval.toy_benchmark.run_toy_eval`
- **SWE-bench Predictions**: `python -m eval.swebench_runner --instance-ids sympy__sympy-20590`

### 5.3 Working Guidelines for Coding Agents
1. **Never break OpenAI message schema validation**: When manipulating `messages`, always preserve pairs of `assistant` with `tool_calls` and their immediate `tool` role responses with valid `tool_call_id`.
2. **Cross-Platform Compatibility**: Always use `os.path` or `pathlib.Path`, handle Windows CRLF vs Unix LF gracefully, and avoid hardcoding Unix-only bash utilities (`rm`, `grep`, `cat`) in host tools.
3. **Zero Host Pollution**: Never execute permanent environment modifications outside `root_dir` or `.sova/`.
4. **Preserve Diff & Output Previews**: When modifying `write_file` or `edit_file`, always return a valid unified diff so the UI can render additions/deletions.

---

## 6. Known Technical Debt & Immediate Bugs (Summary)

*(See Senior Engineering Audit Report for comprehensive deep dive)*
1. **Host Execution Security Hole**: Shell commands run uncontained on the developer's workstation via `subprocess.Popen(..., shell=True)`.
2. **Compaction Truncation Bug**: `_maybe_compact` cuts raw JSON at 12,000 chars, creating invalid JSON and risking breaking `tool_calls` message chains.
3. **Fragile String-Replacement Editing**: `edit_file` fails easily on whitespace/newlines and lacks fuzzy match or line-bounded editing.
4. **SWE-bench Evaluation Incompleteness**: `swebench_runner.py` only clones git repos; it does not set up conda/virtual environments, install dependencies, or run repo-specific test harnesses.
5. **Web UI Concurrency Limits**: Web server uses single-session global state; concurrent requests collide or trigger 409 conflicts.
