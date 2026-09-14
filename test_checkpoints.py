"""Unit tests for CheckpointManager, snapshots, and file rollback engine."""
import os
import shutil
import tempfile
import unittest

from agent.checkpoints import CheckpointManager
from agent.tools import build_tools


class TestCheckpoints(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.session_id = "test-session-chk"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_initial_state(self):
        cm = CheckpointManager(self.test_dir, self.session_id)
        self.assertEqual(cm.list_checkpoints(), [])
        success, msg, restored = cm.undo_last()
        self.assertFalse(success)
        self.assertIsNone(restored)

    def test_undo_newly_created_file(self):
        cm = CheckpointManager(self.test_dir, self.session_id)
        target = "new_module.py"
        full_target = os.path.join(self.test_dir, target)

        # Record checkpoint before creation
        step = cm.record_before_change(target, "write_file")
        self.assertEqual(step, 1)

        # Create file
        with open(full_target, "w", encoding="utf-8") as f:
            f.write("def hello(): pass\n")
        self.assertTrue(os.path.exists(full_target))

        # Undo
        success, msg, restored = cm.undo_last()
        self.assertTrue(success)
        self.assertEqual(restored, target)
        self.assertFalse(os.path.exists(full_target))
        self.assertEqual(len(cm.list_checkpoints()), 0)

    def test_undo_modified_file(self):
        cm = CheckpointManager(self.test_dir, self.session_id)
        target = "existing.py"
        full_target = os.path.join(self.test_dir, target)

        initial_content = "x = 1\ny = 2\n"
        with open(full_target, "w", encoding="utf-8") as f:
            f.write(initial_content)

        # Step 1: Record modification
        step1 = cm.record_before_change(target, "edit_file")
        self.assertEqual(step1, 1)
        with open(full_target, "w", encoding="utf-8") as f:
            f.write("x = 100\ny = 2\n")

        # Step 2: Record second modification
        step2 = cm.record_before_change(target, "edit_file")
        self.assertEqual(step2, 2)
        with open(full_target, "w", encoding="utf-8") as f:
            f.write("x = 100\ny = 200\n")

        self.assertEqual(len(cm.list_checkpoints()), 2)

        # Undo step 2 -> should restore x = 100, y = 2
        success, msg, restored = cm.undo_last()
        self.assertTrue(success)
        with open(full_target, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "x = 100\ny = 2\n")

        # Undo step 1 -> should restore initial x = 1, y = 2
        success, msg, restored = cm.undo_last()
        self.assertTrue(success)
        with open(full_target, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), initial_content)

        # No more to undo
        success, msg, restored = cm.undo_last()
        self.assertFalse(success)

    def test_persistence_across_instances(self):
        cm1 = CheckpointManager(self.test_dir, self.session_id)
        target = "test.txt"
        full_target = os.path.join(self.test_dir, target)
        with open(full_target, "w", encoding="utf-8") as f:
            f.write("version 1")

        cm1.record_before_change(target, "edit_file")
        with open(full_target, "w", encoding="utf-8") as f:
            f.write("version 2")

        # Create new manager instance with same session
        cm2 = CheckpointManager(self.test_dir, self.session_id)
        checkpoints = cm2.list_checkpoints()
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]["file_path"], "test.txt")

        # Undo via second manager
        success, msg, restored = cm2.undo_last()
        self.assertTrue(success)
        with open(full_target, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "version 1")

    def test_tool_integration_write_and_edit(self):
        schemas, impls = build_tools(self.test_dir, session_id=self.session_id)
        self.assertIn("undo", impls)
        self.assertIn("_checkpoints", impls)

        # Tool write_file (new file)
        out, diff = impls["write_file"]("app.py", "def main(): print('hi')\n")
        self.assertIn("Wrote", out)
        app_path = os.path.join(self.test_dir, "app.py")
        self.assertTrue(os.path.exists(app_path))

        # Tool edit_file
        out, diff = impls["edit_file"]("app.py", "print('hi')", "print('hello world')")
        self.assertIn("Edited", out)
        with open(app_path, "r", encoding="utf-8") as f:
            self.assertIn("print('hello world')", f.read())

        # Checkpoints count should be 2
        chk = impls["_checkpoints"].list_checkpoints()
        self.assertEqual(len(chk), 2)

        # Undo edit
        success, msg, restored = impls["undo"]()
        self.assertTrue(success)
        with open(app_path, "r", encoding="utf-8") as f:
            self.assertIn("print('hi')", f.read())

        # Undo write -> deletes app.py
        success, msg, restored = impls["undo"]()
        self.assertTrue(success)
        self.assertFalse(os.path.exists(app_path))


if __name__ == "__main__":
    unittest.main()
