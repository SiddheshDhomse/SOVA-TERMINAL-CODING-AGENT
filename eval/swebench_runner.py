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
        return
    # Fallback for hosts that reject fetching an arbitrary commit SHA directly.
    shutil.rmtree(dest)
    os.makedirs(dest)
    _run(["git", "clone", url, dest])
    checkout = _run(["git", "checkout", base_commit], cwd=dest)
    if checkout.returncode != 0:
        raise RuntimeError(f"Could not checkout {base_commit} for {repo}: {checkout.stderr}")


def get_diff(dest):
    return _run(["git", "diff"], cwd=dest).stdout


def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-ids", nargs="+", required=True, help="SWE-bench Lite instance_ids to run")
    parser.add_argument("--split", default="test")
    parser.add_argument("--output", default="predictions.jsonl")
    parser.add_argument("--max-iterations", type=int, default=20)
    args = parser.parse_args()

    dataset = load_dataset(DATASET_NAME, split=args.split)
    wanted = set(args.instance_ids)
    instances = [row for row in dataset if row["instance_id"] in wanted]
    missing = wanted - {row["instance_id"] for row in instances}
    if missing:
        raise SystemExit(f"Instance ids not found in {DATASET_NAME}/{args.split}: {sorted(missing)}")

    predictions = []
    for instance in instances:
        instance_id = instance["instance_id"]
        print(f"=== {instance_id} ===")
        dest = tempfile.mkdtemp(prefix="swebench_")
        try:
            checkout_repo(instance["repo"], instance["base_commit"], dest)
            run_agent(dest, instance["problem_statement"], max_iterations=args.max_iterations)
            patch = get_diff(dest)
        finally:
            shutil.rmtree(dest, ignore_errors=True)

        predictions.append({
            "instance_id": instance_id,
            "model_name_or_path": MODEL_NAME_OR_PATH,
            "model_patch": patch,
        })

    with open(args.output, "w", encoding="utf-8") as f:
        for pred in predictions:
            f.write(json.dumps(pred) + "\n")
    print(f"Wrote {len(predictions)} predictions to {args.output}")


if __name__ == "__main__":
    main()
