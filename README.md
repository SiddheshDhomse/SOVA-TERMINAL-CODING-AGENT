# Sova: Terminal Coding Agent + Free SWE-bench Eval

A small terminal coding agent (multi-tool, Claude Code/Codex-style) that works with
Groq's free API or a local Ollama model, plus scripts to learn eval mechanics and
score the agent on SWE-bench Lite **without Docker** using the official `sb-cli`
remote evaluation service.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# edit .env: set SOVA_PROVIDER=groq and GROQ_API_KEY (free key from https://console.groq.com/keys)
# or set SOVA_PROVIDER=ollama and make sure `ollama serve` is running with a model pulled
```

To get a plain `sova` command instead of `python -m agent.cli`, install the project itself:

```powershell
pip install -e .
sova
```

## 1. Use the agent interactively

```powershell
sova
sova --provider ollama --model llama3.1:8b
```

Type a task (e.g. "create fizzbuzz.py that prints FizzBuzz 1-20"); the agent will
read/write files and run shell commands in your current directory until it calls
`finish`. Switch providers mid-session with `/provider groq` or `/provider ollama`,
and override the model with `/model <name>`.

### Tools available to the agent

`read_file`, `write_file`, `edit_file`, `list_dir`, `find_files` (glob search),
`grep`, `run_shell`, `memory_read`/`memory_append` (persistent project notes stored
in `.sova/memory.md`, shared across runs), `spawn_subagent` (delegate an independent
subtask to a fresh sub-agent loop), and `finish`.

## 2. Learn evals with the toy benchmark (no Docker, no SWE-bench needed)

```powershell
python -m eval.toy_benchmark.run_toy_eval
```

Runs the agent against a couple of handmade bugfix/implement tasks in isolated temp
folders and checks pass/fail via a plain shell assertion - use this to see how a
task -> patch -> check pipeline scores before touching SWE-bench.

## 3. Explore the real SWE-bench Lite dataset

```powershell
python -m eval.explore_swebench
```

Prints a few instances' `problem_statement`, `patch`, `FAIL_TO_PASS`/`PASS_TO_PASS`
fields so you can see exactly what gets scored.

## 4. Generate predictions for a small SWE-bench Lite sample

```powershell
python -m eval.swebench_runner --instance-ids sympy__sympy-20590 astropy__astropy-14539 --output predictions.jsonl
```

For each instance id this checks out the target repo at `base_commit` into a temp
dir, runs the agent on the issue's `problem_statement`, and records the resulting
`git diff` as the prediction. No Docker or repo dependencies are needed here -
that's only required for scoring, which happens remotely in step 5.

## 5. Score for free via sb-cli (no local Docker)

```powershell
sb-cli gen-api-key you@example.com
# verify the code emailed to you:
sb-cli verify-api-key <code_from_email>
$env:SWEBENCH_API_KEY = "<your_key>"

# check remaining free quota before submitting
sb-cli quota swe-bench_lite test

sb-cli submit swe-bench_lite test --predictions_path predictions.jsonl --run_id my_first_run
sb-cli get-report swe-bench_lite test my_first_run -o ./reports
```

Start with a handful of instance ids (as above) to conserve the free quota while
you iterate on the agent; scale up once it's working reliably.
