"""Web UI for Sova with Claude Code-grade interface, dynamic provider/model dropdowns,
session history, approval modals, diffs, live logs, collapsible side panels, and prompt-embedded model selectors.
"""
import argparse
import json
import os
import platform
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from . import credentials, llm, sessions
from .logger import SessionTrajectoryLogger, get_recent_logs
from .loop import run_agent

_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "index.html")
_PAGE_CACHE = None


def get_page_html() -> str:
    """Load the single-page application HTML template with in-memory caching."""
    global _PAGE_CACHE
    if _PAGE_CACHE is not None:
        return _PAGE_CACHE
    if os.path.exists(_TEMPLATE_PATH):
        try:
            with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
                _PAGE_CACHE = f.read()
                return _PAGE_CACHE
        except OSError:
            pass
    return "<!doctype html><html><body><h1>SOVA Web UI</h1><p>Template missing.</p></body></html>"


class _PageProxy:
    """String proxy for backwards compatibility with any code referencing web._PAGE."""
    def __str__(self):
        return get_page_html()

    def encode(self, *args, **kwargs):
        return get_page_html().encode(*args, **kwargs)

    def replace(self, *args, **kwargs):
        return get_page_html().replace(*args, **kwargs)

    def __len__(self):
        return len(get_page_html())


_PAGE = _PageProxy()


def _build_state(root_dir):
    return {
        "lock": threading.Lock(),
        "running": False,
        "events": [],
        "next_event": 1,
        "thread": None,
        "stop_event": None,
        "conversation": None,
        "result": None,
        "root_dir": root_dir,
        "session_id": sessions.new_session_id(),
        "pending": {},           # approval_id -> threading.Event
        "pending_decision": {},  # approval_id -> bool
        "auto_approve": False,
        "sandbox": None,
    }


def _add_event(state, event):
    with state["lock"]:
        row = {"id": state["next_event"], "ts": time.time(), **event}
        state["next_event"] += 1
        state["events"].append(row)
        if len(state["events"]) > 4000:
            state["events"] = state["events"][-2000:]


def _json_response(handler, code, payload):
    blob = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(blob)))
    handler.end_headers()
    handler.wfile.write(blob)


