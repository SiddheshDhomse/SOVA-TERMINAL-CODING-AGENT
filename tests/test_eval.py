"""Unit tests for agent.eval benchmark runner, instance parsing, and scorecard generation."""
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent.eval import (
    BenchmarkInstance,
    BenchmarkResult,
    BenchmarkSuiteResult,
    format_scorecard,
    run_benchmark_suite,
    run_instance,
)


class TestBenchmarkEvaluation(unittest.TestCase):
    def test_benchmark_instance_from_dict(self):
        data = {
            "instance_id": "test__issue-42",
            "problem_statement": "Fix the division by zero in divide()",
            "repo": "math/calc",
            "FAIL_TO_PASS": ["test_calc.py::test_div_zero"],
            "setup_files": {"calc.py": "def divide(a, b): return a / b\n"},
        }
        inst = BenchmarkInstance.from_dict(data)
        self.assertEqual(inst.instance_id, "test__issue-42")
        self.assertEqual(inst.problem_statement, "Fix the division by zero in divide()")
        self.assertEqual(inst.FAIL_TO_PASS, ["test_calc.py::test_div_zero"])
        self.assertIn("calc.py", inst.setup_files)

    def test_format_scorecard(self):
        suite = BenchmarkSuiteResult(
            total=2,
            resolved=1,
            resolution_rate=50.0,
            total_tokens=5000,
            total_cost_usd=0.0035,
            avg_turns=2.5,
            avg_seconds=3.2,
            results=[
                BenchmarkResult(
                    instance_id="inst-001",
                    resolved=True,
                    metrics={"total_tokens": 2000, "cost_usd": 0.0015},
                ),
                BenchmarkResult(
                    instance_id="inst-002",
                    resolved=False,
                    metrics={"total_tokens": 3000, "cost_usd": 0.0020},
                ),
            ],
        )
        card = format_scorecard(suite)
        self.assertIn("SOVA CODING BENCHMARK SCORECARD", card)
        self.assertIn("1 / 2 (50.0%)", card)
        self.assertIn("inst-001", card)
        self.assertIn("RESOLVED [OK]", card)
        self.assertIn("UNRESOLVED [FAIL]", card)

    def test_run_instance_with_mocked_agent(self):
        inst = BenchmarkInstance(
            instance_id="mock-inst-01",
            problem_statement="Implement square(n)",
            setup_files={"square.py": "def square(n):\n    return n * n\n"},
        )

        with patch("agent.eval.run_agent") as mock_agent, \
             patch("agent.eval.run_tests") as mock_tests:
            mock_agent.return_value = {
                "finished": True,
                "summary": "Implemented square",
                "metrics": {"total_tokens": 1500, "cost_usd": 0.001},
            }
            mock_tests.return_value = "=== TEST RUN: PASSED [OK] ===\nRan 1 test in 0.01s\nOK\n"

            # Create a mock patch by modifying file inside the instance run
            res = run_instance(inst)
            self.assertEqual(res.instance_id, "mock-inst-01")
            self.assertEqual(res.metrics["total_tokens"], 1500)


if __name__ == "__main__":
    unittest.main()
