# SOVA Autonomous Coding Harness: Strategic Development Roadmap

This document outlines the architecture roadmap and sequential milestones for evolving **SOVA** from a resilient terminal & web coding agent into an industry-grade, autonomous software engineering harness.

---

## Current Status (Foundation Completed)
- [x] **Terminal CLI (`agent/cli.py`)**: Rich interactive interface, line-numbered diff previews, interactive permission dialogs (`[y] Yes`, `[a] Always`, `[n] No`, `[c] Cancel`), destructive overwrite guardrails, interactive commands (`/provider`, `/model`, `/resume`, `/logs`, `/clear`, `/help`).
- [x] **Web Control Room (`agent/web.py`)**: Dual-mode Copilot-grade diff viewer (`[ ▤ Inline ]` and `[ ◫ Split ]`), approval cards with embedded diffs, dynamic provider/model dropdowns, session history drawer with one-click resume.
- [x] **Agent Loop Resilience (`agent/loop.py` & `agent/llm.py`)**: Groq 8,000 TPM limit defense (5,500 token compaction ceiling), HTTP 413 auto-recovery, malformed JSON recovery, and text-based JSON tool call extraction for local models.
- [x] **Tools & Observability (`agent/tools.py`, `agent/logger.py`)**: Precision line-bounded `edit_file`, output capping on shell/search/read tools, rotating logs (`.sova/logs/sova.log`), and structured per-session JSONL trajectories (`.sova/logs/<session_id>.trajectory.jsonl`).
- [x] **Base Test Suite**: 23/23 passing unit tests (`test_*.py`).

---

## 🎯 Phase 1: Benchmark Execution & Scoring Engine (Completed)
*Objective: Validate SOVA's end-to-end coding problem-solving rate, automated patch generation, and test verification.*

- [x] **1.1 Local Toy Benchmark Suite Execution**
  - Run `eval/toy_benchmark/run_toy_eval.py` covering bug fixing (`fix_add`) and feature implementation (`reverse_string`).
  - Result: 100% pass rate (`[PASS] fix_add`, `[PASS] reverse_string`).
  - Audit `.sova/logs/` trajectories to ensure zero API quota blowups and clean exit on Windows.
- [x] **1.2 Benchmark Reliability & Hardening**
  - Added automatic repair (`_normalize_content`) for LLMs that double-escape literal `\n` in `write_file` and `edit_file`.
  - Added namespaced tool alias resolution (e.g. `repo_browser.list_dir` -> `list_dir`) in agent loop.
  - Added line number argument aliases (`line_start`/`line_end` -> `start_line`/`end_line`).
  - Added Windows environment guidance to `SYSTEM_PROMPT` to prevent invalid POSIX heredoc invocations.
  - Made console event stream output UTF-8 safe with fallback for Windows `cp1252` encoding.
  - Excluded `.sova/` logs from git tracking during benchmark evaluations to avoid polluting patches.
  - Added `close_logger()` in `eval/swebench_runner.py` cleanup to prevent Windows file-handle lock errors.
  - Added `--model` override and session trajectory recording to `eval/swebench_runner.py`.
- [x] **1.3 Real-World SWE-bench Lite Trial**
  - Executed trial against real-world SWE-bench Lite instance (`psf__requests-2674`).
  - Validated automated repository checkout, multi-turn file exploration via `list_dir`, `grep`, and `read_file`.
  - Validated clean patch generation without log pollution and verified submission formatting in `predictions.jsonl`.

---

## 🧠 Phase 2: Codebase Intelligence & AST Symbol Indexing (Completed)
*Objective: Enable instant, token-efficient repository navigation without burning token limits reading full files.*

- [x] **2.1 AST Symbol Extractor (`agent/symbols.py`)**
  - Implemented Python stdlib `ast` parser (classes, methods, functions, inheritance, arguments, type annotations, 1-line docstrings, line spans).
  - Implemented polyglot regex parsers for JavaScript, TypeScript, Go, and Rust.
  - Implemented incremental disk caching in `.sova/symbols_cache.json` using file timestamps (`mtime`) for sub-millisecond lookups.
  - Added new agent tools to `agent/tools.py`:
    - `get_outline(path)`: High-density architectural outline with line numbers in ~120 tokens.
    - `find_definition(symbol, path=None)`: Fast symbol declaration lookup across project files.
    - `find_references(symbol, path=None)`: Call-site and reference finder tagged with `[DEF]` annotations.
    - `workspace_summary()`: Auto-detects tech stack, test runner, entry points, and indexed symbol counts.