def _text_response(handler, code, text, content_type="text/html; charset=utf-8"):
    blob = text.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(blob)))
    handler.end_headers()
    handler.wfile.write(blob)


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def handle(self):
            try:
                super().handle()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass
            except OSError as exc:
                if getattr(exc, "winerror", None) in (10053, 10054):
                    pass
                else:
                    raise

        def log_message(self, format, *args):
            return

        def _read_json(self):
            size = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(size) if size > 0 else b"{}"
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return None

        def _stream_events(self, cursor):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    with state["lock"]:
                        events = [e for e in state["events"] if e["id"] > cursor]
                        running = state["running"]
                    if events:
                        cursor = events[-1]["id"]
                        payload = {"running": running, "cursor": cursor, "events": events}
                        self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
                    else:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    time.sleep(0.3)
            except OSError:
                return

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                return _text_response(self, 200, get_page_html())

            if parsed.path == "/api/stream":
                query = parse_qs(parsed.query)
                try:
                    cursor = int((query.get("cursor") or ["0"])[0])
                except ValueError:
                    cursor = 0
                return self._stream_events(cursor)

            if parsed.path == "/api/providers-models":
                providers = llm.get_available_providers()
                catalog = {}
                for p in providers:
                    catalog[p] = {
                        "name": llm.PROVIDERS_CONFIG.get(p, {}).get("name", p),
                        "default": llm.DEFAULT_MODELS.get(p, ""),
                        "models": llm.get_models_for_provider(p),
                    }
                return _json_response(self, 200, {
                    "providers": providers,
                    "current_provider": llm.get_provider(),
                    "current_model": llm.get_model(),
                    "catalog": catalog,
                })

            if parsed.path == "/api/sessions":
                rows = sessions.list_sessions(state["root_dir"])
                return _json_response(self, 200, {"sessions": rows})

            if parsed.path == "/api/checkpoints":
                from .checkpoints import CheckpointManager
                sid = state["session_id"]
                cm = CheckpointManager(state["root_dir"], sid)
                return _json_response(self, 200, {"session_id": sid, "checkpoints": cm.list_checkpoints()})

            if parsed.path == "/api/sandbox/status":
                from .sandbox import is_git_repo
                is_git = is_git_repo(state["root_dir"])
                sb = state.get("sandbox")
                active = sb is not None and getattr(sb, "created", False)
                return _json_response(self, 200, {
                    "is_git": is_git,
                    "active": active,
                    "branch": sb.branch_name if active else "",
                    "worktree": sb.worktree_dir if active else "",
                    "has_changes": sb.has_changes() if active else False,
                })

            if parsed.path == "/api/sandbox/diff":
                sb = state.get("sandbox")
                if sb is None or not getattr(sb, "created", False):
                    return _json_response(self, 200, {"diff": ""})
                return _json_response(self, 200, {"diff": sb.get_diff()})

            if parsed.path == "/api/session":
                query = parse_qs(parsed.query)
                sid = (query.get("id") or [""])[0]
                try:
                    full_sess = sessions.load_session_full(state["root_dir"], sid)
                    trajectory = SessionTrajectoryLogger(state["root_dir"], sid).get_trajectory()
                    return _json_response(self, 200, {
                        "id": sid,
                        "session": full_sess,
                        "messages": full_sess.get("messages", []),
                        "events": full_sess.get("events", []),
                        "trajectory": trajectory,
                    })
                except Exception:
                    return _json_response(self, 404, {"error": "Session not found"})

            if parsed.path == "/api/logs":
                lines = get_recent_logs(state["root_dir"], max_lines=150)
                return _json_response(self, 200, {"logs": lines})

            if parsed.path == "/api/credentials":
                root = state["root_dir"]
                saved = credentials.load_credentials(root)
                known_keys = credentials.get_known_keys()
                known_key_names = {k["key"] for k in known_keys}

                key_info = {}
                for k in known_keys:
                    kname = k["key"]
                    active_val = credentials.get_active_credential(kname, root)
                    key_info[kname] = {
                        "masked": credentials.mask_value(active_val) if active_val else "",
                        "is_set": bool(active_val),
                        "is_saved": kname in saved,
                    }

                custom_keys = []
                for kname, v in saved.items():
                    if kname not in known_key_names:
                        custom_keys.append({
                            "key": kname,
                            "masked": credentials.mask_value(v),
                        })

                return _json_response(self, 200, {
                    "key_info": key_info,
                    "known_keys": known_keys,
                    "custom_keys": custom_keys,
                })

            if parsed.path == "/api/system-info":
                root = state["root_dir"]
                saved = credentials.load_credentials(root)
                return _json_response(self, 200, {
                    "python_version": sys.version,
                    "platform": platform.platform(),
                    "workspace": root,
                    "provider": llm.get_provider(),
                    "model": llm.get_model(),
                    "credentials_count": len(saved),
                    "session_id": state["session_id"],
                })

            return _json_response(self, 404, {"error": "not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            data = self._read_json()
            if data is None:
                return _json_response(self, 400, {"error": "invalid json"})

            if parsed.path == "/api/sandbox/toggle":
                from .sandbox import GitWorktreeSandbox, is_git_repo
                if not is_git_repo(state["root_dir"]):
                    return _json_response(self, 400, {"error": "Current workspace is not a Git repository"})
                sb = state.get("sandbox")
                if sb is not None and getattr(sb, "created", False):
                    if sb.has_changes():
                        return _json_response(self, 409, {"error": "Sandbox has uncommitted changes. Apply or discard before disabling."})
                    sb.discard()
                    state["sandbox"] = None
                    return _json_response(self, 200, {"active": False, "message": "Sandbox deactivated."})
                else:
                    new_sb = GitWorktreeSandbox(state["root_dir"], task_id=state["session_id"])
                    try:
                        new_sb.create()
                        state["sandbox"] = new_sb
                        return _json_response(self, 200, {"active": True, "branch": new_sb.branch_name, "message": "Sandbox activated."})
                    except Exception as exc:
                        return _json_response(self, 500, {"error": str(exc)})

            if parsed.path == "/api/sandbox/apply":
                sb = state.get("sandbox")
                if sb is None or not getattr(sb, "created", False):
                    return _json_response(self, 400, {"error": "No active sandbox"})
                commit_msg = data.get("commit_message") or None
                ok, msg = sb.apply_to_main(commit_msg)
                if ok:
                    state["sandbox"] = None
                    return _json_response(self, 200, {"ok": True, "message": msg})
                return _json_response(self, 500, {"error": msg})

            if parsed.path == "/api/sandbox/discard":
                sb = state.get("sandbox")
                if sb is None or not getattr(sb, "created", False):
                    return _json_response(self, 400, {"error": "No active sandbox"})
                ok, msg = sb.discard()
                state["sandbox"] = None
                return _json_response(self, 200, {"ok": ok, "message": msg})

            if parsed.path == "/api/undo":
                with state["lock"]:
                    if state["running"]:
                        return _json_response(self, 409, {"error": "cannot undo while agent is running"})
                    sid = state["session_id"]
                from .checkpoints import CheckpointManager
                cm = CheckpointManager(state["root_dir"], sid)
                success, msg, restored = cm.undo_last()
                _add_event(state, {
                    "type": "tool_result",
                    "name": "undo",
                    "result": msg,
                    "diff": None,
                })
                return _json_response(self, 200, {"ok": success, "message": msg, "restored_file": restored})

            if parsed.path == "/api/delete_session":
                sid = str(data.get("id") or "").strip()
                if not sid:
                    return _json_response(self, 400, {"error": "id is required"})
                success = sessions.delete_session(state["root_dir"], sid)
                return _json_response(self, 200, {"ok": success})

            if parsed.path == "/api/new":
                with state["lock"]:
                    if state["running"]:
                        return _json_response(self, 409, {"error": "cannot reset while agent is running"})
                    state["conversation"] = None
                    state["events"] = []
                    state["next_event"] = 1
                    state["result"] = None
                    state["session_id"] = sessions.new_session_id()
                return _json_response(self, 200, {"ok": True, "session_id": state["session_id"]})

            if parsed.path == "/api/resume_session":
                session_id = str(data.get("id") or "").strip()
                if not session_id:
                    return _json_response(self, 400, {"error": "id is required"})
                with state["lock"]:
                    if state["running"]:
                        return _json_response(self, 409, {"error": "cannot resume while running"})
                    try:
                        state["conversation"] = sessions.load_session(state["root_dir"], session_id)
                    except Exception:
                        return _json_response(self, 404, {"error": "session not found"})
                    state["session_id"] = session_id
                    state["events"] = []
                    state["next_event"] = 1
                    state["result"] = None
                return _json_response(self, 200, {"ok": True})

            if parsed.path in ("/api/approve", "/api/deny"):
                approval_id = str(data.get("id") or "")
                always = bool(data.get("always"))
                with state["lock"]:
                    done = state["pending"].get(approval_id)
                    if done is None:
                        return _json_response(self, 404, {"error": "unknown approval id"})
                    decision = parsed.path.endswith("approve")
                    state["pending_decision"][approval_id] = decision
                    if always and decision:
                        state["auto_approve"] = True
                done.set()
                return _json_response(self, 200, {"ok": True})

            if parsed.path == "/api/stop":
                with state["lock"]:
                    stop_event = state["stop_event"]
                if stop_event is None:
                    return _json_response(self, 200, {"ok": True, "note": "idle"})
                stop_event.set()
                with state["lock"]:
                    for done in state["pending"].values():
                        done.set()
                _add_event(state, {"type": "error", "message": "Task stopped by user."})
                return _json_response(self, 200, {"ok": True})

            if parsed.path == "/api/start":
                task = str(data.get("task") or "").strip()
                provider = str(data.get("provider") or "").strip().lower()
                model = str(data.get("model") or "").strip() or None
                if not task:
                    return _json_response(self, 400, {"error": "task is required"})

                with state["lock"]:
                    if state["running"]:
                        return _json_response(self, 409, {"error": "agent is already running"})
                    stop_event = threading.Event()
                    state["running"] = True
                    state["stop_event"] = stop_event
                    state["result"] = None
                    state["auto_approve"] = bool(data.get("auto_approve"))

                if provider:
                    os.environ["SOVA_PROVIDER"] = provider

                def _on_event(event):
                    _add_event(state, event)

                def _on_permission(name, args):
                    with state["lock"]:
                        if state["auto_approve"]:
                            return True
                    approval_id = uuid.uuid4().hex[:8]
                    done = threading.Event()
                    with state["lock"]:
                        state["pending"][approval_id] = done

                    diff = None
                    try:
                        from .tools import unified_diff
                        sb = state.get("sandbox")
                        base_dir = sb.worktree_dir if (sb is not None and getattr(sb, "created", False)) else state["root_dir"]
                        if name == "edit_file" and isinstance(args, dict) and "path" in args and "old_str" in args and "new_str" in args:
                            full = os.path.abspath(os.path.join(base_dir, args["path"]))
                            if os.path.exists(full):
                                with open(full, "r", encoding="utf-8", errors="replace") as f:
                                    curr = f.read()
                                curr_norm = curr.replace("\r\n", "\n")
                                old_norm = args["old_str"].replace("\r\n", "\n")
                                new_norm = args["new_str"].replace("\r\n", "\n")
                                s_line = args.get("start_line")
                                e_line = args.get("end_line")
                                if s_line is not None or e_line is not None:
                                    lines = curr_norm.splitlines(keepends=True)
                                    s_idx = max(0, (s_line - 1)) if s_line else 0
                                    e_idx = min(len(lines), e_line) if e_line else len(lines)
                                    target_slice = "".join(lines[s_idx:e_idx])
                                    new_slice = target_slice.replace(old_norm, new_norm, 1)
                                    simulated = "".join(lines[:s_idx]) + new_slice + "".join(lines[e_idx:])
                                else:
                                    simulated = curr_norm.replace(old_norm, new_norm, 1)
                                diff = unified_diff(args["path"], curr, simulated)
                        elif name == "write_file" and isinstance(args, dict) and "path" in args and "content" in args:
                            full = os.path.abspath(os.path.join(base_dir, args["path"]))
                            curr = ""
                            if os.path.exists(full):
                                try:
                                    with open(full, "r", encoding="utf-8", errors="replace") as f:
                                        curr = f.read()
                                except Exception:
                                    curr = ""
                            diff = unified_diff(args["path"], curr, args["content"])
                    except Exception:
                        diff = None

                    _add_event(state, {
                        "type": "pending_approval",
                        "approval_id": approval_id,
                        "name": name,
                        "args": args,
                        "diff": diff,
                    })
                    done.wait()
                    with state["lock"]:
                        decision = state["pending_decision"].pop(approval_id, False)
                        state["pending"].pop(approval_id, None)
                    return decision

                def _worker():
                    try:
                        result = run_agent(
                            state["root_dir"],
                            task,
                            model=model,
                            on_event=_on_event,
                            messages=state["conversation"],
                            stop_event=stop_event,
                            on_permission=_on_permission,
                            session_id=state["session_id"],
                            sandbox=state.get("sandbox"),
                        )
                        with state["lock"]:
                            state["conversation"] = result.get("messages")
                            state["result"] = {
                                "finished": bool(result.get("finished")),
                                "summary": result.get("summary"),
                            }
                    except Exception as exc:
                        _add_event(state, {"type": "error", "message": f"Server execution error: {exc}"})
                    finally:
                        with state["lock"]:
                            state["running"] = False
                            state["stop_event"] = None

                thread = threading.Thread(target=_worker, daemon=True)
                with state["lock"]:
                    state["thread"] = thread
                thread.start()
                return _json_response(self, 200, {"ok": True})

            if parsed.path == "/api/credentials":
                root = state["root_dir"]
                key = str(data.get("key") or "").strip()
                value = str(data.get("value") or "").strip()
                if not key:
                    return _json_response(self, 400, {"error": "key is required"})
                if value:
                    credentials.set_credential(root, key, value)
                    credentials.apply_credentials(root)
                    llm.invalidate_client()
                    return _json_response(self, 200, {"ok": True, "message": f"{key} saved"})
                else:
                    return _json_response(self, 400, {"error": "value is required"})

            if parsed.path == "/api/credentials/test":
                root = state["root_dir"]
                key = str(data.get("key") or "").strip()
                value = str(data.get("value") or "").strip() or None
                if not key:
                    return _json_response(self, 400, {"error": "key is required"})
                result = credentials.test_credential(root, key, value=value)
                return _json_response(self, 200, result)

            if parsed.path == "/api/credentials/delete":
                root = state["root_dir"]
                key = str(data.get("key") or "").strip()
                if not key:
                    return _json_response(self, 400, {"error": "key is required"})
                deleted = credentials.delete_credential(root, key)
                llm.invalidate_client()
                return _json_response(self, 200, {"ok": True, "deleted": deleted})

            return _json_response(self, 404, {"error": "not found"})

    return Handler


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return
        if isinstance(exc_val, OSError) and getattr(exc_val, "winerror", None) in (10053, 10054):
            return
        super().handle_error(request, client_address)


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(prog="sova-web")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8787, help="Port to bind")
    parser.add_argument("--provider", choices=llm.get_available_providers(), help="LLM provider")
    parser.add_argument("--model", help="Model override")
    args = parser.parse_args()

    if args.provider:
        os.environ["SOVA_PROVIDER"] = args.provider
    if args.model:
        os.environ["SOVA_MODEL"] = args.model

    root_dir = os.getcwd()
    credentials.apply_credentials(root_dir)
    state = _build_state(root_dir)
    handler = make_handler(state)
    server = QuietThreadingHTTPServer((args.host, args.port), handler)
    print(f"Sova web UI running at http://{args.host}:{args.port}")
    print(f"cwd={root_dir}")
    print(f"provider={llm.get_provider()} model={llm.get_model()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
