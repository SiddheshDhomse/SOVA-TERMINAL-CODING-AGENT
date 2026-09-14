"""Granular file checkpointing, snapshots, and rollback (/undo) engine for SOVA."""
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CheckpointEntry:
    step_id: int
    session_id: str
    file_path: str  # relative path
    action: str  # "write_file", "edit_file"
    timestamp: float
    existed_before: bool
    snapshot_rel_path: Optional[str] = None  # path relative to checkpoints dir

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckpointEntry":
        return cls(**data)


class CheckpointManager:
    """Manages file checkpoints per session to allow instant undo/rollback of edits."""

    def __init__(self, root_dir: str, session_id: Optional[str] = None):
        self.root_dir = os.path.abspath(root_dir)
        self.session_id = session_id or "default"
        self.checkpoints_base = os.path.join(self.root_dir, ".sova", "checkpoints", self.session_id)
        self.meta_file = os.path.join(self.checkpoints_base, "history.json")
        self.history: List[CheckpointEntry] = []
        self._load_history()

    def _load_history(self) -> None:
        if not os.path.exists(self.meta_file):
            self.history = []
            return
        try:
            with open(self.meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    self.history = [CheckpointEntry.from_dict(d) for d in data]
        except Exception:
            self.history = []

    def _save_history(self) -> None:
        try:
            os.makedirs(self.checkpoints_base, exist_ok=True)
            with open(self.meta_file, "w", encoding="utf-8") as f:
                json.dump([c.to_dict() for c in self.history], f, indent=2)
        except Exception:
            pass

    def record_before_change(self, rel_path: str, action: str) -> int:
        """Snapshot file contents before a write or edit action."""
        full_target = os.path.abspath(os.path.join(self.root_dir, rel_path))
        step_id = len(self.history) + 1
        existed = os.path.exists(full_target)
        snapshot_rel = None

        if existed:
            snapshots_dir = os.path.join(self.checkpoints_base, "snapshots")
            os.makedirs(snapshots_dir, exist_ok=True)
            snapshot_name = f"step_{step_id}_{os.path.basename(rel_path)}.bak"
            snapshot_full = os.path.join(snapshots_dir, snapshot_name)
            try:
                shutil.copy2(full_target, snapshot_full)
                snapshot_rel = os.path.join("snapshots", snapshot_name).replace("\\", "/")
            except OSError:
                snapshot_rel = None

        entry = CheckpointEntry(
            step_id=step_id,
            session_id=self.session_id,
            file_path=rel_path.replace("\\", "/"),
            action=action,
            timestamp=time.time(),
            existed_before=existed,
            snapshot_rel_path=snapshot_rel,
        )
        self.history.append(entry)
        self._save_history()
        return step_id

    def undo_last(self) -> Tuple[bool, str, Optional[str]]:
        """Undo the most recent file change in this session.
        
        Returns:
            (success: bool, message: str, restored_file: Optional[str])
        """
        if not self.history:
            return False, "No file modifications to undo in this session.", None

        entry = self.history.pop()
        self._save_history()

        target_full = os.path.abspath(os.path.join(self.root_dir, entry.file_path))

        if not entry.existed_before:
            # File was created by this step; delete it
            if os.path.exists(target_full):
                try:
                    os.remove(target_full)
                    return True, f"Undid creation: deleted newly created file '{entry.file_path}'.", entry.file_path
                except OSError as e:
                    return False, f"Failed to delete '{entry.file_path}': {e}", entry.file_path
            return True, f"File '{entry.file_path}' was already deleted.", entry.file_path
        else:
            # Restore previous snapshot
            if not entry.snapshot_rel_path:
                return False, f"No snapshot backup found for '{entry.file_path}'.", entry.file_path

            snapshot_full = os.path.join(self.checkpoints_base, entry.snapshot_rel_path)
            if not os.path.exists(snapshot_full):
                return False, f"Snapshot file '{snapshot_full}' is missing.", entry.file_path

            try:
                os.makedirs(os.path.dirname(target_full), exist_ok=True)
                shutil.copy2(snapshot_full, target_full)
                return True, f"Restored '{entry.file_path}' to state prior to step #{entry.step_id} ({entry.action}).", entry.file_path
            except OSError as e:
                return False, f"Failed to restore '{entry.file_path}': {e}", entry.file_path

    def list_checkpoints(self) -> List[Dict[str, Any]]:
        """List all available checkpoints for undo in this session."""
        return [c.to_dict() for c in reversed(self.history)]
