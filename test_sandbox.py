"""Unit tests for GitWorktreeSandbox isolation and rollback."""
import os
import shutil
import subprocess
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
