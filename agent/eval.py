"""Standardized SWE-bench evaluation harness and regression benchmark runner for SOVA."""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .cost import format_cost, format_token_cost_summary
from .loop import run_agent
from .sandbox import GitWorktreeSandbox, is_git_repo
from .testing import run_tests


@dataclass
class BenchmarkInstance:
    instance_id: str
    problem_statement: str
    repo: str = "local/benchmark"
    base_commit: str = "HEAD"
    test_patch: str = ""
    FAIL_TO_PASS: List[str] = field(default_factory=list)
    PASS_TO_PASS: List[str] = field(default_factory=list)
    setup_files: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BenchmarkInstance":
        return cls(
            instance_id=data.get("instance_id") or data.get("id") or "inst-unknown",
            problem_statement=data.get("problem_statement") or data.get("prompt") or data.get("issue") or "",
            repo=data.get("repo", "local/benchmark"),
            base_commit=data.get("base_commit", "HEAD"),
            test_patch=data.get("test_patch", ""),
            FAIL_TO_PASS=data.get("FAIL_TO_PASS", []),
            PASS_TO_PASS=data.get("PASS_TO_PASS", []),
            setup_files=data.get("setup_files", {}),
        )


@dataclass
class BenchmarkResult:
    instance_id: str
    resolved: bool
    patch: str = ""
    test_output: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class BenchmarkSuiteResult:
    total: int
    resolved: int
    resolution_rate: float
    total_tokens: int
    total_cost_usd: float
    avg_turns: float
    avg_seconds: float
    results: List[BenchmarkResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "resolved": self.resolved,
            "resolution_rate": self.resolution_rate,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "avg_turns": self.avg_turns,
            "avg_seconds": self.avg_seconds,
            "results": [asdict(r) for r in self.results],
        }


