"""Unit tests for agent.testing test runner auto-detection, parsing, and execution."""
import os
import shutil
import tempfile
import unittest

from agent.testing import detect_test_runner, parse_test_summary, run_tests
from agent.tools import build_tools


class TestTestingHarness(unittest.TestCase):
    def test_detect_test_runner_python_tests(self):
        temp_dir = tempfile.mkdtemp()
        try:
            test_file = os.path.join(temp_dir, "test_example.py")
            with open(test_file, "w") as f:
                f.write("import unittest\n")

            runner, cmd = detect_test_runner(temp_dir)
            self.assertIn(runner, ("unittest", "pytest"))
            self.assertTrue("unittest" in cmd or "pytest" in cmd)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_detect_test_runner_nodejs(self):
        temp_dir = tempfile.mkdtemp()
        try:
            pkg_json = os.path.join(temp_dir, "package.json")
            with open(pkg_json, "w") as f:
                f.write('{"name": "demo", "scripts": {"test": "jest"}}\n')

            runner, cmd = detect_test_runner(temp_dir)
            self.assertEqual(runner, "npm test")
            self.assertEqual(cmd, "npm test")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_parse_test_summary_success(self):
        output = "Ran 5 tests in 0.123s\n\nOK\n"
        summary, failures = parse_test_summary(output, exit_code=0)
        self.assertIn("Ran 5 tests", summary)
        self.assertEqual(len(failures), 0)

    def test_parse_test_summary_failure(self):
        output = (
            "FAIL: test_addition (test_calc.TestCalc)\n"
            "AssertionError: 2 != 3\n"
            "Ran 4 tests in 0.05s\n\nFAILED (failures=1)\n"
        )
        summary, failures = parse_test_summary(output, exit_code=1)
        self.assertIn("test_addition", failures[0])

    def test_run_tests_execution(self):
        temp_dir = tempfile.mkdtemp()
        try:
            # Create a simple test file that succeeds
            test_file = os.path.join(temp_dir, "test_mini.py")
            with open(test_file, "w") as f:
                f.write(
                    "import unittest\n"
                    "class TestMini(unittest.TestCase):\n"
                    "    def test_true(self):\n"
                    "        self.assertTrue(True)\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                )

            report = run_tests(temp_dir, target="test_mini.py")
            self.assertIn("PASSED [OK]", report)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_run_tests_tool_integration(self):
        temp_dir = tempfile.mkdtemp()
        try:
            _schemas, impls = build_tools(temp_dir)
            self.assertIn("run_tests", impls)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
