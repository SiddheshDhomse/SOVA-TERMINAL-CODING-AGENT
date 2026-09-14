"""Persistent conversation sessions, stored as one JSON file per session under .sova/sessions/."""
import json
import os
import random
import string
import time
from typing import Any, Dict, List, Optional


def _sessions_dir(root_dir: str) -> str:
    return os.path.join(root_dir, ".sova", "sessions")


def new_session_id() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{suffix}"


def session_path(root_dir: str, session_id: str) -> str:
    return os.path.join(_sessions_dir(root_dir), f"{session_id}.json")


def save_session(
    root_dir: str,
    session_id: str,
    messages: List[Dict[str, Any]],
    result: Optional[Dict[str, Any]] = None,
    events: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist session state, messages, and execution events atomically."""
    path = session_path(root_dir, session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    created_at = time.time()
    existing_events = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
                created_at = old.get("created_at", created_at)
                if not events and "events" in old:
                    existing_events = old.get("events", [])
        except Exception:
            pass

    first_user = next((m.get("content") for m in messages if m.get("role") == "user"), "")
    first_clean = str(first_user or "").strip()
    title = (first_clean[:60] + "...") if len(first_clean) > 60 else (first_clean or "New Chat")

    meta = metadata or {}
    payload = {
        "id": session_id,
        "created_at": created_at,
        "updated_at": time.time(),
        "title": title,
        "first_message": first_clean[:250],
        "message_count": len(messages),
        "finished": bool(result and result.get("finished")),
        "summary": str((result or {}).get("summary", "")),
        "provider": meta.get("provider", ""),
        "model": meta.get("model", ""),
        "messages": messages,
        "events": events if events is not None else existing_events,
    }
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def load_session(root_dir: str, session_id: str) -> List[Dict[str, Any]]:
    """Return raw message list for compatibility with conversation loops."""
    path = session_path(root_dir, session_id)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("messages", [])


def load_session_full(root_dir: str, session_id: str) -> Dict[str, Any]:
    """Return full session dictionary including events, messages, and metadata."""
    path = session_path(root_dir, session_id)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def delete_session(root_dir: str, session_id: str) -> bool:
    """Delete a saved session from disk."""
    path = session_path(root_dir, session_id)
    deleted = False
    if os.path.exists(path):
        try:
            os.remove(path)
            deleted = True
        except OSError:
            pass
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return deleted


def list_sessions(root_dir: str, limit: int = 50) -> List[Dict[str, Any]]:
    """List recent sessions sorted by last updated time."""
    directory = _sessions_dir(root_dir)
    if not os.path.isdir(directory):
        return []
    rows = []
    for name in os.listdir(directory):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        try:
            with open(os.path.join(directory, name), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        rows.append({
            "id": data.get("id", name[:-5]),
            "title": data.get("title") or (data.get("first_message") or "New Chat")[:50],
            "updated_at": data.get("updated_at", 0),
            "created_at": data.get("created_at", data.get("updated_at", 0)),
            "first_message": data.get("first_message", ""),
            "message_count": data.get("message_count", len(data.get("messages", []))),
            "finished": data.get("finished", False),
            "provider": data.get("provider", ""),
            "model": data.get("model", ""),
        })
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    return rows[:limit]