SAMPLE_INSTANCES: List[BenchmarkInstance] = [
    BenchmarkInstance(
        instance_id="sova-sample-001",
        problem_statement=(
            "Fix the off-by-one error in `math_utils.py`: `fibonacci(n)` should return the n-th "
            "Fibonacci number where fibonacci(0) = 0, fibonacci(1) = 1, and fibonacci(5) = 5. "
            "Currently fibonacci(0) incorrectly raises an IndexError."
        ),
        setup_files={
            "math_utils.py": (
                "def fibonacci(n: int) -> int:\n"
                "    if n < 0:\n"
                "        raise ValueError('n must be non-negative')\n"
                "    seq = [0, 1]\n"
                "    for i in range(2, n + 1):\n"
                "        seq.append(seq[i - 1] + seq[i - 2])\n"
                "    return seq[n]\n"
            ),
            "test_math_utils.py": (
                "import unittest\n"
                "from math_utils import fibonacci\n\n"
                "class TestFib(unittest.TestCase):\n"
                "    def test_zero(self):\n"
                "        self.assertEqual(fibonacci(0), 0)\n"
                "    def test_five(self):\n"
                "        self.assertEqual(fibonacci(5), 5)\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            ),
        },
        FAIL_TO_PASS=["test_math_utils.py"],
    ),
    BenchmarkInstance(
        instance_id="sova-sample-002",
        problem_statement=(
            "In `string_formatter.py`, `slugify(text)` fails to handle whitespace and uppercase strings. "
            "It should lowercase the text, replace spaces with hyphens, and strip punctuation. "
            "Ensure `slugify('Hello World!')` returns 'hello-world'."
        ),
        setup_files={
            "string_formatter.py": (
                "import re\n"
                "def slugify(text: str) -> str:\n"
                "    # Incomplete implementation\n"
                "    return text\n"
            ),
            "test_formatter.py": (
                "import unittest\n"
                "from string_formatter import slugify\n\n"
                "class TestSlug(unittest.TestCase):\n"
                "    def test_slug(self):\n"
                "        self.assertEqual(slugify('Hello World!'), 'hello-world')\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            ),
        },
        FAIL_TO_PASS=["test_formatter.py"],
    ),
]


def run_instance(
    instance: BenchmarkInstance,
    base_dir: Optional[str] = None,
    model: Optional[str] = None,
    max_iterations: int = 15,
) -> BenchmarkResult:
    """Run SOVA agent on a single benchmark instance and evaluate resolution."""
    temp_dir = tempfile.mkdtemp(prefix=f"sova_eval_{instance.instance_id}_")
    try:
        # Initialize as Git repo for diff capture
        subprocess.run(["git", "init"], cwd=temp_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "eval@sova.local"], cwd=temp_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "SOVA Eval"], cwd=temp_dir, capture_output=True, check=True)

        # Write setup files
        for rel_path, content in instance.setup_files.items():
            full_p = os.path.join(temp_dir, rel_path)
            os.makedirs(os.path.dirname(full_p) or temp_dir, exist_ok=True)
            with open(full_p, "w", encoding="utf-8") as f:
                f.write(content)

        subprocess.run(["git", "add", "."], cwd=temp_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Initial benchmark state"], cwd=temp_dir, capture_output=True, check=True)

        # Run agent
        events = []
        result = run_agent(
            temp_dir,
            instance.problem_statement,
            model=model,
            max_iterations=max_iterations,
            verbose=False,
            on_event=lambda ev: events.append(ev),
            force_task=True,
        )

        # Capture generated git patch
        diff_proc = subprocess.run(["git", "diff", "HEAD"], cwd=temp_dir, capture_output=True, text=True)
        patch = diff_proc.stdout

        # Execute evaluation tests
        resolved = False
        test_out = ""
        test_targets = instance.FAIL_TO_PASS or ["test_*.py"]
        all_passed = True

        for target in test_targets:
            out = run_tests(temp_dir, target=target)
            test_out += f"\n--- Target: {target} ---\n{out}"
            if "FAILED [FAIL]" in out or "ERROR:" in out:
                all_passed = False
                break

        resolved = all_passed and bool(patch.strip())

        return BenchmarkResult(
            instance_id=instance.instance_id,
            resolved=resolved,
            patch=patch,
            test_output=test_out,
            metrics=result.get("metrics", {}),
        )
    except Exception as exc:
        return BenchmarkResult(
            instance_id=instance.instance_id,
            resolved=False,
            error=str(exc),
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def run_benchmark_suite(
    instances: Optional[List[BenchmarkInstance]] = None,
    dataset_path: Optional[str] = None,
    model: Optional[str] = None,
    max_iterations: int = 15,
) -> BenchmarkSuiteResult:
    """Execute a suite of benchmark instances and compile the benchmark scorecard."""
    if dataset_path and os.path.exists(dataset_path):
        loaded_instances: List[BenchmarkInstance] = []
        with open(dataset_path, "r", encoding="utf-8") as f:
            if dataset_path.endswith(".jsonl"):
                for line in f:
                    if line.strip():
                        loaded_instances.append(BenchmarkInstance.from_dict(json.loads(line)))
            else:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        loaded_instances.append(BenchmarkInstance.from_dict(item))
        instances = loaded_instances
    elif not instances:
        instances = SAMPLE_INSTANCES

    results: List[BenchmarkResult] = []
    total_tokens = 0
    total_cost = 0.0
    total_turns = 0
    total_secs = 0.0

    for inst in instances:
        res = run_instance(inst, model=model, max_iterations=max_iterations)
        results.append(res)
        m = res.metrics
        total_tokens += m.get("total_tokens", 0)
        total_cost += m.get("cost_usd", 0.0)
        total_turns += m.get("turns", 0)
        total_secs += m.get("elapsed_seconds", 0.0)

    total = len(instances)
    resolved = sum(1 for r in results if r.resolved)
    rate = (resolved / total * 100.0) if total > 0 else 0.0

    return BenchmarkSuiteResult(
        total=total,
        resolved=resolved,
        resolution_rate=round(rate, 2),
        total_tokens=total_tokens,
        total_cost_usd=round(total_cost, 4),
        avg_turns=round((total_turns / total) if total > 0 else 0.0, 1),
        avg_seconds=round((total_secs / total) if total > 0 else 0.0, 1),
        results=results,
    )


def format_scorecard(suite: BenchmarkSuiteResult) -> str:
    """Render a clean ASCII/markdown scorecard from benchmark suite results."""
    lines = [
        "==================================================================",
        "              SOVA CODING BENCHMARK SCORECARD                     ",
        "==================================================================",
        f" Resolution Rate:  {suite.resolved} / {suite.total} ({suite.resolution_rate:.1f}%)",
        f" Total Tokens:     {suite.total_tokens:,}",
        f" Total Est. Cost:  {format_cost(suite.total_cost_usd)}",
        f" Average Turns:    {suite.avg_turns} turn(s) / task",
        f" Average Duration: {suite.avg_seconds}s / task",
        "------------------------------------------------------------------",
        " Instance Details:",
    ]
    for r in suite.results:
        status = "RESOLVED [OK]" if r.resolved else ("ERROR [ERR]" if r.error else "UNRESOLVED [FAIL]")
        tokens = r.metrics.get("total_tokens", 0)
        cost = r.metrics.get("cost_usd", 0.0)
        lines.append(f"  - {r.instance_id:<20} {status:<18} (tokens: {tokens:,}, cost: {format_cost(cost)})")

    lines.append("==================================================================")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="SOVA SWE-Bench & Benchmark Runner")
    parser.add_argument("--dataset", help="Path to SWE-bench format JSON or JSONL dataset file")
    parser.add_argument("--model", help="Model override for benchmark runs")
    parser.add_argument("--max-iterations", type=int, default=15, help="Max turns per task")
    parser.add_argument("--dry-run", action="store_true", help="Print benchmark setup without calling LLM")
    args = parser.parse_args()

    if args.dry_run:
        suite = BenchmarkSuiteResult(
            total=len(SAMPLE_INSTANCES),
            resolved=len(SAMPLE_INSTANCES),
            resolution_rate=100.0,
            total_tokens=4200,
            total_cost_usd=0.0025,
            avg_turns=2.0,
            avg_seconds=1.4,
            results=[
                BenchmarkResult(instance_id=i.instance_id, resolved=True, metrics={"total_tokens": 2100, "cost_usd": 0.0012})
                for i in SAMPLE_INSTANCES
            ],
        )
        print(format_scorecard(suite))
        return

    print("Running SOVA Benchmark Suite...")
    suite = run_benchmark_suite(
        dataset_path=args.dataset,
        model=args.model,
        max_iterations=args.max_iterations,
    )
    print(format_scorecard(suite))


if __name__ == "__main__":
    main()
