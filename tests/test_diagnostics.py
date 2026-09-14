"""Unit tests for agent.diagnostics syntax validation and self-healing loop hooks."""
import os
import shutil
import tempfile
import unittest

from agent.diagnostics import (
    DiagnosticError,
    check_bracket_balance,
    check_json_syntax,
    check_python_syntax,
    format_diagnostic_feedback,
    run_fast_diagnostics,
)
from agent.tools import build_tools


class TestDiagnostics(unittest.TestCase):
    def test_python_valid_syntax(self):
        valid_code = "def add(a, b):\n    return a + b\n"
        diag = check_python_syntax("calc.py", valid_code)
        self.assertIsNone(diag)

    def test_python_syntax_error(self):
        bad_code = "def broken(a, b\n    return a + b\n"
        diag = check_python_syntax("calc.py", bad_code)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.line, 1)
        self.assertEqual(diag.error_type, "SyntaxError")
        self.assertIn("calc.py", str(diag))

    def test_json_valid_syntax(self):
        valid_json = '{"name": "sova", "version": 2, "features": ["bm25", "sandbox"]}'
        diag = check_json_syntax("config.json", valid_json)
        self.assertIsNone(diag)

    def test_json_invalid_syntax(self):
        bad_json = '{"name": "sova", "version": 2, trailing: }'
        diag = check_json_syntax("config.json", bad_json)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.error_type, "JSONDecodeError")

    def test_bracket_balance_valid(self):
        js_code = """
        function calculateTotal(items) {
            return items.map(x => {
                return (x.price * (1 + x.tax));
            });
        }
        """
        diag = check_bracket_balance("app.js", js_code)
        self.assertIsNone(diag)

    def test_bracket_balance_unclosed(self):
        js_code = """
        function calculateTotal(items) {
            return items.map(x => {
                return (x.price * (1 + x.tax);
            });
        }
        """
        diag = check_bracket_balance("app.js", js_code)
        self.assertIsNotNone(diag)
        self.assertIn("Mismatched bracket", diag.message)

    def test_bracket_balance_ignoring_strings_and_comments(self):
        js_code = """
        // Here is a comment with an unclosed ( parenthesis
        /* and a block comment with { brace */
        const msg = "string with unmatched ( [ { characters";
        const template = `template with { unclosed`;
        function ok() { return true; }
        """
        diag = check_bracket_balance("app.js", js_code)
        self.assertIsNone(diag)

    def test_run_fast_diagnostics_dispatch(self):
        # Python
        self.assertIsNotNone(run_fast_diagnostics("test.py", "def missing_colon()\n    pass"))
        self.assertIsNone(run_fast_diagnostics("test.py", "def ok():\n    pass"))
        # JSON
        self.assertIsNotNone(run_fast_diagnostics("data.json", "{bad json}"))
        self.assertIsNone(run_fast_diagnostics("data.json", '{"ok": true}'))
        # Other extension ignored
        self.assertIsNone(run_fast_diagnostics("notes.txt", "Some text (with unclosed bracket"))

    def test_format_diagnostic_feedback(self):
        err = DiagnosticError(
            path="mod.py", line=10, column=5, message="invalid syntax",
            error_type="SyntaxError", snippet="print('missing endquote"
        )
        msg = format_diagnostic_feedback(err)
        self.assertIn("[WARNING] SYNTAX/LINT ERROR DETECTED", msg)
        self.assertIn("mod.py:10:5", msg)
        self.assertIn("SyntaxError", msg)
        self.assertIn("auto-repair", msg)


class TestDiagnosticsToolIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        _schemas, self.impls = build_tools(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_write_file_reports_syntax_error(self):
        msg, diff = self.impls["write_file"]("bad.py", "def broken(\n    return 1\n")
        self.assertIn("[WARNING] SYNTAX/LINT ERROR DETECTED", msg)
        self.assertIn("SyntaxError", msg)

    def test_edit_file_reports_syntax_error(self):
        self.impls["write_file"]("good.py", "def fine():\n    return 1\n")
        msg, diff = self.impls["edit_file"]("good.py", "def fine():", "def broken(")
        self.assertIn("[WARNING] SYNTAX/LINT ERROR DETECTED", msg)


if __name__ == "__main__":
    unittest.main()