- [x] **2.2 Persistent Workspace Context & Prompt Integration**
  - Auto-injects compact workspace summary (stack, test runner, entry points) into the initial system prompt on session boot.
  - Updated `SYSTEM_PROMPT` in `agent/prompts.py` to instruct the agent to use symbols before reading entire files.
- [x] **2.3 Testing & Evaluation Verification**
  - Created `test_symbols.py` covering all AST extraction, definitions, references, caching, and tool bindings (9/9 tests pass).
  - All 34 unit tests pass in 0.35s.
  - Verified 100% pass rate on the benchmark suite.

---

## 🛡️ Phase 3: Sandbox Isolation, Checkpoints & Rollback
*Objective: Zero-risk autonomous agent actions with granular undo and isolated git worktrees.*

- [x] **3.1 Granular Step Checkpoint & Undo (`/undo`)**
  - Created pre-modification file snapshots in `.sova/checkpoints/<session_id>/snapshots/`.
  - Added LIFO rollback support in `agent/checkpoints.py` handling both modified files (restores backup) and created files (deletes file).
  - Integrated checkpoint tracking into `write_file` and `edit_file` in `agent/tools.py`.
  - Added `/undo` and `/checkpoints` commands in Terminal CLI (`agent/cli.py`).
  - Added **[ ↺ Undo Edit ]** action button and `/api/undo` endpoint in Web UI (`agent/web.py`).
  - Implemented persistent chat history and full visual session replay in Web UI and rich turn recaps in Terminal CLI (`agent/sessions.py`, `agent/web.py`, `agent/cli.py`).
  - Eliminated Windows `[WinError 10053]` TCP abort tracebacks via `QuietThreadingHTTPServer` and graceful socket handlers.
- [x] **3.2 Git Worktree Sandboxing (`agent/sandbox.py`)**
  - Implemented `GitWorktreeSandbox` to run agent tasks in isolated git worktrees (`.sova/worktrees/<task_id>`).
  - Supported `has_changes()`, `get_diff()`, `apply_to_main()` (squash merge), and `discard()` (clean worktree and branch removal).
  - Verified with comprehensive test suite (`test_checkpoints.py`, `test_sessions.py`, `test_sandbox.py` — 46/46 tests passing).

---

## 🤖 Phase 4: Sub-Agent Hierarchy & Teamwork Orchestration
*Objective: Multi-agent coordination with specialized roles and clear visualization.*

- [x] **4.1 Role-Specialized Sub-Agents (`agent/subagents.py`)**
  - **Researcher**: Strictly read-only codebase exploration, symbol lookups, zero risk of unwanted edits or shell execution. Iteration budget: 8.
  - **Coder**: Precision editing, new file creation, and local test execution protected by CheckpointManager snapshots. Iteration budget: 10.
  - **Reviewer**: Inspects diffs, runs test suites, verifies correctness with zero code edit permissions. Iteration budget: 6.
  - **General**: Multi-purpose sub-agent fallback. Iteration budget: 8.
  - Enforced Max Depth = 1 recursion restriction (sub-agents cannot recursively spawn child sub-agents).
- [x] **4.2 Visual Sub-Agent Tree (CLI & Web UI)**
  - **Terminal CLI**: Rich branch tree rendering (`├──` / `└──`) with distinct role badges (`[bold cyan]🔬 Researcher[/bold cyan]`, `[bold green]⚡ Coder[/bold green]`, `[bold magenta]🔍 Reviewer[/bold magenta]`), indented execution steps, and completion markers.
  - **Web UI**: Collapsible `.subagent-card` elements in the chat feed with role icons, task titles, status badges (`Running` / `Completed` / `Incomplete`), and nested tool calls/results.
  - **Web Drawer**: Dedicated **Sub-Agents** tab in the Right Drawer rendering the live execution hierarchy, status history, and summary cards.
  - Verified with comprehensive test suite (`test_subagents.py` — 7/7 tests passing; full suite 53/53 tests passing).
