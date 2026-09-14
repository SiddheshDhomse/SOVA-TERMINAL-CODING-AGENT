"""Unit tests for session persistence, chat history, metadata, and replay."""
import os
import shutil
import tempfile
import time
import unittest

from agent import sessions


class TestSessions(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_new_session_id_format(self):
        sid1 = sessions.new_session_id()
        sid2 = sessions.new_session_id()
        self.assertNotEqual(sid1, sid2)
        self.assertIn("-", sid1)

    def test_save_and_load_session(self):
        sid = "test-session-1"
        msgs = [
            {"role": "user", "content": "Hello SOVA, build a web crawler"},
            {"role": "assistant", "content": "I will inspect the workspace."},
        ]
        events = [
            {"type": "user_prompt", "text": "Hello SOVA, build a web crawler"},
            {"type": "tool_call", "name": "list_dir", "args": {}},
            {"type": "answer", "text": "Done!"},
        ]
        sessions.save_session(
            self.test_dir,
            sid,
            msgs,
            result={"finished": True, "summary": "All tasks completed"},
            events=events,
            metadata={"provider": "nvidia", "model": "meta/llama-3.3-70b-instruct"},
        )

        # Legacy load_session returns messages list
        loaded_msgs = sessions.load_session(self.test_dir, sid)
        self.assertEqual(len(loaded_msgs), 2)
        self.assertEqual(loaded_msgs[0]["content"], "Hello SOVA, build a web crawler")

        # Full load returns metadata, events, and messages
        full = sessions.load_session_full(self.test_dir, sid)
        self.assertEqual(full["id"], sid)
        self.assertEqual(full["title"], "Hello SOVA, build a web crawler")
        self.assertEqual(full["message_count"], 2)
        self.assertTrue(full["finished"])
        self.assertEqual(full["summary"], "All tasks completed")
        self.assertEqual(full["provider"], "nvidia")
        self.assertEqual(full["model"], "meta/llama-3.3-70b-instruct")
        self.assertEqual(len(full["events"]), 3)

    def test_list_sessions_ordering_and_metadata(self):
        # Create session 1
        sessions.save_session(
            self.test_dir,
            "sess-1",
            [{"role": "user", "content": "Task 1"}],
        )
        time.sleep(0.02)
        # Create session 2 (more recent)
        sessions.save_session(
            self.test_dir,
            "sess-2",
            [{"role": "user", "content": "Task 2"}],
        )

        rows = sessions.list_sessions(self.test_dir)
        self.assertEqual(len(rows), 2)
        # Most recent first
        self.assertEqual(rows[0]["id"], "sess-2")
        self.assertEqual(rows[1]["id"], "sess-1")
        self.assertEqual(rows[0]["title"], "Task 2")
        self.assertEqual(rows[0]["message_count"], 1)

    def test_delete_session(self):
        sid = "sess-del"
        sessions.save_session(self.test_dir, sid, [{"role": "user", "content": "Delete me"}])
        self.assertTrue(os.path.exists(sessions.session_path(self.test_dir, sid)))

        deleted = sessions.delete_session(self.test_dir, sid)
        self.assertTrue(deleted)
        self.assertFalse(os.path.exists(sessions.session_path(self.test_dir, sid)))

        # Deleting nonexistent session returns False
        self.assertFalse(sessions.delete_session(self.test_dir, "nonexistent"))


if __name__ == "__main__":
    unittest.main()
