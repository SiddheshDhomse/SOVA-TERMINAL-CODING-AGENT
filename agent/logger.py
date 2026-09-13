"""Centralized logging and session trajectory recording for SOVA."""
import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional

_DEFAULT_LOG_DIR = ".sova/logs"
_logger: Optional[logging.Logger] = None


def get_log_dir(root_dir: Optional[str] = None) -> str:
    base = root_dir or os.getcwd()
    path = os.path.join(base, ".sova", "logs")
    os.makedirs(path, exist_ok=True)
    return path


def setup_logger(root_dir: Optional[str] = None) -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    log_dir = get_log_dir(root_dir)
    log_file = os.path.join(log_dir, "sova.log")

    logger = logging.getLogger("sova")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Avoid duplicate handlers if re-initialized
    if not logger.handlers:
        handler = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    _logger = logger
    return _logger


def close_logger() -> None:
    """Flush and close all logger handlers to release open file handles."""
    global _logger
    if _logger is not None:
        for handler in list(_logger.handlers):
            try:
                handler.flush()
                handler.close()
            except Exception:
                pass
            _logger.removeHandler(handler)
        _logger = None


def log_info(msg: str, root_dir: Optional[str] = None) -> None:
    logger = setup_logger(root_dir)
    logger.info(msg)


def log_error(msg: str, root_dir: Optional[str] = None) -> None:
    logger = setup_logger(root_dir)
    logger.error(msg)


def log_warn(msg: str, root_dir: Optional[str] = None) -> None:
    logger = setup_logger(root_dir)
    logger.warning(msg)


class SessionTrajectoryLogger:
    """Records step-by-step trajectory events for a specific session.
    
    Persisted to .sova/logs/<session_id>.trajectory.jsonl for auditing,
    evaluation replay, and web-console observability.
    """

    def __init__(self, root_dir: str, session_id: str):
        self.root_dir = root_dir
        self.session_id = session_id
        self.log_dir = get_log_dir(root_dir)
        self.file_path = os.path.join(self.log_dir, f"{session_id}.trajectory.jsonl")

    def record_step(self, event_type: str, data: Dict[str, Any]) -> None:
        entry = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_id": self.session_id,
            "type": event_type,
            **data,
        }
        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            log_warn(f"Failed to write trajectory entry for session {self.session_id}: {exc}")

    def get_trajectory(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.file_path):
            return []
        entries = []
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            pass
        return entries


def get_recent_logs(root_dir: Optional[str] = None, max_lines: int = 200) -> List[str]:
    """Retrieve the most recent lines from the central log file."""
    log_dir = get_log_dir(root_dir)
    log_file = os.path.join(log_dir, "sova.log")
    if not os.path.exists(log_file):
        return []
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            return [l.rstrip("\r\n") for l in lines[-max_lines:]]
    except OSError:
        return []
