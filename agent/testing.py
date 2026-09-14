"""Automated TDD test runner and reproduction harness for SOVA."""
import os
import re
import shutil
import subprocess
import sys
from typing import List, Optional, Tuple


def detect_test_runner(root_dir: str, target: Optional[str] = None) -> Tuple[str, str]:
    """Detect available test runners and construct standard command line.

    Returns: (runner_name, command_string)
    """
    entries = set(os.listdir(root_dir)) if os.path.exists(root_dir) else set()

    # 1. Python ecosystem
    has_py_tests = (
        any(f.startswith("test_") and f.endswith(".py") for f in entries)
        or os.path.exists(os.path.join(root_dir, "tests"))
        or "pytest.ini" in entries
        or "setup.cfg" in entries
    )
    if has_py_tests or any(f in entries for f in ("requirements.txt", "pyproject.toml", "setup.py")):
        python_bin = sys.executable or "python"
        has_pytest = False
        try:
            import importlib.util
            has_pytest = importlib.util.find_spec("pytest") is not None
        except Exception:
            has_pytest = False

        if has_pytest:
            if target:
                target_path = target
                if not os.path.exists(os.path.join(root_dir, target)) and os.path.exists(os.path.join(root_dir, "tests", target)):
                    target_path = os.path.join("tests", target).replace("\\", "/")
                return "pytest", f'"{python_bin}" -m pytest {target_path} -v'
            return "pytest", f'"{python_bin}" -m pytest -v'
        else:
            if target:
                target_path = target
                if not os.path.exists(os.path.join(root_dir, target)) and os.path.exists(os.path.join(root_dir, "tests", target)):
                    target_path = os.path.join("tests", target).replace("\\", "/")
                clean_target = target_path.replace("\\", "/").rstrip(".py")
                if os.path.exists(os.path.join(root_dir, target_path)):
                    return "unittest", f'"{python_bin}" -m unittest {target_path}'
                return "unittest", f'"{python_bin}" -m unittest {clean_target}'
            tests_dir = "tests" if os.path.isdir(os.path.join(root_dir, "tests")) else "."
            return "unittest", f'"{python_bin}" -m unittest discover -s {tests_dir} -p "test_*.py"'

    # 2. JavaScript / TypeScript (Node.js)
    if "package.json" in entries:
        if target:
            return "npm test", f"npm test -- {target}"
        return "npm test", "npm test"

    # 3. Rust (Cargo)
    if "Cargo.toml" in entries:
        if target:
            return "cargo test", f"cargo test {target}"
        return "cargo test", "cargo test"

    # 4. Go
    if "go.mod" in entries:
        if target:
            return "go test", f"go test {target}"
        return "go test", "go test ./..."

    # Default fallback to python unittest
    python_bin = sys.executable or "python"
    tests_dir = "tests" if os.path.isdir(os.path.join(root_dir, "tests")) else "."
    return "unittest", f'"{python_bin}" -m unittest discover -s {tests_dir} -p "test_*.py"'


def parse_test_summary(output: str, exit_code: int) -> Tuple[str, List[str]]:
    """Extract human-readable pass/fail counts and failure reasons from test output."""
    failures: List[str] = []

    # pytest patterns
    # e.g., FAILED test_subagents.py::TestSubagents::test_role_configs
    pytest_raw = re.findall(r"^FAILED\s+(.*?)(?:$|\s+-)", output, re.MULTILINE)
    pytest_fails = [f.strip() for f in pytest_raw if f.strip() and not f.strip().startswith("(")]
    if pytest_fails:
        failures.extend(pytest_fails[:10])

    # unittest patterns
    # e.g., FAIL: test_invalid_syntax (test_diagnostics.TestSyntax)
    # e.g., ERROR: test_missing (test_diagnostics.TestSyntax)
    unittest_fails = re.findall(r"^(?:FAIL|ERROR):\s+(.*?)(?:\s+\(.*?\))?$", output, re.MULTILINE)
    if unittest_fails:
        failures.extend(unittest_fails[:10])

    # Summary line pattern
    # pytest: "=== 1 failed, 10 passed in 0.12s ==="
    py_summary = re.search(r"={3,}\s*(.*?in\s+[\d\.]+s.*?)\s*={3,}", output)
    if py_summary:
        summary = py_summary.group(1).strip()
    else:
        # unittest: "Ran 12 tests in 0.05s\n\nFAILED (failures=1)"
        unit_summary = re.search(r"(Ran \d+ tests? in [\d\.]+s.*?)(?:OK|FAILED.*)", output, re.DOTALL)
        if unit_summary:
            summary = " ".join(unit_summary.group(0).splitlines()[:2]).strip()
        else:
            summary = "Passed" if exit_code == 0 else f"Failed with exit code {exit_code}"

    return summary, failures


def run_tests(
    root_dir: str,
    target: Optional[str] = None,
    command: Optional[str] = None,
    timeout: int = 60,
) -> str:
    """Execute test suite or target test script, parsing concise diagnostics for the agent."""
    root_dir = os.path.abspath(root_dir)
    if command:
        runner_name = "custom"
        cmd_str = command
    else:
        runner_name, cmd_str = detect_test_runner(root_dir, target=target)

    try:
        proc = subprocess.run(
            cmd_str,
            shell=True,
            cwd=root_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        combined_output = (proc.stdout + "\n" + proc.stderr).strip()
        summary, failures = parse_test_summary(combined_output, proc.returncode)

        status = "PASSED [OK]" if proc.returncode == 0 else "FAILED [FAIL]"
        lines = [
            f"=== TEST RUN: {status} ===",
            f"Command: {cmd_str}",
            f"Summary: {summary}",
        ]

        if failures:
            lines.append("\nFailing Test Cases:")
            for f in failures:
                lines.append(f"  - {f}")

        # Keep diagnostic output concise to preserve token limits
        if len(combined_output) > 2000:
            sample = combined_output[-1800:]
            lines.append(f"\n--- Output (tail) ---\n... [earlier lines omitted]\n{sample}")
        else:
            lines.append(f"\n--- Output ---\n{combined_output}")

        return "\n".join(lines)
    except subprocess.TimeoutExpired:
        return f"ERROR: Test execution timed out after {timeout}s (Command: {cmd_str})"
    except Exception as exc:
        return f"ERROR: Failed to run tests: {exc}"
