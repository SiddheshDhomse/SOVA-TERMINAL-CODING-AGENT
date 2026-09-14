"""Generate SWE-bench Lite predictions.jsonl using the agent, without Docker.

Scoring is NOT done here - it happens remotely via `sb-cli` (see README.md), so this
script only needs the target repo checked out at base_commit for the agent to edit.
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile

from datasets import load_dataset
from dotenv import load_dotenv

from agent.loop import run_agent

DATASET_NAME = "princeton-nlp/SWE-bench_Lite"
MODEL_NAME_OR_PATH = "groq-llama3.3-70b-terminal-agent"


def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def checkout_repo(repo, base_commit, dest):
    """Check out `repo` (owner/name) at base_commit into dest, without a full clone if possible."""
    url = f"https://github.com/{repo}.git"
    _run(["git", "init"], cwd=dest)
    _run(["git", "remote", "add", "origin", url], cwd=dest)
    fetch = _run(["git", "fetch", "--depth", "1", "origin", base_commit], cwd=dest)
    if fetch.returncode == 0:
        _run(["git", "checkout", "FETCH_HEAD"], cwd=dest)
    else:
        # Fallback for hosts that reject fetching an arbitrary commit SHA directly.
        shutil.rmtree(dest)
        os.makedirs(dest)
        _run(["git", "clone", url, dest])
        checkout = _run(["git", "checkout", base_commit], cwd=dest)
        if checkout.returncode != 0:
            raise RuntimeError(f"Could not checkout {base_commit} for {repo}: {checkout.stderr}")

    # Exclude SOVA's internal log directory from git tracking
    exclude_file = os.path.join(dest, ".git", "info", "exclude")
    if os.path.exists(os.path.dirname(exclude_file)):
        with open(exclude_file, "a", encoding="utf-8") as f:
            f.write("\n.sova\n.sova/**\n")


def get_diff(dest):
    _run(["git", "reset", ".sova"], cwd=dest)
    _run(["git", "add", "-A", "--", ":!.sova", ":!.sova/**"], cwd=dest)
    return _run(["git", "diff", "--cached", "--", ":!.sova", ":!.sova/**"], cwd=dest).stdout


def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-ids", nargs="+", required=True, help="SWE-bench Lite instance_ids to run")
    parser.add_argument("--split", default="test")
    parser.add_argument("--output", default="predictions.jsonl")
    parser.add_argument("--max-iterations", type=int, default=20)
    parser.add_argument("--model", default=None, help="LLM model override")
    args = parser.parse_args()

    dataset = load_dataset(DATASET_NAME, split=args.split)
    wanted = set(args.instance_ids)
    instances = [row for row in dataset if row["instance_id"] in wanted]
    missing = wanted - {row["instance_id"] for row in instances}
    if missing:
        raise SystemExit(f"Instance ids not found in {DATASET_NAME}/{args.split}: {sorted(missing)}")

    active_model = args.model or os.getenv("SOVA_MODEL") or MODEL_NAME_OR_PATH
    predictions = []
    for instance in instances:
        instance_id = instance["instance_id"]
        print(f"=== {instance_id} ===")
        dest = tempfile.mkdtemp(prefix="swebench_")
        try:
            checkout_repo(instance["repo"], instance["base_commit"], dest)
            task_prompt = (
                f"You are working in the cloned repository for {instance['repo']}.\n"
                f"Resolve the following issue by inspecting and modifying the repository code:\n\n"
                f"{instance['problem_statement']}\n\n"
                f"Use tools (grep, find_files, read_file, edit_file) to locate the relevant files, "
                f"apply the required bug fix, and call finish when done."
            )
            run_agent(
                dest,
                task_prompt,
                model=args.model,
                max_iterations=args.max_iterations,
                session_id=f"swebench_{instance_id}",
                force_task=True,
                verbose=True,
            )
            patch = get_diff(dest)
            print(f"Generated patch for {instance_id}: {len(patch.splitlines())} diff lines")
        finally:
            try:
                from agent.logger import close_logger
                close_logger()
            except Exception:
                pass
            shutil.rmtree(dest, ignore_errors=True)

        predictions.append({
            "instance_id": instance_id,
            "model_name_or_path": active_model,
            "model_patch": patch,
        })

    with open(args.output, "w", encoding="utf-8") as f:
        for pred in predictions:
            f.write(json.dumps(pred) + "\n")
    print(f"Wrote {len(predictions)} predictions to {args.output}")


if __name__ == "__main__":
    main()
