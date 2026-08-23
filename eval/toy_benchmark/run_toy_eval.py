"""Run the agent against the toy benchmark tasks and report pass/fail, no Docker required."""
import os
import shutil
import subprocess
import tempfile

from dotenv import load_dotenv

from agent.loop import run_agent
from eval.toy_benchmark.tasks import TASKS


def run_one(task_def):
    root = tempfile.mkdtemp(prefix=f"toy_{task_def['id']}_")
    try:
        for rel_path, content in task_def["files"].items():
            full = os.path.join(root, rel_path)
            os.makedirs(os.path.dirname(full) or root, exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)

        run_agent(root, task_def["task"], verbose=False)

        result = subprocess.run(
            task_def["check_cmd"], shell=True, cwd=root, capture_output=True, text=True
        )
        passed = result.returncode == 0 and "OK" in result.stdout
        return passed, result.stdout + result.stderr
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    load_dotenv()
    for task_def in TASKS:
        passed, output = run_one(task_def)
        print(f"[{'PASS' if passed else 'FAIL'}] {task_def['id']}")
        if not passed:
            print(output.strip())


if __name__ == "__main__":
    main()
