# SOVA: Autonomous Terminal & Web Coding Agent Harness

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Interface](https://img.shields.io/badge/UI-CLI%20%26%20Web-purple.svg)](#interfaces)
[![Providers](https://img.shields.io/badge/Providers-Groq%20%7C%20Ollama%20%7C%20Nvidia%20%7C%20OpenAI-orange.svg)](#multi-provider-support)

**SOVA** is an autonomous coding agent harness designed for software engineering benchmarks (SWE-bench style) and everyday development tasks. It delivers a **Claude Code-grade terminal experience** alongside a modern **Web Console** featuring **VS Code / GitHub Copilot-style diff viewing**, multi-provider resilience, and comprehensive observability.

---

## Key Features

### 1. Claude Code-Grade Terminal Interface (`sova`)
- **Rich Syntax & Diff Previews**: Line-numbered code previews for `write_file`, colorized unified diff previews for `edit_file`, and rendered Markdown responses.
- **Interactive Decision Prompts**: Permission cards for sensitive tools (`write_file`, `edit_file`, `run_shell`) with quick selection:
  - `[y] Yes, allow once`
  - `[a] Always allow sensitive tools for this session`
  - `[n] No, deny this tool call`
  - `[c] Cancel task execution`
- **Destructive Overwrite Guards**: Prominent warnings with line count differences and full diffs whenever `write_file` targets an existing file.
- **Interactive Slash Commands**: `/provider`, `/model`, `/resume`, `/logs`, `/clear`, and `/help`.
- **Cross-Platform**: Fully compatible with Windows PowerShell (ASCII-safe encoding fallbacks) and POSIX shells.

### 2. Web Control Console (`sova-web`)
- **VS Code / GitHub Copilot Diff Viewer**:
  - **Inline Mode**: Dual line-number gutters (Old Line # and New Line #), change symbols (`+`/`-`), and soft green/red row highlights.
  - **Split (Side-by-Side) Mode**: Synchronized panes (Original vs Modified) with diagonal stripe alignment placeholders.
  - **Header Statistics**: Live additions & deletions badge (`+X -Y`), clipboard copy with instant feedback (`✓ Copied!`), and collapsible diff blocks.
  - **Approval Cards**: Direct diff embedding inside permission approval requests with <kbd>Y</kbd> / <kbd>A</kbd> / <kbd>N</kbd> hotkeys.
- **Dynamic Provider & Model Switching**: Real-time dropdowns auto-populated from installed Ollama models or remote provider catalogs.
- **Session Sidebar**: Browse past sessions with timestamps, completion status, and single-click resumption.
- **Right Inspector Drawer**: Live Todo checklist, touched files registry, and real-time streaming logs.

### 3. Resilient Agent Loop
- **Adaptive Token Budgeting**: Model-specific budgets (e.g. 5,500 token budget for Groq `openai/gpt-oss-120b` preventing HTTP 413 8,000 TPM limit errors).
- **Proactive Context Compaction**: Summarizes older conversation turns before hitting API rate limits while strictly preserving tool-call / tool-result message pairings.
- **Emergency 413 / TPM Intercept**: Auto-catches rate limits, aggressively prunes context, and retries under quota.
- **Malformed JSON Recovery**: Catches API-level JSON parse failures (`Failed to parse tool call arguments as JSON`) and retries with explicit escaping guidance.
- **Text-Based Tool Call Parser**: Automatically detects and executes tool calls when smaller open-source models (e.g. Llama 3 on Ollama) output JSON into `message.content` instead of the API tool-calls channel.

### 4. Observability & Trajectory Logging
- **Rotating System Log**: Central log in `.sova/logs/sova.log` (10MB max, 5 backups).
- **Structured Trajectories**: Every session records full step-by-step JSONL events in `.sova/logs/<session_id>.trajectory.jsonl` for auditing, evaluation replay, and telemetry.
- **Persistent Memory**: Project-level conventions and test commands persist across runs in `.sova/memory.md`.

---

## Installation & Setup

```powershell
# 1. Clone repository and set up virtual environment
git clone https://github.com/SiddheshDhomse/SOVA-TERMINAL-CODING-AGENT.git
cd "SOVA- Terminal Agent"
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Install dependencies & CLI entrypoints
pip install -r requirements.txt
pip install -e .

# 3. Configure environment variables
copy .env.example .env
# Edit .env:
# SOVA_PROVIDER=groq
# GROQ_API_KEY=your_free_groq_api_key_here
```

---

## Usage

### Terminal CLI (`sova`)
```powershell
# Launch default interactive session
sova

# Launch with specific provider & model
sova --provider ollama --model llama3.1:8b
sova --provider groq --model llama-3.3-70b-versatile
```

### Web Console (`sova-web`)
```powershell
# Launch Web Console (opens at http://127.0.0.1:8787)
sova-web
# Or specify host and port
python -m agent.web --port 8787
```

---

## Tools Available to the Agent

| Tool | Purpose |
|------|---------|
| `read_file` | Read file contents with line numbers (capped to conserve token budget). |
| `write_file` | Create a new file or completely rewrite an existing file (with syntax checks). |
| `edit_file` | Precision line-bounded replacement of `old_str` with `new_str` using optional `start_line` / `end_line`. |
| `list_dir` | List files and directories within the project workspace. |
| `find_files` | Fast glob pattern matching across workspace files. |
| `grep` | Regex / string search within files using `ripgrep` (fallback to Python). |
| `run_shell` | Run shell commands (foreground or tracked background dev servers). |
| `shell_output` | Poll logs from active background shell jobs. |
| `todo_write` / `todo_read` | Maintain and track multi-step task checklists. |
| `memory_append` / `memory_read` | Read and update durable project memory in `.sova/memory.md`. |
| `spawn_subagent` | Proactively delegate independent subtasks to a fresh sub-agent loop. |
| `finish` | Confirm task completion with a summary of touched files and changes. |

---

## Evaluations & Benchmarking

### 1. Local Toy Benchmark (No Docker required)
```powershell
python -m eval.toy_benchmark.run_toy_eval
```
Runs synthetic bug-fix and implementation challenges in isolated temp directories and validates task completion via shell assertions.

### 2. SWE-bench Lite Runner
```powershell
# Generate prediction patch files for SWE-bench Lite instances
python -m eval.swebench_runner --instance-ids sympy__sympy-20590 astropy__astropy-14539 --output predictions.jsonl

# Free remote evaluation via sb-cli (no local Docker required):
sb-cli submit swe-bench_lite test --predictions_path predictions.jsonl --run_id my_run
sb-cli get-report swe-bench_lite test my_run -o ./reports
```

### 3. Unit Test Suite
```powershell
# Run all tests using unittest
python -m unittest discover -s tests -p "test_*.py"

# Or using pytest (auto-configured in pyproject.toml)
pytest
```

---

## Architecture & Conventions

For a comprehensive technical specification of module contracts, token compaction algorithms, and guidelines for AI coding agents, refer to [`CONTEXT.md`](CONTEXT.md).
