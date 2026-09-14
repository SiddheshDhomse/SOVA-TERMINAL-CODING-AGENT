"""Git worktree sandbox isolation engine for zero-risk autonomous task execution."""
import os
import shutil
import subprocess
import time
from typing import Optional, Tuple


def is_git_repo(root_dir: str) -> bool:
    """Check whether root_dir is a Git repository root or worktree."""
    git_dir = os.path.join(root_dir, ".git")
    if os.path.exists(git_dir):
        return True
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root_dir,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0:
            top_level = os.path.abspath(res.stdout.strip())
            return os.path.abspath(root_dir) == top_level
        return False
    except Exception:
        return False


class GitWorktreeSandbox:
    """Manages an isolated Git worktree for zero-risk task execution and review."""

    def __init__(self, root_dir: str, task_id: Optional[str] = None, base_ref: str = "HEAD"):
        self.root_dir = os.path.abspath(root_dir)
        self.task_id = task_id or f"{time.strftime('%Y%m%d_%H%M%S')}_{os.urandom(2).hex()}"
        self.branch_name = f"sova/sandbox-{self.task_id}"
        self.worktree_dir = os.path.abspath(os.path.join(self.root_dir, ".sova", "worktrees", self.task_id))
        self.base_ref = base_ref
        self.created = False

    def create(self) -> str:
        """Create and initialize the isolated worktree directory."""
        if not is_git_repo(self.root_dir):
            raise ValueError(f"Directory '{self.root_dir}' is not a Git repository.")

        os.makedirs(os.path.dirname(self.worktree_dir), exist_ok=True)
        # Ensure clean state if previously leftover
        if os.path.exists(self.worktree_dir):
            self.discard()

        cmd = ["git", "worktree", "add", "-b", self.branch_name, self.worktree_dir, self.base_ref]
        res = subprocess.run(cmd, cwd=self.root_dir, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to create git worktree: {res.stderr.strip() or res.stdout.strip()}")

        self.created = True
        return self.worktree_dir

    def get_diff(self) -> str:
        """Return unified diff of changes made inside the sandbox relative to base_ref."""
        if not self.created or not os.path.exists(self.worktree_dir):
            return ""
        try:
            res = subprocess.run(
                ["git", "diff", self.base_ref],
                cwd=self.worktree_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return res.stdout
        except Exception:
            return ""

    def has_changes(self) -> bool:
        """Check if any files were modified, added, or deleted in the sandbox."""
        if not self.created or not os.path.exists(self.worktree_dir):
            return False
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.worktree_dir,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return bool(res.stdout.strip())
        except Exception:
            return False

    def apply_to_main(self, commit_msg: Optional[str] = None) -> Tuple[bool, str]:
        """Apply/squash sandbox changes into the main working tree and clean up worktree."""
        if not self.created:
            return False, "Sandbox was not created or has already been closed."

        try:
            # Stage any changes inside the worktree
            subprocess.run(["git", "add", "-A"], cwd=self.worktree_dir, capture_output=True)
            # Commit inside sandbox branch if there are staged changes
            st = subprocess.run(["git", "status", "--porcelain"], cwd=self.worktree_dir, capture_output=True, text=True)
            if st.stdout.strip():
                msg = commit_msg or f"Sandbox changes for {self.task_id}"
                subprocess.run(["git", "commit", "-m", msg], cwd=self.worktree_dir, capture_output=True)

            # Remove worktree first so git allows branch manipulation
            subprocess.run(["git", "worktree", "remove", "--force", self.worktree_dir], cwd=self.root_dir, capture_output=True)
            if os.path.exists(self.worktree_dir):
                shutil.rmtree(self.worktree_dir, ignore_errors=True)
            subprocess.run(["git", "worktree", "prune"], cwd=self.root_dir, capture_output=True)

            # Squash merge sandbox branch into main tree
            merge_res = subprocess.run(
                ["git", "merge", "--squash", self.branch_name],
                cwd=self.root_dir,
                capture_output=True,
                text=True,
            )

            # Clean up ephemeral branch
            subprocess.run(["git", "branch", "-D", self.branch_name], cwd=self.root_dir, capture_output=True)
            self.created = False

            if merge_res.returncode == 0:
                return True, f"Successfully merged sandbox changes ({self.task_id}) into main workspace."
            return False, f"Squash merge returned error: {merge_res.stderr.strip()}"
        except Exception as exc:
            self.discard()
            return False, f"Failed to apply sandbox changes: {exc}"

    def discard(self) -> Tuple[bool, str]:
        """Clean up and remove the worktree and ephemeral branch without applying changes."""
        try:
            if os.path.exists(self.worktree_dir):
                subprocess.run(
                    ["git", "worktree", "remove", "--force", self.worktree_dir],
                    cwd=self.root_dir,
                    capture_output=True,
                )
                if os.path.exists(self.worktree_dir):
                    shutil.rmtree(self.worktree_dir, ignore_errors=True)

            subprocess.run(["git", "worktree", "prune"], cwd=self.root_dir, capture_output=True)
            subprocess.run(["git", "branch", "-D", self.branch_name], cwd=self.root_dir, capture_output=True)
            self.created = False
            return True, f"Sandbox {self.task_id} discarded cleanly."
        except Exception as exc:
            return False, f"Failed to discard sandbox: {exc}"

    def __enter__(self) -> "GitWorktreeSandbox":
        self.create()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self.discard()
