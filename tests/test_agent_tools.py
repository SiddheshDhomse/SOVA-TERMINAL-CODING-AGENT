#!/usr/bin/env python
import os
import shutil
import tempfile
import unittest

from agent import sessions
from agent.tools import build_tools, unified_diff


class TestUnifiedDiff(unittest.TestCase):
    def test_no_change(self):
        self.assertEqual(unified_diff("a.py", "x\n", "x\n"), "")

    def test_add_and_remove_lines(self):
        diff = unified_diff("a.py", "one\ntwo\n", "one\nthree\n")
        self.assertIn("-two", diff)
        self.assertIn("+three", diff)
        self.assertIn("a.py", diff)


class TestToolsGrepFind(unittest.TestCase):
    """Exercises the os.walk fallback path (no rg installed in CI/dev by default)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self._schemas, self.impls = build_tools(self.root)
        self.impls["write_file"]("pkg/mod.py", "def hello():\n    return 'hi'\n")
        self.impls["write_file"]("notes.txt", "hello world\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_find_files_glob(self):
        result = self.impls["find_files"]("**/*.py")
        self.assertIn("pkg/mod.py", result.replace("\\", "/"))

    def test_grep_plain_text(self):
        result = self.impls["grep"]("hello")
        self.assertIn("notes.txt", result)

    def test_grep_no_matches(self):
        result = self.impls["grep"]("definitely-not-present-xyz")
        self.assertEqual(result, "No matches")


class TestWriteEditDiffs(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self._schemas, self.impls = build_tools(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_write_file_returns_diff(self):
        message, diff = self.impls["write_file"]("f.py", "print(1)\n")
        self.assertIn("Wrote", message)
        self.assertIn("+print(1)", diff)

    def test_edit_file_returns_diff(self):
        self.impls["write_file"]("f.py", "print(1)\n")
        message, diff = self.impls["edit_file"]("f.py", "print(1)", "print(2)")
        self.assertIn("Edited", message)
        self.assertIn("-print(1)", diff)
        self.assertIn("+print(2)", diff)


class TestTodoAndShellJobs(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self._schemas, self.impls = build_tools(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_todo_write_read_roundtrip(self):
        self.impls["todo_write"]([{"content": "step 1", "status": "pending"}])
        self.assertEqual(self.impls["todo_read"](), [{"content": "step 1", "status": "pending"}])

    def test_background_shell_job(self):
        msg = self.impls["run_shell"]("echo hi", background=True)
        self.assertIn("Started background job", msg)
        job_id = msg.split("job ")[1].split(".")[0]
        import time
        for _ in range(20):
            status = self.impls["shell_output"](job_id)
            if "exited" in status:
                break
            time.sleep(0.1)
        self.assertIn("hi", status)


class TestSessions(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_save_list_load_roundtrip(self):
        session_id = sessions.new_session_id()
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello there"}]
        sessions.save_session(self.root, session_id, messages, {"finished": True})

        rows = sessions.list_sessions(self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], session_id)
        self.assertTrue(rows[0]["finished"])
        self.assertIn("hello there", rows[0]["first_message"])

        loaded = sessions.load_session(self.root, session_id)
        self.assertEqual(loaded, messages)

    def test_list_sessions_empty(self):
        self.assertEqual(sessions.list_sessions(self.root), [])


class TestPrecisionEditAndLogger(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self._schemas, self.impls = build_tools(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_edit_file_line_bounded(self):
        code = "def f1():\n    return 1\n\ndef f2():\n    return 1\n"
        self.impls["write_file"]("m.py", code)

        # Replacing 'return 1' globally fails because it matches twice
        res = self.impls["edit_file"]("m.py", "return 1", "return 2")
        if isinstance(res, tuple):
            res = res[0]
        self.assertIn("matches 2 times", res)

        # Replacing with start_line and end_line targeting f2 succeeds
        res, diff = self.impls["edit_file"]("m.py", "return 1", "return 2", start_line=4, end_line=5)
        self.assertIn("Edited", res)
        self.assertIn("+    return 2", diff)

        content = self.impls["read_file"]("m.py")
        self.assertIn("def f1():\n2:     return 1", content)
        self.assertIn("def f2():\n5:     return 2", content)

    def test_edit_file_crlf_normalization(self):
        full = os.path.join(self.root, "crlf.py")
        with open(full, "wb") as f:
            f.write(b"line1\r\nline2\r\nline3\r\n")

        res, diff = self.impls["edit_file"]("crlf.py", "line2", "line_replaced")
        self.assertIn("Edited", res)
        self.assertIn("+line_replaced", diff)

    def test_session_trajectory_logger(self):
        from agent.logger import SessionTrajectoryLogger
        logger = SessionTrajectoryLogger(self.root, "test-sess-1")
        logger.record_step("tool_call", {"name": "write_file", "args": {"path": "a.txt"}})
        logger.record_step("tool_result", {"name": "write_file", "result": "Wrote 10 chars"})

        steps = logger.get_trajectory()
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["type"], "tool_call")
        self.assertEqual(steps[1]["type"], "tool_result")

    def test_write_file_normalizes_double_escaped_newlines(self):
        # LLMs occasionally pass literal \\n when intending multiline code
        escaped_code = "def foo():\\n    return 42\\n"
        self.impls["write_file"]("test_escaped.py", escaped_code)
        read_back = self.impls["read_file"]("test_escaped.py")
        self.assertIn("1: def foo():", read_back)
        self.assertIn("2:     return 42", read_back)

    def test_edit_file_normalizes_double_escaped_newlines(self):
        self.impls["write_file"]("target.py", "def a():\n    pass\n")
        res, diff = self.impls["edit_file"]("target.py", "pass", "x = 1\\n    return x")
        self.assertIn("Edited", res)
        read_back = self.impls["read_file"]("target.py")
        self.assertIn("x = 1", read_back)
        self.assertIn("return x", read_back)

    def test_edit_file_preserves_crlf_on_disk(self):
        full = os.path.join(self.root, "crlf_preserved.py")
        with open(full, "wb") as f:
            f.write(b"def foo():\r\n    return 1\r\n")

        res, diff = self.impls["edit_file"]("crlf_preserved.py", "return 1", "return 2")
        self.assertIn("Edited", res)
        with open(full, "rb") as f:
            raw_bytes = f.read()
        self.assertIn(b"\r\n", raw_bytes)
        self.assertEqual(raw_bytes, b"def foo():\r\n    return 2\r\n")

    def test_read_file_truncates_at_newline_boundary(self):
        # 100 lines of 60 chars is < 250 lines, but ~6,500 chars (> 5,000 char limit)
        line_body = "x" * 60
        long_content = (line_body + "\n") * 100
        self.impls["write_file"]("long_file.py", long_content)
        read_back = self.impls["read_file"]("long_file.py")
        self.assertIn("Truncated to stay within token limits", read_back)
        lines = read_back.splitlines()
        trunc_idx = [i for i, l in enumerate(lines) if "Truncated to stay within token limits" in l][0]
        prev_line = lines[trunc_idx - 1]
        self.assertTrue(prev_line.endswith(line_body))

    def test_parse_content_tool_calls_nested_json(self):
        from agent.loop import _parse_content_tool_calls
        content = (
            'Here is the plan:\n'
            '```json\n'
            '{\n'
            '  "name": "todo_write",\n'
            '  "parameters": {\n'
            '    "todos": [\n'
            '      {"content": "Inspect code", "status": "completed"},\n'
            '      {"content": "Fix bug", "status": "in_progress"}\n'
            '    ]\n'
            '  }\n'
            '}\n'
            '```'
        )
        calls = _parse_content_tool_calls(content, {"todo_write", "read_file"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].function.name, "todo_write")
        import json
        args = json.loads(calls[0].function.arguments)
        self.assertEqual(len(args["todos"]), 2)
        self.assertEqual(args["todos"][0]["status"], "completed")

    def test_read_file_empty_file(self):
        empty_path = os.path.join(self.root, "empty.txt")
        with open(empty_path, "w", encoding="utf-8") as f:
            pass
        out = self.impls["read_file"]("empty.txt")
        self.assertEqual(out, "(empty file)")


class TestFuzzyFileResolution(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self._schemas, self.impls = build_tools(self.root)
        self.impls["write_file"]("agent/tools.py", "def build_tools():\n    return 'tools'\n")
        self.impls["write_file"]("agent/sub/deep.py", "def deep_fn():\n    pass\n")
        self.impls["write_file"]("pkg_a/shared.py", "# pkg_a\n")
        self.impls["write_file"]("pkg_b/shared.py", "# pkg_b\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_read_file_auto_resolves_basename(self):
        res = self.impls["read_file"]("tools.py")
        self.assertIn("[Auto-resolved 'tools.py' -> 'agent/tools.py']", res)
        self.assertIn("def build_tools():", res)

    def test_read_file_exact_path_has_no_auto_resolve_prefix(self):
        res = self.impls["read_file"]("agent/tools.py")
        self.assertNotIn("Auto-resolved", res)
        self.assertIn("def build_tools():", res)

    def test_read_file_nonexistent_returns_clean_error(self):
        res = self.impls["read_file"]("nonexistent.py")
        self.assertIn("ERROR: file 'nonexistent.py' does not exist in workspace.", res)

    def test_read_file_ambiguous_candidates(self):
        res = self.impls["read_file"]("shared.py")
        self.assertIn("ERROR: file 'shared.py' not found. Did you mean one of:", res)
        self.assertIn("pkg_a/shared.py", res)
        self.assertIn("pkg_b/shared.py", res)

    def test_read_file_with_line_number_in_path(self):
        res = self.impls["read_file"]("agent/tools.py:1")
        self.assertIn("1: def build_tools():", res)

    def test_read_file_directory_fails_cleanly(self):
        res = self.impls["read_file"]("agent")
        self.assertIn("ERROR: 'agent' is a directory, not a file.", res)

    def test_edit_file_auto_resolves_basename(self):
        res, diff = self.impls["edit_file"]("tools.py", "return 'tools'", "return 'modern_tools'")
        self.assertIn("Edited agent/tools.py (auto-resolved from 'tools.py')", res)
        self.assertIn("+    return 'modern_tools'", diff)
        updated = self.impls["read_file"]("agent/tools.py")
        self.assertIn("return 'modern_tools'", updated)

    def test_get_outline_auto_resolves_basename(self):
        res = self.impls["get_outline"]("tools.py")
        self.assertIn("agent/tools.py", res)
        self.assertIn("build_tools", res)

    def test_list_dir_missing_returns_clean_error(self):
        res = self.impls["list_dir"]("does_not_exist")
        self.assertIn("ERROR: directory 'does_not_exist' does not exist.", res)

    def test_grep_missing_path_returns_clean_error(self):
        res = self.impls["grep"]("build_tools", path="does_not_exist")
        self.assertIn("ERROR: path 'does_not_exist' does not exist.", res)


if __name__ == "__main__":
    unittest.main()
