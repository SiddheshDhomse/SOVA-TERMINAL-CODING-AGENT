"""Web UI for Sova with prompt input, live SSE feed, approvals, diffs, and sessions."""
import argparse
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from . import llm, sessions
from .loop import run_agent


_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sova Control Room</title>
  <style>
    :root {
      --bg0: #0f1823;
      --bg1: #13273a;
      --bg2: #1f3f55;
      --panel: rgba(11, 25, 37, 0.86);
      --line: rgba(156, 193, 219, 0.35);
      --text: #e7f3ff;
      --muted: #95b7d3;
      --brand: #4bd7d1;
      --danger: #ff6e63;
      --ok: #8ce38f;
      --shadow: 0 16px 42px rgba(2, 8, 16, 0.45);
    }

    * { box-sizing: border-box; }

    ::-webkit-scrollbar { width: 10px; height: 10px; }
    ::-webkit-scrollbar-track { background: rgba(20, 40, 60, 0.3); border-radius: 10px; }
    ::-webkit-scrollbar-thumb { background: rgba(75, 215, 209, 0.4); border-radius: 10px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(75, 215, 209, 0.6); }

    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background:
        radial-gradient(1100px 620px at 8% 0%, rgba(65, 151, 190, 0.34), transparent 70%),
        radial-gradient(900px 540px at 95% 95%, rgba(32, 116, 130, 0.32), transparent 72%),
        linear-gradient(135deg, var(--bg0), var(--bg1) 46%, var(--bg2));
      font-family: "Cascadia Code", "Consolas", "Lucida Console", monospace;
      padding: 28px;
    }

    .frame {
      width: min(1360px, 100%);
      margin: 0 auto;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(9, 24, 36, 0.92), rgba(11, 25, 37, 0.78));
      box-shadow: var(--shadow);
      overflow: hidden;
      backdrop-filter: blur(6px);
      display: flex;
      flex-direction: column;
      min-height: 100vh;
    }

    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 14px 20px;
      border-bottom: 1px solid var(--line);
      background: rgba(8, 20, 32, 0.78);
    }

    .brand { font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--brand); }
    .status { color: var(--muted); font-size: 13px; }

    .layout {
      display: grid;
      grid-template-columns: 340px 1fr 260px;
      gap: 0;
      height: calc(100vh - 60px);
      flex: 1;
    }

    .panel {
      border-right: 1px solid var(--line);
      padding: 18px;
      height: 100%;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
    }

    .panel h2 {
      margin: 0 0 10px 0;
      font-size: 15px;
      letter-spacing: 0.04em;
      color: var(--muted);
      text-transform: uppercase;
    }

    label { display: block; margin-bottom: 6px; color: var(--muted); font-size: 12px; letter-spacing: 0.03em; text-transform: uppercase; }

    textarea, select, input {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 12px;
      color: var(--text);
      background: rgba(12, 28, 42, 0.86);
      padding: 12px;
      font: inherit;
      outline: none;
      transition: border-color .2s ease, box-shadow .2s ease;
    }

    textarea:focus, select:focus, input:focus {
      border-color: var(--brand);
      box-shadow: 0 0 0 3px rgba(75, 215, 209, 0.15);
    }

    textarea { min-height: 160px; resize: vertical; line-height: 1.45; }
    .row { margin-bottom: 14px; }
    .actions { display: flex; gap: 10px; margin-top: 14px; flex-wrap: wrap; }

    button {
      border: 0;
      border-radius: 12px;
      padding: 9px 13px;
      font: inherit;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      transition: transform .12s ease, opacity .2s ease;
    }
    button:active { transform: translateY(1px) scale(0.99); }
    button:disabled { opacity: 0.45; cursor: default; }

    .run { background: linear-gradient(140deg, #42e0c4, #2bb7ca); color: #02242f; }
    .stop { background: linear-gradient(140deg, #ff8b76, #ff5e65); color: #320f1a; }
    .ghost { background: rgba(18, 40, 58, 0.92); color: var(--muted); border: 1px solid var(--line); }

    .console { padding: 18px; display: flex; flex-direction: column; gap: 12px; height: 100%; overflow: hidden; }

    .feed {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(8, 20, 30, 0.8);
      flex: 1;
      overflow: auto;
      padding: 14px;
      line-height: 1.4;
      font-size: 13px;
    }

    .line { padding: 6px 8px; border-radius: 8px; margin-bottom: 8px; border: 1px solid transparent; white-space: pre-wrap; }

    .thinking { color: var(--muted); font-style: italic; }
    .tool_call { color: #99d8ff; border-color: rgba(120, 178, 215, 0.35); }
    .tool_result { color: #b9dcf2; }
    .answer { color: var(--ok); border-color: rgba(140, 227, 143, 0.33); }
    .error { color: var(--danger); border-color: rgba(255, 110, 99, 0.28); }
    .system { color: #f3dd90; border-color: rgba(243, 221, 144, 0.3); }
    .todo { border-color: rgba(156, 193, 219, 0.35); }
    .pending_approval { border-color: rgba(255, 189, 99, 0.5); background: rgba(255, 189, 99, 0.08); }

    .diff-block { margin-top: 6px; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; font-size: 12px; }
    .diff-line { padding: 1px 8px; white-space: pre; overflow-x: auto; }
    .diff-add { background: rgba(140, 227, 143, 0.14); color: var(--ok); }
    .diff-del { background: rgba(255, 110, 99, 0.14); color: var(--danger); }
    .diff-hunk { color: var(--brand); }
    .diff-ctx { color: var(--muted); }

    .md h1, .md h2, .md h3 { margin: 6px 0; color: var(--text); }
    .md code { background: rgba(255,255,255,0.08); padding: 1px 5px; border-radius: 5px; }
    .md pre { background: rgba(0,0,0,0.35); padding: 8px 10px; border-radius: 8px; overflow-x: auto; }
    .md ul { margin: 4px 0; padding-left: 20px; }

    .side { border-right: 0; }
    .side-section { margin-bottom: 18px; }
    .side-item {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px 10px;
      margin-bottom: 8px;
      font-size: 12px;
      color: var(--muted);
      cursor: pointer;
    }
    .side-item:hover { border-color: var(--brand); color: var(--text); }
    .side-item .id { color: var(--brand); display: block; margin-bottom: 3px; }
    .empty-hint { color: var(--muted); font-size: 12px; }

    @media (max-width: 1180px) {
      body { padding: 14px; }
      .layout { grid-template-columns: 1fr; }
      .panel, .side { border-right: 0; border-bottom: 1px solid var(--line); }
      .feed { min-height: 420px; }
    }
  </style>
</head>
<body>
  <div class="frame">
    <div class="topbar">
      <div class="brand">Sova Control Room</div>
      <div class="status" id="status">idle</div>
    </div>
    <div class="layout">
      <section class="panel">
        <h2>Prompt</h2>
        <div class="row">
          <label for="task">Task Prompt</label>
          <textarea id="task" placeholder="Describe the coding task for Sova..."></textarea>
        </div>
        <div class="row">
          <label for="provider">Provider</label>
          <select id="provider">
            <option value="">default</option>
            <option value="groq">groq</option>
            <option value="ollama">ollama</option>
            <option value="nvidia">nvidia (Nemotron)</option>
          </select>
        </div>
        <div class="row">
          <label for="model">Model Override</label>
          <input id="model" type="text" placeholder="Optional model name" />
        </div>
        <div class="actions">
          <button class="run" id="runBtn">Run</button>
          <button class="stop" id="stopBtn">Stop Execution</button>
          <button class="ghost" id="newBtn">New Chat</button>
        </div>
        <div class="row" style="margin-top:14px;">
          <label><input type="checkbox" id="autoApprove" style="width:auto;display:inline-block;margin-right:6px;" /> Auto-approve write/edit/shell</label>
        </div>
      </section>
      <section class="console">
        <h2 style="margin:0;font-size:15px;letter-spacing:.04em;color:var(--muted);text-transform:uppercase;">Live Events</h2>
        <div id="feed" class="feed"></div>
      </section>
      <section class="panel side">
        <div class="side-section">
          <h2>Todo</h2>
          <div id="todoBox" class="empty-hint">(no todos yet)</div>
        </div>
        <div class="side-section">
          <h2>Changes</h2>
          <div id="changesBox" class="empty-hint">No files touched yet</div>
        </div>
        <div class="side-section">
          <h2>Sessions</h2>
          <div id="sessionsBox" class="empty-hint">Loading…</div>
        </div>
      </section>
    </div>
  </div>

  <script>
    const feed = document.getElementById("feed");
    const statusEl = document.getElementById("status");
    const todoBox = document.getElementById("todoBox");
    const changesBox = document.getElementById("changesBox");
    const sessionsBox = document.getElementById("sessionsBox");
    let cursor = 0;
    let thinkingEl = null;
    const changedFiles = new Set();

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    }

    function renderMarkdown(text) {
      if (!text) return "";
      const parts = String(text).split(/```(\\w*)\\n([\\s\\S]*?)```/g);
      let html = "";
      for (let i = 0; i < parts.length; i += 3) {
        let chunk = escapeHtml(parts[i] || "");
        chunk = chunk.replace(/^### (.*)$/gm, "<h3>$1</h3>");
        chunk = chunk.replace(/^## (.*)$/gm, "<h2>$1</h2>");
        chunk = chunk.replace(/^# (.*)$/gm, "<h1>$1</h1>");
        chunk = chunk.replace(/\\*\\*(.+?)\\*\\*/g, "<b>$1</b>");
        chunk = chunk.replace(/`([^`]+)`/g, "<code>$1</code>");
        chunk = chunk.replace(/^(?:- (.*)(?:\\n|$))+/gm, (m) => {
          const items = m.trim().split(/\\n/).map((l) => "<li>" + l.replace(/^- /, "") + "</li>").join("");
          return "<ul>" + items + "</ul>";
        });
        chunk = chunk.replace(/\\n/g, "<br/>");
        html += chunk;
        if (i + 1 < parts.length) {
          const lang = parts[i + 1] || "";
          const code = escapeHtml(parts[i + 2] || "");
          html += `<pre><code data-lang="${escapeHtml(lang)}">${code}</code></pre>`;
        }
      }
      return `<div class="md">${html}</div>`;
    }

    function renderDiff(diffText) {
      if (!diffText) return "";
      const lines = diffText.split("\\n").slice(0, 300);
      let rows = "";
      for (const line of lines) {
        let cls = "diff-ctx";
        if (line.startsWith("+++") || line.startsWith("---")) cls = "diff-ctx";
        else if (line.startsWith("+")) cls = "diff-add";
        else if (line.startsWith("-")) cls = "diff-del";
        else if (line.startsWith("@@")) cls = "diff-hunk";
        rows += `<div class="diff-line ${cls}">${escapeHtml(line) || "&nbsp;"}</div>`;
      }
      return `<div class="diff-block">${rows}</div>`;
    }

    function fileFromDiff(diffText) {
      const m = /\\+\\+\\+ b\\/(.+)/.exec(diffText || "");
      return m ? m[1].trim() : null;
    }

    function renderTodo(todos) {
      if (!todos || !todos.length) { todoBox.innerHTML = "(no todos yet)"; return; }
      const marks = {completed: "✔", in_progress: "➤"};
      todoBox.innerHTML = todos.map((t) =>
        `<div>${marks[t.status] || "☐"} ${escapeHtml(t.content || "")}</div>`
      ).join("");
    }

    function renderChanges() {
      if (!changedFiles.size) { changesBox.innerHTML = "No files touched yet"; return; }
      changesBox.innerHTML = [...changedFiles].map((f) => `<div class="side-item">${escapeHtml(f)}</div>`).join("");
    }

    async function post(url, body) {
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "request failed");
      return data;
    }

    function renderApproval(ev) {
      const div = document.createElement("div");
      div.className = "line pending_approval";
      div.innerHTML = `<div>Allow <b>${escapeHtml(ev.name)}</b>(${escapeHtml(JSON.stringify(ev.args || {}))})?</div>`;
      const row = document.createElement("div");
      row.style.marginTop = "8px";
      row.style.display = "flex";
      row.style.gap = "8px";
      const approveBtn = document.createElement("button");
      approveBtn.textContent = "Approve";
      approveBtn.className = "run";
      const denyBtn = document.createElement("button");
      denyBtn.textContent = "Deny";
      denyBtn.className = "stop";
      approveBtn.onclick = async () => { approveBtn.disabled = true; denyBtn.disabled = true; await post("/api/approve", { id: ev.approval_id }); };
      denyBtn.onclick = async () => { approveBtn.disabled = true; denyBtn.disabled = true; await post("/api/deny", { id: ev.approval_id }); };
      row.appendChild(approveBtn);
      row.appendChild(denyBtn);
      div.appendChild(row);
      feed.appendChild(div);
      feed.scrollTop = feed.scrollHeight;
    }

    function appendEvents(events) {
      if (!events || !events.length) return;
      for (const ev of events) {
        const agent = ev.subagent || "main";
        if (ev.type === "thinking") {
          if (!thinkingEl) {
            thinkingEl = document.createElement("div");
            thinkingEl.className = "line thinking";
            feed.appendChild(thinkingEl);
          }
          thinkingEl.textContent = "[" + agent + "] thinking...";
          continue;
        }
        if (ev.type === "thinking_delta") {
          if (!thinkingEl) {
            thinkingEl = document.createElement("div");
            thinkingEl.className = "line thinking";
            feed.appendChild(thinkingEl);
          }
          thinkingEl.textContent = ("[" + agent + "] " + (thinkingEl.dataset.buf || "") + (ev.text || "")).slice(-600);
          thinkingEl.dataset.buf = (thinkingEl.dataset.buf || "") + (ev.text || "");
          feed.scrollTop = feed.scrollHeight;
          continue;
        }
        thinkingEl = null;

        if (ev.type === "pending_approval") { renderApproval(ev); continue; }
        if (ev.type === "todo") { renderTodo(ev.todos); continue; }

        const line = document.createElement("div");
        line.className = "line " + (ev.type || "system");
        if (ev.type === "tool_call") {
          line.textContent = "[" + agent + "] call " + ev.name + " " + JSON.stringify(ev.args || {});
        } else if (ev.type === "tool_result") {
          line.textContent = "[" + agent + "] result " + String(ev.result || "").slice(0, 900);
          if (ev.diff) {
            line.innerHTML += renderDiff(ev.diff);
            const f = fileFromDiff(ev.diff);
            if (f) { changedFiles.add(f); renderChanges(); }
          }
        } else if (ev.type === "answer") {
          line.innerHTML = "[" + agent + "] " + renderMarkdown(ev.text || "");
        } else if (ev.type === "error") {
          line.textContent = "[" + agent + "] ERROR " + (ev.message || "");
        } else {
          line.textContent = JSON.stringify(ev);
        }
        feed.appendChild(line);
      }
      feed.scrollTop = feed.scrollHeight;
    }

    async function loadSessions() {
      try {
        const res = await fetch("/api/sessions");
        const data = await res.json();
        const rows = data.sessions || [];
        if (!rows.length) { sessionsBox.innerHTML = "No saved sessions yet"; return; }
        sessionsBox.innerHTML = "";
        for (const r of rows) {
          const item = document.createElement("div");
          item.className = "side-item";
          item.innerHTML = `<span class="id">${r.finished ? "✔" : "…"} ${escapeHtml(r.id)}</span>${escapeHtml(r.first_message || "")}`;
          item.onclick = async () => {
            await post("/api/resume_session", { id: r.id });
            feed.innerHTML = "";
            cursor = 0;
            thinkingEl = null;
          };
          sessionsBox.appendChild(item);
        }
      } catch (e) {
        sessionsBox.innerHTML = "Could not load sessions";
      }
    }

    function connectStream() {
      const es = new EventSource("/api/stream?cursor=" + cursor);
      es.onmessage = (e) => {
        if (!e.data) return;
        try {
          const data = JSON.parse(e.data);
          statusEl.textContent = data.running ? "running" : "idle";
          appendEvents(data.events || []);
          cursor = data.cursor || cursor;
        } catch (err) { /* ignore malformed chunk */ }
      };
      es.onerror = () => {
        statusEl.textContent = "reconnecting...";
        es.close();
        setTimeout(connectStream, 1500);
      };
    }

    document.getElementById("runBtn").addEventListener("click", async () => {
      const task = document.getElementById("task").value.trim();
      if (!task) return;
      try {
        await post("/api/start", {
          task,
          provider: document.getElementById("provider").value,
          model: document.getElementById("model").value.trim(),
          auto_approve: document.getElementById("autoApprove").checked,
        });
      } catch (e) {
        appendEvents([{ type: "error", message: String(e.message || e) }]);
      }
    });

    document.getElementById("stopBtn").addEventListener("click", async () => {
      try { await post("/api/stop", {}); } catch (e) { appendEvents([{ type: "error", message: String(e.message || e) }]); }
    });

    document.getElementById("newBtn").addEventListener("click", async () => {
      try {
        await post("/api/new", {});
        feed.innerHTML = "";
        cursor = 0;
        thinkingEl = null;
        changedFiles.clear();
        renderChanges();
        renderTodo([]);
        loadSessions();
      } catch (e) {
        appendEvents([{ type: "error", message: String(e.message || e) }]);
      }
    });

    loadSessions();
    connectStream();
  </script>
</body>
</html>
"""


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
        "pending": {},           # approval id -> threading.Event
        "pending_decision": {},  # approval id -> bool
        "auto_approve": False,
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

        def log_message(self, format, *args):  # noqa: A003
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
                    time.sleep(0.35)
            except OSError:
                return

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                return _text_response(self, 200, _PAGE)

            if parsed.path == "/api/stream":
                query = parse_qs(parsed.query)
                try:
                    cursor = int((query.get("cursor") or ["0"])[0])
                except ValueError:
                    cursor = 0
                return self._stream_events(cursor)

            if parsed.path == "/api/state":
                query = parse_qs(parsed.query)
                try:
                    cursor = int((query.get("cursor") or ["0"])[0])
                except ValueError:
                    cursor = 0
                with state["lock"]:
                    events = [e for e in state["events"] if e["id"] > cursor]
                    payload = {
                        "running": state["running"],
                        "cursor": state["next_event"] - 1,
                        "events": events,
                        "result": state["result"],
                    }
                return _json_response(self, 200, payload)

            if parsed.path == "/api/sessions":
                rows = sessions.list_sessions(state["root_dir"])
                return _json_response(self, 200, {"sessions": rows})

            return _json_response(self, 404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            data = self._read_json()
            if data is None:
                return _json_response(self, 400, {"error": "invalid json"})

            if parsed.path == "/api/new":
                with state["lock"]:
                    if state["running"]:
                        return _json_response(self, 409, {"error": "cannot reset while running"})
                    state["conversation"] = None
                    state["events"] = []
                    state["next_event"] = 1
                    state["result"] = None
                    state["session_id"] = sessions.new_session_id()
                return _json_response(self, 200, {"ok": True})

            if parsed.path == "/api/resume_session":
                session_id = str(data.get("id") or "").strip()
                if not session_id:
                    return _json_response(self, 400, {"error": "id is required"})
                with state["lock"]:
                    if state["running"]:
                        return _json_response(self, 409, {"error": "cannot resume while running"})
                    try:
                        state["conversation"] = sessions.load_session(state["root_dir"], session_id)
                    except (OSError, json.JSONDecodeError, KeyError):
                        return _json_response(self, 404, {"error": "session not found"})
                    state["session_id"] = session_id
                    state["events"] = []
                    state["next_event"] = 1
                    state["result"] = None
                _add_event(state, {"type": "system", "message": f"Resumed session {session_id}"})
                return _json_response(self, 200, {"ok": True})

            if parsed.path in ("/api/approve", "/api/deny"):
                approval_id = str(data.get("id") or "")
                with state["lock"]:
                    done = state["pending"].get(approval_id)
                    if done is None:
                        return _json_response(self, 404, {"error": "unknown or already-resolved approval id"})
                    state["pending_decision"][approval_id] = parsed.path.endswith("approve")
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
                        done.set()  # unblock anything waiting on an approval so stop takes effect immediately
                _add_event(state, {"type": "system", "message": "Stop requested"})
                return _json_response(self, 200, {"ok": True})

            if parsed.path == "/api/start":
                task = str(data.get("task") or "").strip()
                provider = str(data.get("provider") or "").strip().lower()
                model = str(data.get("model") or "").strip() or None
                if not task:
                    return _json_response(self, 400, {"error": "task is required"})
                if provider and provider not in ("groq", "ollama", "nvidia"):
                    return _json_response(self, 400, {"error": "provider must be groq, ollama, or nvidia"})

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

                _add_event(state, {
                    "type": "system",
                    "message": f"Started task with provider={llm.get_provider()} model={model or llm.get_model()}",
                })

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
                    _add_event(state, {"type": "pending_approval", "approval_id": approval_id, "name": name, "args": args})
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
                        )
                        with state["lock"]:
                            state["conversation"] = result.get("messages")
                            state["result"] = {
                                "finished": bool(result.get("finished")),
                                "summary": result.get("summary"),
                            }
                    except Exception as exc:
                        _add_event(state, {"type": "error", "message": f"Unhandled server error: {exc}"})
                    finally:
                        with state["lock"]:
                            state["running"] = False
                            state["stop_event"] = None
                        _add_event(state, {"type": "system", "message": "Task ended"})

                thread = threading.Thread(target=_worker, daemon=True)
                with state["lock"]:
                    state["thread"] = thread
                thread.start()
                return _json_response(self, 200, {"ok": True})

            return _json_response(self, 404, {"error": "not found"})

    return Handler


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(prog="sova-web")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8787, help="Port to bind")
    parser.add_argument("--provider", choices=["groq", "ollama", "nvidia"], help="LLM provider")
    parser.add_argument("--model", help="Model override")
    args = parser.parse_args()

    if args.provider:
        os.environ["SOVA_PROVIDER"] = args.provider
    if args.model:
        os.environ["SOVA_MODEL"] = args.model

    root_dir = os.getcwd()
    state = _build_state(root_dir)
    handler = make_handler(state)
    server = ThreadingHTTPServer((args.host, args.port), handler)
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
