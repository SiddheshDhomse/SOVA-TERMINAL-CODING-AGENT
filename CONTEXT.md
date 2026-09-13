# SOVA — Production Coding Agent & Benchmark Harness: Project Context

> **Audience**: AI Coding Agents (Antigravity, Claude Code, Cursor, Aider, Devin) and Human Maintainers.  
> **Purpose**: Single source of truth for the codebase architecture, conventions, interfaces, runtime execution flows, and technical roadmap. Any agent modifying this repository **must read and adhere to this document**.

---

## 1. Executive Summary & Vision

**SOVA** is an autonomous terminal- and web-driven coding agent harness designed to solve real-world software engineering tasks (SWE-bench style) across multiple LLM providers (Groq, Ollama, Nvidia NIM, OpenAI).

### Target Evolution: From Prototype Agent to Production Coding Harness
SOVA is equipped with:
1. **Claude Code-Grade Frontends**:
   - Terminal CLI with interactive options boxes for permissions (`[y] Yes`, `[a] Always`, `[n] No`, `[c] Cancel`), interactive provider/model selectors, and session resumption.
   - Web UI with VS Code / GitHub Copilot-grade visual diff viewer supporting dual view modes:
     - **Inline Mode**: Dual line-number gutters (Old Line #, New Line #, change sign `+`/`-`, code).
     - **Split (Side-by-Side) Mode**: Synchronized dual panes (Original vs Modified) with diagonal stripe alignment placeholders.
     - Header stats badge (`+X -Y`), clipboard copy with instant feedback, and collapsible diff sections.
   - Interactive permission approval cards embedding the full Copilot diff directly in the approval request before execution.
2. **Resilient Agent Loop**:
   - Dynamic token-budget-aware compaction (e.g. 5,500 token budget for Groq `openai/gpt-oss-120b` preventing HTTP 413 8,000 TPM limit errors).
   - Tool-call / tool-result message pair preservation preventing OpenAI schema validation errors.
   - Automatic HTTP 413 / TPM rate limit interception with context pruning and auto-retry.
   - Robust malformed tool-call JSON error recovery: intercepts API-level JSON parse failures (`Failed to parse tool call arguments as JSON`) and retries with explicit escaping instructions.
   - **Text-Based JSON Tool-Call Fallback (`_parse_content_tool_calls`)**: Extracts and executes tool calls when smaller open-source models (e.g. Llama 3 on Ollama) output JSON directly into `message.content` instead of using the API tool-calls channel.
3. **Robust Tooling & Safety Hygiene**:
   - **Destructive Overwrite Guards**: Explicit system prompts and error messages forbidding `write_file` for partial changes or comment insertions, preventing complete file wipes.
   - **CLI Overwrite Alerts**: In `agent/cli.py`, `write_file` on existing files triggers a bright red alert with line counts and a full unified diff preview before requesting approval.
   - Line-bounded and CRLF-insensitive `edit_file` with `start_line` and `end_line`.
   - Tool output capping on `read_file`, `run_shell`, `grep`, and `find_files` to conserve token budgets.
   - Process cleanup (`cleanup_jobs()`) preventing zombie background processes on exit or cancellation.
4. **Centralized Observability**:
   - Rotating file logger in `.sova/logs/sova.log`.
   - Per-session structured step trajectories in `.sova/logs/<session_id>.trajectory.jsonl`.

---

## 2. Directory Layout & Module Responsibilities

```
SOVA- Terminal Agent/
├── .env.example              # Sample environment configuration
├── pyproject.toml            # Packaging and CLI/Web entrypoints (sova, sova-web)
├── requirements.txt          # Python dependencies
├── CONTEXT.md                # [THIS FILE] Master project context for all coding agents
├── agent/                    # Core agent engine
│   ├── __init__.py           # Package marker
│   ├── cli.py                # Claude Code-grade terminal CLI (Rich + prompt_toolkit)
│   ├── logger.py             # Centralized rotating logger & session trajectory recorder
│   ├── llm.py                # Multi-provider client, model catalog, token budgeting, streaming
│   ├── loop.py               # ReAct loop, token-budget compaction, 413 recovery, subagent spawner
│   ├── prompts.py            # System prompts and behavior guidelines
│   ├── sessions.py           # Session persistence (.sova/sessions/*.json)
│   ├── tools.py              # Precision file editing, sandboxing checks, and process management
│   └── web.py                # Claude Code-grade Web UI & API (ThreadingHTTPServer + SSE)
├── eval/                     # Evaluation harness
│   ├── __init__.py           # Package marker
│   ├── swebench_runner.py    # SWE-bench Lite runner (git add -A + git diff --cached -> predictions.jsonl)
│   ├── explore_swebench.py   # SWE-bench dataset exploration utility
│   └── toy_benchmark/        # Local lightweight eval suite (no Docker needed)
│       ├── __init__.py
│       ├── tasks.py          # Synthetic challenge definitions
│       └── run_toy_eval.py   # Local runner asserting task completion
├── .sova/                    # Runtime artifacts (gitignored)
│   ├── history               # CLI prompt_toolkit input history
│   ├── logs/                 # Central sova.log and <session_id>.trajectory.jsonl
│   ├── memory.md             # Cross-session persistent notes
│   ├── jobs/                 # Logs for background shell jobs
│   └── sessions/             # Saved session JSON files
└── tests / test_*.py         # Unit tests (test_agent_tools.py, test_llm_models.py, test_providers.py)
```

---

## 3. Architecture & Runtime Execution Flow

### 3.1 The Core Agent Loop (`agent/loop.py`)
1. **Classification Turn**: `_is_chat()` queries the LLM to differentiate pure questions (`CHAT`) from code inspection/mutation requests (`TASK`).
2. **Token-Budget Compaction**: `_maybe_compact()` computes `estimate_tokens(messages, tools)`. If tokens exceed 82% of `get_token_budget()`, older turns are safely summarized into a system prompt without cutting raw JSON and without separating assistant tool-calls from their tool responses.
3. **Emergency 413 / TPM Recovery**: If Groq returns HTTP 413 (e.g. 8,000 TPM limit exceeded), `_safe_chat()` intercepts the error, aggressively compacts history via `_force_compact()`, and auto-retries immediately.
4. **Tool Execution & Lifecycle**:
   - Sensitive tools (`write_file`, `edit_file`, `run_shell`) trigger permission checks via `on_permission()`.
   - Repeated failures on identical arguments abort after 3 consecutive errors.
   - Clean shutdown terminates all active background subprocesses via `cleanup_jobs()`.
5. **Multi-turn & Subagent Orchestration**:
   - `spawn_subagent`: Delegates self-contained subtasks with restricted depth (`allow_subagents=False`, `max_iterations=8`).

### 3.2 Tool Runtime & Sandboxing (`agent/tools.py`)
- **`read_file`**: Returns line-numbered text; caps lines (>250) and output size (>5,000 chars) to prevent context blowup.
- **`write_file`**: Overwrites file, runs Python syntax validation via `compile()`, and emits a unified diff.
- **`edit_file`**: Line-bounded and CRLF-normalized editing. Accepts optional `start_line` and `end_line` to uniquely target repeated code blocks.
- **`run_shell` / `shell_output`**: Runs foreground commands (capped at 2,500 chars) or background jobs tracked in `_GLOBAL_JOBS` and reaped cleanly.
- **`grep` / `find_files`**: Fast search using `ripgrep` (`rg`) if installed, with pure Python fallback capped at 50-60 entries.

### 3.3 Persistence & Observability
- **Central Logs** (`.sova/logs/sova.log`): Rotating file logger recording system lifecycle, errors, and task starts.
- **Session Trajectories** (`.sova/logs/<session_id>.trajectory.jsonl`): Detailed JSONL entries recording every step, tool invocation, token count, diff, and error.
- **Sessions** (`.sova/sessions/<session_id>.json`): Full conversation message history for seamless resumption in CLI (`/resume`) and Web UI.

---

## 4. Interfaces & Contracts

### 4.1 Event Stream Contract (`on_event` callback)
Frontends consume events emitted by `run_agent()`:
- `{"type": "thinking"}`: LLM stream initialized.
- `{"type": "thinking_delta", "text": "..."}`: Streamed response tokens.
- `{"type": "tool_call", "name": "...", "args": {...}}`: Tool call initiated.
- `{"type": "tool_result", "name": "...", "result": "...", "diff": "..."}`: Tool output + optional unified diff.
- `{"type": "todo", "todos": [{"content": "...", "status": "..."}]}`: Updated task checklist.
- `{"type": "answer", "text": "...", "verified": bool}`: Final assistant response.
- `{"type": "error", "message": "..."}`: Error message.

### 4.2 LLM Providers & Model Registry (`agent/llm.py`)
- **Providers**: `groq` (default), `ollama`, `nvidia`, `openai`.
- **Dynamic Catalog**: `get_available_providers()` and `get_models_for_provider(provider)`.
- **Token Budgets**: `get_token_budget(provider, model)`:
  - Groq `openai/gpt-oss-120b`: 5,500 tokens (safe under 8,000 TPM limit).
  - Groq `llama-3.3-70b-versatile`: 9,000 tokens.
  - Groq `llama-3.1-8b-instant`: 18,000 tokens.
  - Ollama: 16,000 tokens.
  - Nvidia: 32,000 tokens.
  - OpenAI: 64,000 tokens.

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
- **Web UI**: `python -m agent.web` or `sova-web` (http://127.0.0.1:8787)
- **Unit Tests**: `python -m unittest discover -s . -p "test_*.py"`
- **Toy Benchmark**: `python -m eval.toy_benchmark.run_toy_eval`
- **SWE-bench Predictions**: `python -m eval.swebench_runner --instance-ids sympy__sympy-20590`

### 5.3 Guidelines for Coding Agents
1. **Preserve Tool Call / Tool Result Invariants**: Never delete or isolate an assistant message with `tool_calls` without also removing or updating its corresponding `role: "tool"` responses.
2. **Cross-Platform File Operations**: Always normalize `\r\n` to `\n` in string matching to maintain compatibility between Windows and POSIX environments.
3. **Token Prudence**: Never uncap tool output. If reading or listing large directories, paginate or instruct the model to use line bounds.
