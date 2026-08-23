"""Persistent conversation sessions, stored as one JSON file per session under .sova/sessions/."""
import json
import os
import random
import string
import time


def _sessions_dir(root_dir):
    return os.path.join(root_dir, ".sova", "sessions")


def new_session_id():
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{suffix}"


def session_path(root_dir, session_id):
    return os.path.join(_sessions_dir(root_dir), f"{session_id}.json")


def save_session(root_dir, session_id, messages, result=None):
    path = session_path(root_dir, session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    first_user = next((m.get("content") for m in messages if m.get("role") == "user"), "")
    payload = {
        "id": session_id,
        "updated_at": time.time(),
        "first_message": (first_user or "")[:200],
        "finished": bool(result and result.get("finished")),
        "messages": messages,
    }
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp_path, path)


def load_session(root_dir, session_id):
    path = session_path(root_dir, session_id)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["messages"]


def list_sessions(root_dir, limit=20):
    directory = _sessions_dir(root_dir)
    if not os.path.isdir(directory):
        return []
    rows = []
    for name in os.listdir(directory):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        rows.append({
            "id": data.get("id", name[:-5]),
            "updated_at": data.get("updated_at", 0),
            "first_message": data.get("first_message", ""),
            "finished": data.get("finished", False),
        })
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    return rows[:limit]
