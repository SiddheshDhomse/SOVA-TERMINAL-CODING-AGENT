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


if __name__ == "__main__":
    unittest.main()
