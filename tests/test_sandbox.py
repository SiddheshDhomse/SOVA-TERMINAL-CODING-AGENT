"""Unit tests for GitWorktreeSandbox isolation and rollback."""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent.loop import run_agent
from agent.sandbox import GitWorktreeSandbox, is_git_repo


class TestGitWorktreeSandbox(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        # Initialize an isolated Git repository
        subprocess.run(["git", "init"], cwd=self.test_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "sova@test.local"], cwd=self.test_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "SOVA Test"], cwd=self.test_dir, capture_output=True, check=True)

        # Create initial commit so HEAD exists
        self.readme = os.path.join(self.test_dir, "README.md")
        with open(self.readme, "w", encoding="utf-8") as f:
            f.write("# Main Repo\nInitial content\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.test_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.test_dir, capture_output=True, check=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_is_git_repo(self):
        self.assertTrue(is_git_repo(self.test_dir))
        non_git = tempfile.mkdtemp()
        try:
            self.assertFalse(is_git_repo(non_git))
        finally:
            shutil.rmtree(non_git, ignore_errors=True)

    def test_create_and_discard_sandbox(self):
        sandbox = GitWorktreeSandbox(self.test_dir, "test_task_1")
        worktree_path = sandbox.create()
        self.assertTrue(os.path.exists(worktree_path))
        self.assertTrue(sandbox.created)

        # Modify file inside sandbox
        sb_file = os.path.join(worktree_path, "sandbox_file.py")
        with open(sb_file, "w", encoding="utf-8") as f:
            f.write("print('in sandbox')\n")

        self.assertTrue(sandbox.has_changes())
        diff = sandbox.get_diff()
        # Newly untracked files aren't in git diff until staged, but has_changes detects it
        self.assertTrue(os.path.exists(sb_file))
        # Main tree must NOT have sandbox_file.py
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "sandbox_file.py")))

        # Discard
        ok, msg = sandbox.discard()
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(worktree_path))

    def test_apply_sandbox_to_main(self):
        sandbox = GitWorktreeSandbox(self.test_dir, "test_task_apply")
        worktree_path = sandbox.create()

        # Modify existing file inside sandbox
        sb_readme = os.path.join(worktree_path, "README.md")
        with open(sb_readme, "a", encoding="utf-8") as f:
            f.write("Added by sandbox\n")

        self.assertTrue(sandbox.has_changes())
        diff = sandbox.get_diff()
        self.assertIn("+Added by sandbox", diff)

        # Apply to main
        ok, msg = sandbox.apply_to_main("Merge sandbox changes")
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(worktree_path))

        # Main tree README should now have the change
        with open(self.readme, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("Added by sandbox", content)

    def test_run_agent_with_sandbox_isolation(self):
        sandbox = GitWorktreeSandbox(self.test_dir, "test_agent_sb")

        with patch("agent.loop._safe_chat") as mock_chat:
            def make_resp(tool_name, tool_args, content=""):
                call = MagicMock()
                call.id = f"call_{tool_name}"
                call.function = MagicMock()
                call.function.name = tool_name
                call.function.arguments = tool_args
                msg = MagicMock()
                msg.content = content
                msg.tool_calls = [call]
                msg.model_dump.return_value = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [{
                        "id": call.id,
                        "type": "function",
                        "function": {"name": tool_name, "arguments": tool_args}
                    }]
                }
                choice = MagicMock()
                choice.message = msg
                resp = MagicMock()
                resp.choices = [choice]
                return resp

            first_resp = make_resp("write_file", '{"path": "sandbox_isolated.py", "content": "print(\'isolated\')"}')
            second_resp = make_resp("finish", '{"summary": "Done creating isolated file"}')

            mock_chat.side_effect = [
                (first_resp, None, False),
                (second_resp, None, False),
            ]

            result = run_agent(
                self.test_dir,
                "Create isolated file",
                sandbox=sandbox,
                verbose=False,
                force_task=True,
            )

            # Check that file was created in sandbox worktree
            isolated_file = os.path.join(sandbox.worktree_dir, "sandbox_isolated.py")
            self.assertTrue(os.path.exists(isolated_file))

            # Main repo must NOT have the file yet
            main_file = os.path.join(self.test_dir, "sandbox_isolated.py")
            self.assertFalse(os.path.exists(main_file))

            # Sandbox status returned in result
            self.assertIn("sandbox", result)
            self.assertTrue(result["sandbox"]["active"])
            self.assertTrue(result["sandbox"]["has_changes"])

            # Now apply sandbox to main
            ok, msg = sandbox.apply_to_main("Merge isolated file")
            self.assertTrue(ok)
            self.assertTrue(os.path.exists(main_file))

    def test_sandbox_status_event_emitted(self):
        sandbox = GitWorktreeSandbox(self.test_dir, "test_event_sb")
        events = []

        with patch("agent.loop._safe_chat") as mock_chat:
            call = MagicMock()
            call.id = "call_finish"
            call.function = MagicMock()
            call.function.name = "finish"
            call.function.arguments = '{"summary": "Done"}'
            msg = MagicMock()
            msg.content = ""
            msg.tool_calls = [call]
            msg.model_dump.return_value = {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": call.id, "type": "function", "function": {"name": "finish", "arguments": '{"summary": "Done"}'}}]
            }
            choice = MagicMock()
            choice.message = msg
            resp = MagicMock()
            resp.choices = [choice]

            mock_chat.side_effect = [(resp, None, False)]

            run_agent(
                self.test_dir,
                "Quick task",
                sandbox=sandbox,
                on_event=lambda ev: events.append(ev),
                verbose=False,
                force_task=True,
            )

            sb_events = [e for e in events if e.get("type") == "sandbox_status"]
            self.assertEqual(len(sb_events), 1)
            self.assertTrue(sb_events[0]["active"])
            self.assertEqual(sb_events[0]["worktree"], sandbox.worktree_dir)

        sandbox.discard()


if __name__ == "__main__":
    unittest.main()
