"""Web UI for Sova with Claude Code-grade interface, dynamic provider/model dropdowns,
session history, approval modals, diffs, and live logs.
"""
import argparse
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import platform
import sys

from dotenv import load_dotenv

from . import credentials, llm, sessions
from .logger import SessionTrajectoryLogger, get_recent_logs
from .loop import run_agent

_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sova — Coding Agent Harness</title>
  <style>
    :root {
      --bg-canvas: #090a0f;
      --bg-sidebar: #0f111a;
      --bg-panel: #141724;
      --bg-card: #1a1e30;
      --bg-input: #0e1017;
      --border: #24293e;
      --border-subtle: #1c2033;
      --text: #f3f4f6;
      --text-muted: #8e95a5;
      --accent: #38bdf8;
      --accent-glow: rgba(56, 189, 248, 0.15);
      --amber: #f59e0b;
      --amber-glow: rgba(245, 158, 11, 0.15);
      --emerald: #10b981;
      --rose: #f43f5e;
      --diff-add-bg: rgba(16, 185, 129, 0.14);
      --diff-add-text: #34d399;
      --diff-del-bg: rgba(244, 63, 94, 0.14);
      --diff-del-text: #fb7185;
      --font-mono: "Cascadia Code", "JetBrains Mono", Consolas, "Courier New", monospace;
      --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: var(--bg-canvas); }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #4b5563; }

    body {
      background-color: var(--bg-canvas);
      color: var(--text);
      font-family: var(--font-mono);
      height: 100vh;
      overflow: hidden;
      display: flex;
    }

    /* Left Sidebar: Sessions & Navigation */
    .sidebar {
      width: 280px;
      background-color: var(--bg-sidebar);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
    }
    .sidebar-header {
      padding: 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .logo-badge {
      font-weight: 800;
      letter-spacing: 0.08em;
      color: var(--accent);
      font-size: 15px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .logo-badge span { color: var(--text-muted); font-weight: 400; font-size: 11px; text-transform: uppercase; }
    .btn-new-chat {
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 6px 12px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 12px;
      font-family: inherit;
      font-weight: 600;
      transition: all .2s;
    }
    .btn-new-chat:hover {
      border-color: var(--accent);
      color: var(--accent);
      box-shadow: 0 0 10px var(--accent-glow);
    }
    .session-list {
      flex: 1;
      overflow-y: auto;
      padding: 12px 8px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .session-card {
      padding: 10px 12px;
      border-radius: 8px;
      background: transparent;
      border: 1px solid transparent;
      cursor: pointer;
      transition: all .15s ease;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .session-card:hover {
      background: var(--bg-panel);
      border-color: var(--border);
    }
    .session-card.active {
      background: var(--bg-card);
      border-color: var(--accent);
    }
    .session-card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 11px;
      color: var(--text-muted);
    }
    .session-badge {
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 10px;
      font-weight: 600;
    }
    .badge-done { background: rgba(16, 185, 129, 0.2); color: var(--emerald); }
    .badge-open { background: rgba(245, 158, 11, 0.2); color: var(--amber); }
    .session-snippet {
      font-size: 12px;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    /* Main Workspace */
    .main-workspace {
      flex: 1;
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }

    /* Top Control Bar */
    .topbar {
      height: 54px;
      background: var(--bg-panel);
      border-bottom: 1px solid var(--border);
      padding: 0 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-shrink: 0;
    }
    .topbar-left {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .status-pill {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      padding: 4px 10px;
      border-radius: 12px;
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-muted);
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #6b7280;
    }
    .status-dot.running { background: var(--amber); box-shadow: 0 0 8px var(--amber); }
    .status-dot.idle { background: var(--emerald); }

    .topbar-controls {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .ctrl-group {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      color: var(--text-muted);
    }
    select, input[type="text"] {
      background: var(--bg-input);
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 6px;
      padding: 5px 10px;
      font-family: inherit;
      font-size: 12px;
      outline: none;
      transition: border-color .15s;
    }
    select:focus, input[type="text"]:focus {
      border-color: var(--accent);
    }

    /* Central Content Area */
    .content-area {
      flex: 1;
      display: flex;
      overflow: hidden;
    }

    /* Chat & Activity Stream */
    .chat-container {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .chat-feed {
      flex: 1;
      overflow-y: auto;
      padding: 24px 32px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }

    /* Messages & Event Cards */
    .msg-user {
      align-self: flex-end;
      max-width: 80%;
      background: rgba(56, 189, 248, 0.1);
      border: 1px solid rgba(56, 189, 248, 0.3);
      padding: 12px 18px;
      border-radius: 12px;
      font-size: 13px;
      line-height: 1.5;
      white-space: pre-wrap;
      box-shadow: 0 4px 14px rgba(0,0,0,0.2);
    }
    .msg-agent {
      align-self: flex-start;
      max-width: 92%;
      background: var(--bg-panel);
      border: 1px solid var(--border);
      padding: 16px 20px;
      border-radius: 12px;
      font-size: 13px;
      line-height: 1.6;
      box-shadow: 0 4px 14px rgba(0,0,0,0.25);
    }
    .msg-agent-header {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
      color: var(--accent);
      font-weight: 700;
      font-size: 13px;
    }
    .msg-thinking {
      color: var(--text-muted);
      font-style: italic;
      font-size: 12px;
      padding: 8px 14px;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--border-subtle);
      display: flex;
      align-items: center;
      gap: 8px;
    }

    /* Markdown Body in Agent Answer */
    .md-body h1, .md-body h2, .md-body h3 {
      margin: 12px 0 6px 0;
      color: #fff;
    }
    .md-body p { margin-bottom: 8px; }
    .md-body ul { margin: 6px 0 10px 20px; }
    .md-body li { margin-bottom: 4px; }
    .inline-code {
      background: rgba(255,255,255,0.08);
      color: #38bdf8;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 12px;
    }
    .code-block-wrapper {
      background: #090a0f;
      border: 1px solid var(--border);
      border-radius: 8px;
      margin: 10px 0;
      overflow: hidden;
    }
    .code-block-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 6px 12px;
      background: rgba(255,255,255,0.03);
      border-bottom: 1px solid var(--border);
      font-size: 11px;
      color: var(--text-muted);
    }
    .copy-btn {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text-muted);
      padding: 2px 8px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 10px;
      font-family: inherit;
    }
    .copy-btn:hover { color: var(--text); border-color: var(--accent); }
    .code-block-wrapper pre {
      padding: 12px 14px;
      overflow-x: auto;
      font-size: 12px;
      line-height: 1.45;
      color: #e2e8f0;
    }

    /* Tool Cards */
    .tool-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
      font-size: 12px;
      margin-top: 4px;
    }
    .tool-header {
      padding: 8px 14px;
      background: rgba(255,255,255,0.03);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: var(--accent);
      font-weight: 600;
    }
    .tool-body {
      padding: 10px 14px;
      color: #d1d5db;
      white-space: pre-wrap;
      max-height: 250px;
      overflow-y: auto;
      line-height: 1.45;
    }
    /* VS CODE / GITHUB COPILOT DIFF VIEWER */
    .copilot-diff-viewer {
      background: #0d1117;
      border: 1px solid #30363d;
      border-radius: 8px;
      overflow: hidden;
      margin: 8px 0;
      font-family: var(--font-mono);
      font-size: 11.5px;
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35);
    }
    .diff-header {
      background: #161b22;
      border-bottom: 1px solid #30363d;
      padding: 6px 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      user-select: none;
    }
    .diff-header-left {
      display: flex;
      align-items: center;
      gap: 8px;
      overflow: hidden;
      white-space: nowrap;
    }
    .diff-file-icon {
      color: #7d8590;
      font-size: 13px;
    }
    .diff-file-path {
      color: #e6edf3;
      font-weight: 600;
      font-size: 12px;
      text-overflow: ellipsis;
      overflow: hidden;
    }
    .diff-stat-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      font-weight: 700;
      padding: 1px 6px;
      border-radius: 4px;
      background: rgba(255, 255, 255, 0.05);
    }
    .diff-stat-add { color: #3fb950; }
    .diff-stat-del { color: #f85149; }
    .diff-header-right {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-shrink: 0;
    }
    .diff-mode-switcher {
      display: inline-flex;
      background: #21262d;
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 2px;
      gap: 2px;
    }
    .diff-mode-btn {
      background: transparent;
      border: none;
      color: #8b949e;
      font-family: inherit;
      font-size: 11px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 4px;
      cursor: pointer;
      transition: all .15s ease;
    }
    .diff-mode-btn:hover {
      color: #e6edf3;
    }
    .diff-mode-btn.active {
      background: #388bfd;
      color: #ffffff;
    }
    .diff-btn-util {
      background: #21262d;
      border: 1px solid #30363d;
      color: #c9d1d9;
      font-family: inherit;
      font-size: 11px;
      padding: 3px 8px;
      border-radius: 6px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      transition: all .15s ease;
    }
    .diff-btn-util:hover {
      background: #30363d;
      color: #ffffff;
    }
    .diff-body {
      max-height: 440px;
      overflow: auto;
      background: #0d1117;
    }

    /* INLINE VIEW */
    .diff-inline-view {
      display: flex;
      flex-direction: column;
      width: 100%;
    }
    .diff-inline-row {
      display: flex;
      min-height: 20px;
      line-height: 20px;
      border-left: 3px solid transparent;
      width: 100%;
    }
    .diff-inline-row:hover {
      filter: brightness(1.1);
    }
    .diff-inline-row.row-add {
      background: rgba(46, 160, 67, 0.15);
      border-left-color: #2ea043;
    }
    .diff-inline-row.row-del {
      background: rgba(248, 81, 73, 0.15);
      border-left-color: #f85149;
    }
    .diff-inline-row.row-hunk {
      background: rgba(56, 139, 253, 0.1);
      border-left-color: #388bfd;
      color: #79c0ff;
      font-size: 11px;
    }
    .gutter-num {
      width: 42px;
      padding: 0 6px;
      text-align: right;
      color: #6e7681;
      user-select: none;
      flex-shrink: 0;
      border-right: 1px solid rgba(255, 255, 255, 0.05);
      font-size: 11px;
    }
    .row-add .gutter-new { color: #3fb950; }
    .row-del .gutter-old { color: #f85149; }
    .gutter-sign {
      width: 20px;
      text-align: center;
      user-select: none;
      flex-shrink: 0;
      font-weight: 700;
      font-size: 11px;
    }
    .row-add .gutter-sign { color: #3fb950; }
    .row-del .gutter-sign { color: #f85149; }
    .line-code {
      flex: 1;
      padding: 0 8px;
      white-space: pre;
      overflow-x: visible;
      color: #e6edf3;
    }
    .row-add .line-code { color: #aff5b4; }
    .row-del .line-code { color: #ffd8d3; }

    /* SPLIT (SIDE-BY-SIDE) VIEW */
    .diff-split-view {
      display: flex;
      flex-direction: column;
      width: 100%;
      min-width: 600px;
    }
    .diff-split-header {
      display: flex;
      background: #161b22;
      border-bottom: 1px solid #30363d;
      color: #8b949e;
      font-size: 10.5px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .diff-split-header-pane {
      flex: 1;
      padding: 4px 12px;
      border-right: 1px solid #30363d;
    }
    .diff-split-header-pane:last-child {
      border-right: none;
    }
    .diff-split-row {
      display: flex;
      width: 100%;
      min-height: 20px;
      line-height: 20px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.02);
    }
    .diff-split-row:hover {
      filter: brightness(1.1);
    }
    .diff-split-pane {
      flex: 1;
      display: flex;
      overflow: hidden;
      border-right: 1px solid #30363d;
      border-left: 3px solid transparent;
    }
    .diff-split-pane:last-child {
      border-right: none;
    }
    .diff-split-pane.pane-del {
      background: rgba(248, 81, 73, 0.15);
      border-left-color: #f85149;
    }
    .diff-split-pane.pane-add {
      background: rgba(46, 160, 67, 0.15);
      border-left-color: #2ea043;
    }
    .diff-split-pane.pane-empty {
      background: repeating-linear-gradient(45deg, rgba(255,255,255,0.02), rgba(255,255,255,0.02) 6px, transparent 6px, transparent 12px);
      user-select: none;
    }
    .diff-split-pane.pane-hunk {
      background: rgba(56, 139, 253, 0.1);
      border-left-color: #388bfd;
      color: #79c0ff;
    }

    /* PROFESSIONAL PERMISSION CARD (Solves raw JSON dump) */
    .approval-card {
      background: linear-gradient(180deg, rgba(26, 30, 48, 0.95), rgba(18, 21, 33, 0.95));
      border: 1px solid rgba(245, 158, 11, 0.4);
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5), 0 0 20px rgba(245, 158, 11, 0.08);
      border-radius: 12px;
      padding: 18px 20px;
      margin: 12px 0;
      display: flex;
      flex-direction: column;
      gap: 14px;
      animation: fadeIn .2s ease-in-out;
    }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

    .approval-title-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .approval-badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-weight: 700;
      font-size: 13px;
      color: var(--amber);
      letter-spacing: 0.02em;
    }

    .action-preview-meta {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 8px;
      flex-wrap: wrap;
    }
    .action-tag {
      font-size: 11px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .action-tag.write { background: rgba(56, 189, 248, 0.15); color: var(--accent); border: 1px solid rgba(56, 189, 248, 0.3); }
    .action-tag.edit { background: rgba(245, 158, 11, 0.15); color: var(--amber); border: 1px solid rgba(245, 158, 11, 0.3); }
    .action-tag.shell { background: rgba(16, 185, 129, 0.15); color: var(--emerald); border: 1px solid rgba(16, 185, 129, 0.3); }
    .action-tag.general { background: rgba(255, 255, 255, 0.1); color: var(--text); }
    .target-path {
      font-weight: 600;
      color: var(--text);
      font-size: 13px;
    }
    .meta-pill {
      font-size: 11px;
      color: var(--text-muted);
      background: rgba(255, 255, 255, 0.05);
      padding: 2px 8px;
      border-radius: 12px;
    }

    /* Formatted Code Box inside Permission */
    .code-editor-preview {
      display: flex;
      background: #090a0f;
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      max-height: 280px;
      font-size: 12px;
      line-height: 1.5;
    }
    .line-gutter {
      padding: 10px 8px;
      background: rgba(255, 255, 255, 0.02);
      border-right: 1px solid var(--border);
      color: #52525b;
      user-select: none;
      text-align: right;
      font-size: 11px;
      white-space: pre;
    }
    .code-lines {
      padding: 10px 14px;
      flex: 1;
      overflow-x: auto;
      overflow-y: auto;
      white-space: pre;
      margin: 0;
      color: #e4e4e7;
    }
    .terminal-command-preview {
      background: #090a0f;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 13px;
    }
    .term-prompt { color: var(--emerald); font-weight: 700; }
    .term-cmd { color: var(--text); }

    .approval-actions {
      display: flex;
      gap: 10px;
      margin-top: 4px;
    }
    .btn-action-choice {
      font-family: inherit;
      font-size: 12px;
      font-weight: 700;
      padding: 8px 16px;
      border-radius: 8px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all .15s ease;
    }
    .btn-approve {
      background: var(--emerald);
      color: #042f2e;
      border: 1px solid rgba(16, 185, 129, 0.4);
    }
    .btn-approve:hover { filter: brightness(1.1); box-shadow: 0 0 12px rgba(16, 185, 129, 0.3); }
    .btn-always {
      background: var(--accent);
      color: #082f49;
      border: 1px solid rgba(56, 189, 248, 0.4);
    }
    .btn-always:hover { filter: brightness(1.1); box-shadow: 0 0 12px rgba(56, 189, 248, 0.3); }
    .btn-deny {
      background: var(--rose);
      color: #4c0519;
      border: 1px solid rgba(244, 63, 94, 0.4);
    }
    .btn-deny:hover { filter: brightness(1.1); box-shadow: 0 0 12px rgba(244, 63, 94, 0.3); }

    /* Prompt Box (Claude Code-level) */
    .prompt-container {
      padding: 16px 32px 20px;
      background: var(--bg-panel);
      border-top: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .prompt-wrapper {
      position: relative;
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px 14px;
      transition: border-color .2s ease;
    }
    .prompt-wrapper:focus-within {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px var(--accent-glow);
    }
    textarea#taskInput {
      width: 100%;
      min-height: 80px;
      max-height: 240px;
      background: transparent;
      border: 0;
      outline: none;
      color: var(--text);
      font-family: inherit;
      font-size: 13px;
      line-height: 1.5;
      resize: none;
    }
    .prompt-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-top: 6px;
      font-size: 11px;
      color: var(--text-muted);
    }
    .prompt-buttons {
      display: flex;
      gap: 8px;
    }
    button.action-btn {
      font-family: inherit;
      font-size: 12px;
      font-weight: 700;
      padding: 8px 16px;
      border-radius: 8px;
      border: 0;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 6px;
      transition: opacity .15s;
    }
    button.action-btn:disabled { opacity: 0.4; cursor: not-allowed; }
    .btn-run { background: linear-gradient(135deg, #38bdf8, #0ea5e9); color: #082f49; }
    .btn-stop { background: linear-gradient(135deg, #f43f5e, #e11d48); color: #4c0519; }

    /* Right Inspection Drawer */
    .drawer {
      width: 320px;
      background: var(--bg-sidebar);
      border-left: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
    }
    .drawer-tabs {
      display: flex;
      border-bottom: 1px solid var(--border);
    }
    .drawer-tab {
      flex: 1;
      padding: 12px 8px;
      text-align: center;
      font-size: 12px;
      color: var(--text-muted);
      cursor: pointer;
      border-bottom: 2px solid transparent;
      transition: all .2s;
    }
    .drawer-tab.active {
      color: var(--accent);
      border-bottom-color: var(--accent);
      background: var(--bg-panel);
    }
    .drawer-content {
      flex: 1;
      overflow-y: auto;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .todo-item {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      font-size: 12px;
      color: var(--text);
      padding: 4px 0;
    }
    .file-item {
      padding: 6px 10px;
      border-radius: 6px;
      background: var(--bg-panel);
      border: 1px solid var(--border);
      font-size: 12px;
      color: var(--accent);
    }
    .log-box {
      font-family: var(--font-mono);
      font-size: 11px;
      color: #9ca3af;
      line-height: 1.4;
      white-space: pre-wrap;
    }

    /* ── Sidebar Footer ── */
    .sidebar-footer {
      padding: 12px 16px;
      border-top: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .version-badge {
      font-size: 10px;
      color: var(--text-muted);
      background: rgba(255,255,255,0.04);
      padding: 3px 8px;
      border-radius: 6px;
    }
    .btn-settings {
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-muted);
      width: 34px;
      height: 34px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 16px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all .2s;
    }
    .btn-settings:hover {
      border-color: var(--accent);
      color: var(--accent);
      box-shadow: 0 0 12px var(--accent-glow);
      transform: rotate(45deg);
    }

    /* ── Settings Panel Overlay ── */
    .settings-overlay {
      position: fixed;
      inset: 0;
      z-index: 1000;
      display: none;
    }
    .settings-overlay.open { display: flex; }
    .settings-backdrop {
      position: absolute;
      inset: 0;
      background: rgba(0,0,0,0.6);
      backdrop-filter: blur(4px);
    }
    .settings-panel {
      position: absolute;
      right: 0;
      top: 0;
      bottom: 0;
      width: 520px;
      max-width: 90vw;
      background: var(--bg-sidebar);
      border-left: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      box-shadow: -8px 0 30px rgba(0,0,0,0.5);
      transform: translateX(100%);
      transition: transform .3s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .settings-overlay.open .settings-panel {
      transform: translateX(0);
    }
    .settings-header {
      padding: 18px 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .settings-title {
      font-size: 16px;
      font-weight: 700;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .btn-close-settings {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text-muted);
      width: 30px;
      height: 30px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all .15s;
    }
    .btn-close-settings:hover {
      border-color: var(--rose);
      color: var(--rose);
    }
    .settings-tabs {
      display: flex;
      border-bottom: 1px solid var(--border);
      padding: 0 20px;
    }
    .settings-tab {
      padding: 10px 16px;
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
      cursor: pointer;
      border-bottom: 2px solid transparent;
      transition: all .2s;
    }
    .settings-tab:hover { color: var(--text); }
    .settings-tab.active {
      color: var(--accent);
      border-bottom-color: var(--accent);
    }
    .settings-body {
      flex: 1;
      overflow-y: auto;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .settings-section {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .settings-section-title {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-muted);
      padding-bottom: 4px;
      border-bottom: 1px solid var(--border-subtle);
    }

    /* Credential Row */
    .cred-row {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px 16px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      transition: border-color .15s;
    }
    .cred-row:hover { border-color: var(--border); }
    .cred-row-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .cred-label {
      font-size: 13px;
      font-weight: 600;
      color: var(--text);
    }
    .cred-hint {
      font-size: 11px;
      color: var(--text-muted);
    }
    .cred-status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .cred-status-dot.untested { background: #6b7280; }
    .cred-status-dot.ok { background: var(--emerald); box-shadow: 0 0 6px rgba(16,185,129,0.5); }
    .cred-status-dot.fail { background: var(--rose); box-shadow: 0 0 6px rgba(244,63,94,0.5); }
    .cred-input-row {
      display: flex;
      gap: 6px;
      align-items: center;
    }
    .cred-input-wrap {
      flex: 1;
      position: relative;
    }
    .cred-input {
      width: 100%;
      background: var(--bg-input);
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 6px;
      padding: 7px 36px 7px 10px;
      font-family: var(--font-mono);
      font-size: 12px;
      outline: none;
      transition: border-color .15s;
    }
    .cred-input:focus { border-color: var(--accent); }
    .cred-input::placeholder { color: #4b5563; }
    .btn-toggle-vis {
      position: absolute;
      right: 6px;
      top: 50%;
      transform: translateY(-50%);
      background: transparent;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 14px;
      padding: 2px;
    }
    .btn-toggle-vis:hover { color: var(--text); }
    .btn-cred-sm {
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-muted);
      padding: 6px 10px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 11px;
      font-family: inherit;
      font-weight: 600;
      transition: all .15s;
      white-space: nowrap;
    }
    .btn-cred-sm:hover {
      border-color: var(--accent);
      color: var(--accent);
    }
    .btn-cred-sm.save { color: var(--emerald); }
    .btn-cred-sm.save:hover { border-color: var(--emerald); }
    .btn-cred-sm.test { color: var(--accent); }
    .btn-cred-sm.test:hover { border-color: var(--accent); }
    .btn-cred-sm.del { color: var(--rose); }
    .btn-cred-sm.del:hover { border-color: var(--rose); }

    /* Custom Credential Add Form */
    .custom-cred-form {
      background: var(--bg-panel);
      border: 1px dashed var(--border);
      border-radius: 10px;
      padding: 14px 16px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .custom-cred-inputs {
      display: flex;
      gap: 6px;
    }
    .custom-cred-inputs input {
      flex: 1;
      background: var(--bg-input);
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 6px;
      padding: 7px 10px;
      font-family: var(--font-mono);
      font-size: 12px;
      outline: none;
    }
    .custom-cred-inputs input:focus { border-color: var(--accent); }

    /* System Info */
    .sysinfo-grid {
      display: grid;
      grid-template-columns: 140px 1fr;
      gap: 6px 12px;
      font-size: 12px;
    }
    .sysinfo-label {
      color: var(--text-muted);
      font-weight: 600;
    }
    .sysinfo-value {
      color: var(--text);
      word-break: break-all;
    }

    /* Preferences */
    .pref-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 0;
      border-bottom: 1px solid var(--border-subtle);
    }
    .pref-label {
      font-size: 13px;
      color: var(--text);
    }
    .pref-desc {
      font-size: 11px;
      color: var(--text-muted);
    }
    .toggle-switch {
      position: relative;
      width: 40px;
      height: 22px;
      flex-shrink: 0;
    }
    .toggle-switch input {
      opacity: 0;
      width: 0;
      height: 0;
    }
    .toggle-slider {
      position: absolute;
      cursor: pointer;
      inset: 0;
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 22px;
      transition: .3s;
    }
    .toggle-slider::before {
      content: "";
      position: absolute;
      width: 16px;
      height: 16px;
      border-radius: 50%;
      left: 2px;
      bottom: 2px;
      background: var(--text-muted);
      transition: .3s;
    }
    .toggle-switch input:checked + .toggle-slider {
      background: var(--accent);
      border-color: var(--accent);
    }
    .toggle-switch input:checked + .toggle-slider::before {
      transform: translateX(18px);
      background: #fff;
    }

    /* ── Toast Notifications ── */
    .toast-container {
      position: fixed;
      top: 16px;
      right: 16px;
      z-index: 2000;
      display: flex;
      flex-direction: column;
      gap: 8px;
      pointer-events: none;
    }
    .toast {
      pointer-events: auto;
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px 16px;
      font-size: 12px;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 8px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.4);
      animation: toastIn .3s ease-out;
      max-width: 360px;
    }
    .toast.success { border-color: rgba(16,185,129,0.4); }
    .toast.error { border-color: rgba(244,63,94,0.4); }
    .toast.info { border-color: rgba(56,189,248,0.4); }
    @keyframes toastIn { from { opacity: 0; transform: translateX(40px); } to { opacity: 1; transform: translateX(0); } }
    @keyframes toastOut { from { opacity: 1; transform: translateX(0); } to { opacity: 0; transform: translateX(40px); } }

    /* ── Provider Connection Status ── */
    .connection-indicator {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      color: var(--text-muted);
      padding: 4px 10px;
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
    }
    .conn-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: #6b7280;
    }
    .conn-dot.connected { background: var(--emerald); box-shadow: 0 0 4px rgba(16,185,129,0.5); }
    .conn-dot.error { background: var(--rose); }

    /* ── Welcome State ── */
    .welcome-state {
      text-align: center;
      padding: 40px 20px;
      color: var(--text-muted);
    }
    .welcome-logo {
      font-size: 48px;
      font-weight: 900;
      color: var(--accent);
      margin-bottom: 8px;
      letter-spacing: 0.1em;
    }
    .welcome-sub {
      font-size: 14px;
      color: var(--text-muted);
      margin-bottom: 24px;
    }
    .welcome-tips {
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-width: 480px;
      margin: 0 auto;
      text-align: left;
    }
    .welcome-tip {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 14px;
      font-size: 12px;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .welcome-tip-icon {
      font-size: 16px;
      flex-shrink: 0;
    }

    /* ── Session Loading Skeleton ── */
    .skeleton-card {
      padding: 12px;
      border-radius: 8px;
      background: var(--bg-panel);
      margin-bottom: 6px;
      animation: skeletonPulse 1.5s infinite;
    }
    .skeleton-line {
      height: 10px;
      background: var(--border);
      border-radius: 4px;
      margin-bottom: 6px;
    }
    .skeleton-line.short { width: 60%; }
    .skeleton-line.long { width: 90%; }
    @keyframes skeletonPulse { 0%, 100% { opacity: 0.4; } 50% { opacity: 0.7; } }

    /* ── No-Key Warning Banner ── */
    .key-warning {
      background: rgba(245,158,11,0.1);
      border: 1px solid rgba(245,158,11,0.3);
      border-radius: 8px;
      padding: 8px 14px;
      font-size: 12px;
      color: var(--amber);
      display: none;
      align-items: center;
      gap: 8px;
      margin: 0 32px;
    }
    .key-warning.show { display: flex; }
    .key-warning a {
      color: var(--accent);
      cursor: pointer;
      text-decoration: underline;
    }
  </style>
</head>
<body>

  <!-- Left Sidebar -->
  <aside class="sidebar">
    <div class="sidebar-header">
      <div class="logo-badge">SOVA <span>harness</span></div>
      <button class="btn-new-chat" id="btnNewChat">+ New Chat</button>
    </div>
    <div class="session-list" id="sessionList">
      <div class="skeleton-card"><div class="skeleton-line short"></div><div class="skeleton-line long"></div></div>
      <div class="skeleton-card"><div class="skeleton-line short"></div><div class="skeleton-line long"></div></div>
    </div>
    <div class="sidebar-footer">
      <span class="version-badge">SOVA v0.1.0</span>
      <button class="btn-settings" id="btnSettings" title="Settings">⚙</button>
    </div>
  </aside>

  <!-- Main Workspace -->
  <main class="main-workspace">
    <!-- Top Control Bar -->
    <header class="topbar">
      <div class="topbar-left">
        <div class="status-pill">
          <div class="status-dot idle" id="statusDot"></div>
          <span id="statusText">idle</span>
        </div>
        <div style="font-size:11px;color:var(--text-muted);" id="sessionIdDisplay"></div>
      </div>

      <div class="topbar-controls">
        <div class="ctrl-group">
          <label for="providerSelect">Provider</label>
          <select id="providerSelect"></select>
        </div>
        <div class="ctrl-group">
          <label for="modelSelect">Model</label>
          <select id="modelSelect"></select>
          <input type="text" id="customModelInput" placeholder="custom model" style="display:none;width:140px;" />
        </div>
        <div class="ctrl-group">
          <label><input type="checkbox" id="autoApproveCheck" style="margin-right:4px;" /> Auto-approve</label>
        </div>
        <div class="connection-indicator" id="connIndicator">
          <div class="conn-dot" id="connDot"></div>
          <span id="connText">checking...</span>
        </div>
      </div>
    </header>

    <div class="key-warning" id="keyWarning">
      ⚠️ No API key configured for the selected provider. <a onclick="openSettings()">Add one in Settings</a>
    </div>

    <!-- Central Area -->
    <div class="content-area">
      <!-- Chat Stream -->
      <section class="chat-container">
        <div class="chat-feed" id="chatFeed">
          <div class="welcome-state" id="welcomeState">
            <div class="welcome-logo">SOVA</div>
            <div class="welcome-sub">Production Coding Agent Harness</div>
            <div class="welcome-tips">
              <div class="welcome-tip"><span class="welcome-tip-icon">⚡</span> Type a coding task below — inspect, write, edit, or test code in this workspace</div>
              <div class="welcome-tip"><span class="welcome-tip-icon">🔑</span> Configure API keys via the <b>⚙ Settings</b> button in the sidebar</div>
              <div class="welcome-tip"><span class="welcome-tip-icon">🛡️</span> Review file changes with VS Code-grade diffs before approval</div>
              <div class="welcome-tip"><span class="welcome-tip-icon">📋</span> Track progress in the right panel — Todos, Changes, Logs</div>
            </div>
          </div>
        </div>

        <!-- Prompt Input (Claude Code-level) -->
        <div class="prompt-container">
          <div class="prompt-wrapper">
            <textarea id="taskInput" placeholder="Type a task (e.g. 'implement simple calculator GUI in tkinter', 'run tests')..."></textarea>
            <div class="prompt-footer">
              <span>Enter to run &bull; Shift+Enter for newline</span>
              <div class="prompt-buttons">
                <button class="action-btn btn-run" id="btnRun">Run Task ❯</button>
                <button class="action-btn btn-stop" id="btnStop" disabled>Stop</button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- Right Inspection Drawer -->
      <aside class="drawer">
        <div class="drawer-tabs">
          <div class="drawer-tab active" data-tab="todos">Todo</div>
          <div class="drawer-tab" data-tab="changes">Changes</div>
          <div class="drawer-tab" data-tab="logs">Logs</div>
        </div>
        <div class="drawer-content" id="drawerContent">
          <!-- Populated by JS -->
        </div>
      </aside>
    </div>
  </main>

  <!-- Settings Panel Overlay -->
  <div class="settings-overlay" id="settingsOverlay">
    <div class="settings-backdrop" onclick="closeSettings()"></div>
    <div class="settings-panel">
      <div class="settings-header">
        <div class="settings-title">⚙ Settings</div>
        <button class="btn-close-settings" onclick="closeSettings()">✕</button>
      </div>
      <div class="settings-tabs">
        <div class="settings-tab active" data-stab="credentials" onclick="switchSettingsTab('credentials')">🔑 API Keys</div>
        <div class="settings-tab" data-stab="preferences" onclick="switchSettingsTab('preferences')">⚙ Preferences</div>
        <div class="settings-tab" data-stab="sysinfo" onclick="switchSettingsTab('sysinfo')">ℹ System</div>
      </div>
      <div class="settings-body" id="settingsBody">
        <!-- Populated by JS -->
      </div>
    </div>
  </div>

  <!-- Toast Container -->
  <div class="toast-container" id="toastContainer"></div>

  <script>
    const chatFeed = document.getElementById("chatFeed");
    const taskInput = document.getElementById("taskInput");
    const btnRun = document.getElementById("btnRun");
    const btnStop = document.getElementById("btnStop");
    const btnNewChat = document.getElementById("btnNewChat");
    const statusDot = document.getElementById("statusDot");
    const statusText = document.getElementById("statusText");
    const sessionIdDisplay = document.getElementById("sessionIdDisplay");
    const sessionList = document.getElementById("sessionList");
    const providerSelect = document.getElementById("providerSelect");
    const modelSelect = document.getElementById("modelSelect");
    const customModelInput = document.getElementById("customModelInput");
    const autoApproveCheck = document.getElementById("autoApproveCheck");
    const drawerContent = document.getElementById("drawerContent");
    const settingsOverlay = document.getElementById("settingsOverlay");
    const settingsBody = document.getElementById("settingsBody");
    const toastContainer = document.getElementById("toastContainer");
    const connDot = document.getElementById("connDot");
    const connText = document.getElementById("connText");
    const keyWarning = document.getElementById("keyWarning");
    const btnSettings = document.getElementById("btnSettings");

    let currentCursor = 0;
    let thinkingNode = null;
    let currentTab = "todos";
    let catalog = {};
    let latestTodos = [];
    let changedFiles = new Set();
    let currentSettingsTab = "credentials";
    let credentialStatuses = {}; // key -> "untested"|"ok"|"fail"

    // ── Toast Notifications ──
    function showToast(message, type = "info") {
      const icons = { success: "✓", error: "✕", info: "ℹ" };
      const toast = document.createElement("div");
      toast.className = `toast ${type}`;
      toast.innerHTML = `<span>${icons[type] || "ℹ"}</span> ${escapeHtml(message)}`;
      toastContainer.appendChild(toast);
      setTimeout(() => {
        toast.style.animation = "toastOut .3s ease-in forwards";
        setTimeout(() => toast.remove(), 300);
      }, 3500);
    }

    // ── Settings Panel ──
    function openSettings() {
      settingsOverlay.classList.add("open");
      renderSettingsTab();
    }
    function closeSettings() {
      settingsOverlay.classList.remove("open");
    }
    function switchSettingsTab(tab) {
      currentSettingsTab = tab;
      document.querySelectorAll(".settings-tab").forEach(t => {
        t.classList.toggle("active", t.dataset.stab === tab);
      });
      renderSettingsTab();
    }

    btnSettings.onclick = openSettings;

    // Keyboard: Escape closes settings
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && settingsOverlay.classList.contains("open")) {
        closeSettings();
      }
    });

    async function renderSettingsTab() {
      if (currentSettingsTab === "credentials") {
        await renderCredentialsTab();
      } else if (currentSettingsTab === "preferences") {
        renderPreferencesTab();
      } else if (currentSettingsTab === "sysinfo") {
        await renderSysInfoTab();
      }
    }

    // ── Credentials Tab ──
    async function renderCredentialsTab() {
      settingsBody.innerHTML = `<div style="color:var(--text-muted);font-size:12px;">Loading credentials...</div>`;
      try {
        const res = await fetch("/api/credentials");
        const data = await res.json();
        const saved = data.credentials || {};
        const known = data.known_keys || [];
        const custom = data.custom_keys || [];

        let html = `<div class="settings-section"><div class="settings-section-title">LLM Provider Keys</div>`;
        for (const k of known) {
          const val = saved[k.key] || "";
          const statusCls = credentialStatuses[k.key] || "untested";
          html += buildCredRow(k.key, k.label, k.hint, val, statusCls);
        }
        html += `</div>`;

        if (custom.length) {
          html += `<div class="settings-section"><div class="settings-section-title">Custom Credentials</div>`;
          for (const c of custom) {
            const val = saved[c.key] || "";
            const statusCls = credentialStatuses[c.key] || "untested";
            html += buildCredRow(c.key, c.key, "", val, statusCls);
          }
          html += `</div>`;
        }

        html += `
          <div class="settings-section">
            <div class="settings-section-title">Add Custom Credential</div>
            <div class="custom-cred-form">
              <div style="font-size:12px;color:var(--text-muted);">Store any API key, token, or URL — e.g. GITHUB_TOKEN, SLACK_WEBHOOK_URL, DATABASE_URL</div>
              <div class="custom-cred-inputs">
                <input id="customCredKey" placeholder="KEY_NAME" style="flex:0.6;" />
                <input id="customCredVal" placeholder="value" type="password" />
                <button class="btn-cred-sm save" onclick="saveCustomCred()">+ Add</button>
              </div>
            </div>
          </div>
        `;
        settingsBody.innerHTML = html;
      } catch (err) {
        settingsBody.innerHTML = `<div style="color:var(--rose);font-size:12px;">Failed to load credentials: ${escapeHtml(err.message)}</div>`;
      }
    }

    function buildCredRow(key, label, hint, maskedVal, statusCls) {
      return `
        <div class="cred-row" id="cred_${key}">
          <div class="cred-row-top">
            <div>
              <div class="cred-label">${escapeHtml(label)}</div>
              ${hint ? `<div class="cred-hint">${escapeHtml(hint)}</div>` : ""}
            </div>
            <div class="cred-status-dot ${statusCls}" title="${statusCls}"></div>
          </div>
          <div class="cred-input-row">
            <div class="cred-input-wrap">
              <input class="cred-input" type="password" id="inp_${key}" placeholder="${maskedVal ? '(saved) ' + maskedVal : 'Enter value...'}" />
              <button class="btn-toggle-vis" onclick="toggleCredVis('inp_${key}')">👁</button>
            </div>
            <button class="btn-cred-sm save" onclick="saveCred('${key}')">Save</button>
            <button class="btn-cred-sm test" onclick="testCred('${key}')">Test</button>
            ${maskedVal ? `<button class="btn-cred-sm del" onclick="deleteCred('${key}')">✕</button>` : ""}
          </div>
        </div>
      `;
    }

    function toggleCredVis(inputId) {
      const inp = document.getElementById(inputId);
      if (!inp) return;
      inp.type = inp.type === "password" ? "text" : "password";
    }

    async function saveCred(key) {
      const inp = document.getElementById(`inp_${key}`);
      const value = inp ? inp.value.trim() : "";
      if (!value) { showToast("Please enter a value", "error"); return; }
      try {
        const res = await fetch("/api/credentials", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key, value })
        });
        if (!res.ok) throw new Error((await res.json()).error);
        credentialStatuses[key] = "untested";
        showToast(`${key} saved successfully`, "success");
        await renderCredentialsTab();
        checkProviderConnection();
      } catch (err) {
        showToast(`Failed to save: ${err.message}`, "error");
      }
    }

    async function testCred(key) {
      showToast(`Testing ${key}...`, "info");
      try {
        const res = await fetch("/api/credentials/test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key })
        });
        const data = await res.json();
        credentialStatuses[key] = data.ok ? "ok" : "fail";
        showToast(data.message || (data.ok ? "Connection OK!" : "Connection failed"), data.ok ? "success" : "error");
        await renderCredentialsTab();
      } catch (err) {
        credentialStatuses[key] = "fail";
        showToast(`Test failed: ${err.message}`, "error");
        await renderCredentialsTab();
      }
    }

    async function deleteCred(key) {
      try {
        const res = await fetch("/api/credentials", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key })
        });
        if (!res.ok) throw new Error((await res.json()).error);
        delete credentialStatuses[key];
        showToast(`${key} deleted`, "success");
        await renderCredentialsTab();
        checkProviderConnection();
      } catch (err) {
        showToast(`Failed to delete: ${err.message}`, "error");
      }
    }

    async function saveCustomCred() {
      const keyInp = document.getElementById("customCredKey");
      const valInp = document.getElementById("customCredVal");
      const key = (keyInp ? keyInp.value.trim() : "").toUpperCase().replace(/[^A-Z0-9_]/g, "_");
      const value = valInp ? valInp.value.trim() : "";
      if (!key || !value) { showToast("Both key name and value are required", "error"); return; }
      try {
        const res = await fetch("/api/credentials", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key, value })
        });
        if (!res.ok) throw new Error((await res.json()).error);
        showToast(`${key} saved successfully`, "success");
        await renderCredentialsTab();
      } catch (err) {
        showToast(`Failed to save: ${err.message}`, "error");
      }
    }

    // ── Preferences Tab ──
    function renderPreferencesTab() {
      const currentProvider = providerSelect.value;
      const currentModel = modelSelect.value;
      const autoApprove = autoApproveCheck.checked;
      settingsBody.innerHTML = `
        <div class="settings-section">
          <div class="settings-section-title">Defaults</div>
          <div class="pref-row">
            <div>
              <div class="pref-label">Default Provider</div>
              <div class="pref-desc">Currently: ${escapeHtml(currentProvider)}</div>
            </div>
            <span style="font-size:12px;color:var(--text-muted);">Use topbar selector</span>
          </div>
          <div class="pref-row">
            <div>
              <div class="pref-label">Default Model</div>
              <div class="pref-desc">Currently: ${escapeHtml(currentModel)}</div>
            </div>
            <span style="font-size:12px;color:var(--text-muted);">Use topbar selector</span>
          </div>
          <div class="pref-row">
            <div>
              <div class="pref-label">Auto-approve Actions</div>
              <div class="pref-desc">Skip permission prompts for file edits and shell commands</div>
            </div>
            <label class="toggle-switch">
              <input type="checkbox" id="prefAutoApprove" ${autoApprove ? "checked" : ""} onchange="autoApproveCheck.checked = this.checked;" />
              <span class="toggle-slider"></span>
            </label>
          </div>
        </div>
        <div class="settings-section">
          <div class="settings-section-title">Theme (Coming Soon)</div>
          <div class="pref-row" style="opacity:0.5;">
            <div>
              <div class="pref-label">Dark Mode</div>
              <div class="pref-desc">Currently the only available theme</div>
            </div>
            <label class="toggle-switch">
              <input type="checkbox" checked disabled />
              <span class="toggle-slider"></span>
            </label>
          </div>
        </div>
      `;
    }

    // ── System Info Tab ──
    async function renderSysInfoTab() {
      settingsBody.innerHTML = `<div style="color:var(--text-muted);font-size:12px;">Loading system info...</div>`;
      try {
        const res = await fetch("/api/system-info");
        const info = await res.json();
        settingsBody.innerHTML = `
          <div class="settings-section">
            <div class="settings-section-title">Runtime</div>
            <div class="sysinfo-grid">
              <span class="sysinfo-label">Python</span><span class="sysinfo-value">${escapeHtml(info.python_version)}</span>
              <span class="sysinfo-label">Platform</span><span class="sysinfo-value">${escapeHtml(info.platform)}</span>
              <span class="sysinfo-label">Workspace</span><span class="sysinfo-value">${escapeHtml(info.workspace)}</span>
            </div>
          </div>
          <div class="settings-section">
            <div class="settings-section-title">Agent</div>
            <div class="sysinfo-grid">
              <span class="sysinfo-label">Active Provider</span><span class="sysinfo-value">${escapeHtml(info.provider)}</span>
              <span class="sysinfo-label">Active Model</span><span class="sysinfo-value">${escapeHtml(info.model)}</span>
              <span class="sysinfo-label">Saved Credentials</span><span class="sysinfo-value">${info.credentials_count} keys</span>
              <span class="sysinfo-label">Session ID</span><span class="sysinfo-value">${escapeHtml(info.session_id)}</span>
            </div>
          </div>
        `;
      } catch (err) {
        settingsBody.innerHTML = `<div style="color:var(--rose);font-size:12px;">Failed to load: ${escapeHtml(err.message)}</div>`;
      }
    }

    // ── Connection Status Check ──
    async function checkProviderConnection() {
      const provider = providerSelect.value;
      const keyMap = { groq: "GROQ_API_KEY", openai: "OPENAI_API_KEY", nvidia: "NVIDIA_API_KEY", ollama: "SOVA_OLLAMA_HOST" };
      const neededKey = keyMap[provider];
      try {
        const res = await fetch("/api/credentials");
        const data = await res.json();
        const saved = data.credentials || {};
        if (provider === "ollama") {
          connDot.className = "conn-dot connected";
          connText.textContent = "ollama (local)";
          keyWarning.classList.remove("show");
        } else if (neededKey && saved[neededKey]) {
          connDot.className = "conn-dot connected";
          connText.textContent = provider + " configured";
          keyWarning.classList.remove("show");
        } else {
          connDot.className = "conn-dot error";
          connText.textContent = "no key";
          keyWarning.classList.add("show");
        }
      } catch {
        connDot.className = "conn-dot";
        connText.textContent = "unknown";
      }
    }

    providerSelect.addEventListener("change", () => {
      updateModelDropdown();
      checkProviderConnection();
    });

    function escapeHtml(s) {
      return String(s || "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
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
        chunk = chunk.replace(/`([^`]+)`/g, "<code class='inline-code'>$1</code>");
        chunk = chunk.replace(/^(?:- (.*)(?:\\n|$))+/gm, (m) => {
          const items = m.trim().split(/\\n/).map((l) => "<li>" + l.replace(/^- /, "") + "</li>").join("");
          return "<ul>" + items + "</ul>";
        });
        chunk = chunk.replace(/\\n/g, "<br/>");
        html += chunk;
        if (i + 1 < parts.length) {
          const lang = parts[i + 1] || "code";
          const code = escapeHtml(parts[i + 2] || "");
          html += `
            <div class="code-block-wrapper">
              <div class="code-block-header">
                <span>${lang}</span>
                <button class="copy-btn" onclick="navigator.clipboard.writeText(this.closest('.code-block-wrapper').querySelector('code').innerText); this.innerText='Copied!'; setTimeout(()=>this.innerText='Copy', 1500);">Copy</button>
              </div>
              <pre><code>${code}</code></pre>
            </div>
          `;
        }
      }
      return `<div class="md-body">${html}</div>`;
    }

    let diffViewerCounter = 0;
    const diffTextStore = new Map();

    function parseUnifiedDiff(diffText) {
      if (!diffText) return null;
      const rawLines = diffText.split("\\n");
      let fromFile = "";
      let toFile = "";
      const hunks = [];
      let currentHunk = null;
      let adds = 0;
      let dels = 0;

      for (let i = 0; i < rawLines.length; i++) {
        const line = rawLines[i];
        if (line.startsWith("--- ")) {
          fromFile = line.slice(4).trim().replace(/^a\\//, "");
        } else if (line.startsWith("+++ ")) {
          toFile = line.slice(4).trim().replace(/^b\\//, "");
        } else if (line.startsWith("@@")) {
          const match = line.match(/^@@ -(\\d+)(?:,\\d+)? \\+(\\d+)(?:,\\d+)? @@(.*)$/);
          let oldStart = 1, newStart = 1, heading = "";
          if (match) {
            oldStart = parseInt(match[1], 10);
            newStart = parseInt(match[2], 10);
            heading = match[3] ? match[3].trim() : "";
          }
          currentHunk = {
            header: line,
            heading: heading,
            oldStart: oldStart,
            newStart: newStart,
            lines: []
          };
          hunks.push(currentHunk);
        } else if (currentHunk) {
          if (line.startsWith("+")) {
            currentHunk.lines.push({ type: "add", text: line.slice(1) });
            adds++;
          } else if (line.startsWith("-")) {
            currentHunk.lines.push({ type: "del", text: line.slice(1) });
            dels++;
          } else if (line.startsWith(" ") || line === "") {
            currentHunk.lines.push({ type: "ctx", text: line.startsWith(" ") ? line.slice(1) : line });
          } else if (line.startsWith("\\\\ No newline")) {
            // skip warning line
          } else {
            currentHunk.lines.push({ type: "ctx", text: line });
          }
        }
      }

      for (const hunk of hunks) {
        let curOld = hunk.oldStart;
        let curNew = hunk.newStart;
        for (const item of hunk.lines) {
          if (item.type === "ctx") {
            item.oldNum = curOld++;
            item.newNum = curNew++;
          } else if (item.type === "del") {
            item.oldNum = curOld++;
            item.newNum = null;
          } else if (item.type === "add") {
            item.oldNum = null;
            item.newNum = curNew++;
          }
        }
      }

      return {
        filePath: toFile || fromFile || "",
        adds: adds,
        dels: dels,
        hunks: hunks
      };
    }

    function buildSplitRows(hunkLines) {
      const rows = [];
      let i = 0;
      while (i < hunkLines.length) {
        const item = hunkLines[i];
        if (item.type === "ctx") {
          rows.push({
            left: { num: item.oldNum, text: item.text, type: "ctx" },
            right: { num: item.newNum, text: item.text, type: "ctx" }
          });
          i++;
        } else {
          const delGroup = [];
          const addGroup = [];
          while (i < hunkLines.length && (hunkLines[i].type === "del" || hunkLines[i].type === "add")) {
            if (hunkLines[i].type === "del") delGroup.push(hunkLines[i]);
            else addGroup.push(hunkLines[i]);
            i++;
          }
          const maxLen = Math.max(delGroup.length, addGroup.length);
          for (let k = 0; k < maxLen; k++) {
            const d = delGroup[k] || null;
            const a = addGroup[k] || null;
            rows.push({
              left: d ? { num: d.oldNum, text: d.text, type: "del" } : null,
              right: a ? { num: a.newNum, text: a.text, type: "add" } : null
            });
          }
        }
      }
      return rows;
    }

    function renderCopilotDiff(diffText, preferredPath) {
      if (!diffText) return "";
      diffViewerCounter++;
      const viewerId = `diff_v_${diffViewerCounter}`;
      diffTextStore.set(viewerId, diffText);

      const parsed = parseUnifiedDiff(diffText);
      const filePath = preferredPath || (parsed && parsed.filePath) || "modified file";
      const adds = parsed ? parsed.adds : 0;
      const dels = parsed ? parsed.dels : 0;

      if (!parsed || !parsed.hunks.length) {
        const lines = diffText.split("\\n").slice(0, 300);
        let fallbackLines = "";
        for (const line of lines) {
          let cls = "row-ctx";
          let sign = " ";
          if (line.startsWith("+")) { cls = "row-add"; sign = "+"; }
          else if (line.startsWith("-")) { cls = "row-del"; sign = "-"; }
          else if (line.startsWith("@@")) { cls = "row-hunk"; sign = "@"; }
          fallbackLines += `
            <div class="diff-inline-row ${cls}">
              <div class="gutter-num gutter-old"></div>
              <div class="gutter-num gutter-new"></div>
              <div class="gutter-sign">${sign}</div>
              <div class="line-code">${escapeHtml(line.slice(1)) || "&nbsp;"}</div>
            </div>`;
        }
        return `
          <div class="copilot-diff-viewer" id="${viewerId}">
            <div class="diff-header">
              <div class="diff-header-left">
                <span class="diff-file-icon">📄</span>
                <span class="diff-file-path">${escapeHtml(filePath)}</span>
              </div>
            </div>
            <div class="diff-body" id="${viewerId}_body">
              <div class="diff-inline-view">${fallbackLines}</div>
            </div>
          </div>
        `;
      }

      let inlineHtml = "";
      for (const hunk of parsed.hunks) {
        inlineHtml += `
          <div class="diff-inline-row row-hunk">
            <div class="gutter-num gutter-old">...</div>
            <div class="gutter-num gutter-new">...</div>
            <div class="gutter-sign">@@</div>
            <div class="line-code">${escapeHtml(hunk.header)}</div>
          </div>`;
        for (const item of hunk.lines) {
          const cls = item.type === "add" ? "row-add" : (item.type === "del" ? "row-del" : "row-ctx");
          const sign = item.type === "add" ? "+" : (item.type === "del" ? "-" : " ");
          const oldStr = item.oldNum !== null ? item.oldNum : "";
          const newStr = item.newNum !== null ? item.newNum : "";
          inlineHtml += `
            <div class="diff-inline-row ${cls}">
              <div class="gutter-num gutter-old">${oldStr}</div>
              <div class="gutter-num gutter-new">${newStr}</div>
              <div class="gutter-sign">${sign}</div>
              <div class="line-code">${escapeHtml(item.text) || "&nbsp;"}</div>
            </div>`;
        }
      }

      let splitHtml = `
        <div class="diff-split-header">
          <div class="diff-split-header-pane">Original (a/${escapeHtml(filePath)})</div>
          <div class="diff-split-header-pane">Modified (b/${escapeHtml(filePath)})</div>
        </div>
      `;
      for (const hunk of parsed.hunks) {
        splitHtml += `
          <div class="diff-split-row">
            <div class="diff-split-pane pane-hunk" style="width:100%;flex:2;">
              <div class="gutter-sign" style="width:30px;">@@</div>
              <div class="line-code">${escapeHtml(hunk.header)}</div>
            </div>
          </div>`;
        const splitRows = buildSplitRows(hunk.lines);
        for (const row of splitRows) {
          let leftContent = "";
          let leftCls = "";
          if (row.left) {
            leftCls = row.left.type === "del" ? "pane-del" : "";
            const lSign = row.left.type === "del" ? "-" : " ";
            leftContent = `
              <div class="gutter-num gutter-old">${row.left.num}</div>
              <div class="gutter-sign">${lSign}</div>
              <div class="line-code">${escapeHtml(row.left.text) || "&nbsp;"}</div>
            `;
          } else {
            leftCls = "pane-empty";
            leftContent = `<div class="gutter-num"></div><div class="gutter-sign"></div><div class="line-code">&nbsp;</div>`;
          }

          let rightContent = "";
          let rightCls = "";
          if (row.right) {
            rightCls = row.right.type === "add" ? "pane-add" : "";
            const rSign = row.right.type === "add" ? "+" : " ";
            rightContent = `
              <div class="gutter-num gutter-new">${row.right.num}</div>
              <div class="gutter-sign">${rSign}</div>
              <div class="line-code">${escapeHtml(row.right.text) || "&nbsp;"}</div>
            `;
          } else {
            rightCls = "pane-empty";
            rightContent = `<div class="gutter-num"></div><div class="gutter-sign"></div><div class="line-code">&nbsp;</div>`;
          }

          splitHtml += `
            <div class="diff-split-row">
              <div class="diff-split-pane ${leftCls}">${leftContent}</div>
              <div class="diff-split-pane ${rightCls}">${rightContent}</div>
            </div>
          `;
        }
      }

      return `
        <div class="copilot-diff-viewer" id="${viewerId}">
          <div class="diff-header">
            <div class="diff-header-left">
              <span class="diff-file-icon">📄</span>
              <span class="diff-file-path" title="${escapeHtml(filePath)}">${escapeHtml(filePath)}</span>
              <span class="diff-stat-badge">
                <span class="diff-stat-add">+${adds}</span>
                <span class="diff-stat-del">-${dels}</span>
              </span>
            </div>
            <div class="diff-header-right">
              <div class="diff-mode-switcher">
                <button class="diff-mode-btn active" id="${viewerId}_btn_inline" onclick="switchDiffMode('${viewerId}', 'inline')">▤ Inline</button>
                <button class="diff-mode-btn" id="${viewerId}_btn_split" onclick="switchDiffMode('${viewerId}', 'split')">◫ Split</button>
              </div>
              <button class="diff-btn-util" onclick="copyDiffViewerText(this, '${viewerId}')">📋 Copy</button>
              <button class="diff-btn-util" id="${viewerId}_collapse_btn" onclick="toggleDiffCollapse('${viewerId}')">▾</button>
            </div>
          </div>
          <div class="diff-body" id="${viewerId}_body">
            <div class="diff-inline-view" id="${viewerId}_inline">${inlineHtml}</div>
            <div class="diff-split-view" id="${viewerId}_split" style="display:none;">${splitHtml}</div>
          </div>
        </div>
      `;
    }

    function switchDiffMode(viewerId, mode) {
      const inlineEl = document.getElementById(`${viewerId}_inline`);
      const splitEl = document.getElementById(`${viewerId}_split`);
      const btnInline = document.getElementById(`${viewerId}_btn_inline`);
      const btnSplit = document.getElementById(`${viewerId}_btn_split`);
      if (!inlineEl || !splitEl) return;

      if (mode === "split") {
        inlineEl.style.display = "none";
        splitEl.style.display = "flex";
        if (btnSplit) btnSplit.classList.add("active");
        if (btnInline) btnInline.classList.remove("active");
      } else {
        splitEl.style.display = "none";
        inlineEl.style.display = "flex";
        if (btnInline) btnInline.classList.add("active");
        if (btnSplit) btnSplit.classList.remove("active");
      }
    }

    function toggleDiffCollapse(viewerId) {
      const body = document.getElementById(`${viewerId}_body`);
      const btn = document.getElementById(`${viewerId}_collapse_btn`);
      if (!body) return;
      if (body.style.display === "none") {
        body.style.display = "block";
        if (btn) btn.textContent = "▾";
      } else {
        body.style.display = "none";
        if (btn) btn.textContent = "▸";
      }
    }

    function copyDiffViewerText(btn, viewerId) {
      const text = diffTextStore.get(viewerId) || "";
      if (navigator.clipboard) {
        navigator.clipboard.writeText(text);
      }
      const orig = btn.innerHTML;
      btn.innerHTML = "✓ Copied!";
      setTimeout(() => { btn.innerHTML = orig; }, 1500);
    }

    function renderDiff(diffText, preferredPath) {
      return renderCopilotDiff(diffText, preferredPath);
    }

    async function post(url, body) {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {})
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Request failed");
      return data;
    }

    // Load Providers and Models
    async function initCatalog() {
      try {
        const res = await fetch("/api/providers-models");
        const data = await res.json();
        catalog = data.catalog || {};

        providerSelect.innerHTML = "";
        for (const p of data.providers) {
          const opt = document.createElement("option");
          opt.value = p;
          opt.textContent = catalog[p]?.name || p;
          if (p === data.current_provider) opt.selected = true;
          providerSelect.appendChild(opt);
        }

        updateModelDropdown();
      } catch (err) {
        console.error("Failed to load catalog:", err);
      }
    }

    function updateModelDropdown() {
      const p = providerSelect.value;
      const conf = catalog[p] || {};
      const models = conf.models || [];
      modelSelect.innerHTML = "";

      for (const m of models) {
        const opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m;
        modelSelect.appendChild(opt);
      }
      const customOpt = document.createElement("option");
      customOpt.value = "__custom__";
      customOpt.textContent = "Custom...";
      modelSelect.appendChild(customOpt);

      customModelInput.style.display = modelSelect.value === "__custom__" ? "inline-block" : "none";
    }

    modelSelect.addEventListener("change", () => {
      customModelInput.style.display = modelSelect.value === "__custom__" ? "inline-block" : "none";
    });

    // Session Management
    async function loadSessions() {
      try {
        const res = await fetch("/api/sessions");
        const data = await res.json();
        const rows = data.sessions || [];
        sessionList.innerHTML = "";
        if (!rows.length) {
          sessionList.innerHTML = `<div style="padding:10px;font-size:12px;color:var(--text-muted);">No saved sessions yet</div>`;
          return;
        }
        for (const r of rows) {
          const card = document.createElement("div");
          card.className = "session-card";
          const badgeCls = r.finished ? "badge-done" : "badge-open";
          const badgeText = r.finished ? "Done" : "Open";
          card.innerHTML = `
            <div class="session-card-header">
              <span>${escapeHtml(r.id.slice(0, 15))}</span>
              <span class="session-badge ${badgeCls}">${badgeText}</span>
            </div>
            <div class="session-snippet">${escapeHtml(r.first_message || "(empty)")}</div>
          `;
          card.onclick = () => resumeSession(r.id);
          sessionList.appendChild(card);
        }
      } catch (e) {
        sessionList.innerHTML = `<div style="padding:10px;font-size:12px;color:var(--rose);">Failed to load sessions</div>`;
      }
    }

    async function resumeSession(id) {
      try {
        await post("/api/resume_session", { id });
        chatFeed.innerHTML = "";
        currentCursor = 0;
        thinkingNode = null;
        changedFiles.clear();
        latestTodos = [];
        updateDrawer();
        sessionIdDisplay.textContent = id;
      } catch (err) {
        alert("Could not resume session: " + err.message);
      }
    }

    // Professional Action Preview Builder (Fixes raw JSON dump)
    // Professional Action Preview Builder with Copilot Diff
    function renderActionPreview(name, args, diff) {
      args = args || {};
      const path = args.path || "";
      if (name === "write_file") {
        const content = String(args.content || "");
        let diffToRender = diff;
        if (!diffToRender) {
          const lines = content.split("\\n");
          diffToRender = `--- /dev/null\\n+++ b/${path || 'new_file'}\\n@@ -0,0 +1,${lines.length} @@\\n` +
            lines.map(l => "+" + l).join("\\n");
        }
        return `
          <div class="action-preview-meta">
            <span class="action-tag write">📄 Write File</span>
            <span class="target-path">${escapeHtml(path)}</span>
          </div>
          ${renderCopilotDiff(diffToRender, path)}
        `;
      } else if (name === "edit_file") {
        const oldStr = String(args.old_str || "");
        const newStr = String(args.new_str || "");
        const rangeText = (args.start_line || args.end_line) ? `Lines ${args.start_line || 1} - ${args.end_line || 'end'}` : "Exact match";

        let diffToRender = diff;
        if (!diffToRender) {
          diffToRender = `--- a/${path || 'file'}\\n+++ b/${path || 'file'}\\n@@ -1,1 +1,1 @@\\n` +
            (oldStr ? oldStr.split("\\n").map(l => "-" + l).join("\\n") + "\\n" : "") +
            (newStr ? newStr.split("\\n").map(l => "+" + l).join("\\n") : "");
        }

        return `
          <div class="action-preview-meta">
            <span class="action-tag edit">✏️ Edit File</span>
            <span class="target-path">${escapeHtml(path)}</span>
            <span class="meta-pill">${escapeHtml(rangeText)}</span>
          </div>
          ${renderCopilotDiff(diffToRender, path)}
        `;
      } else if (name === "run_shell") {
        const cmd = args.command || "";
        return `
          <div class="action-preview-meta">
            <span class="action-tag shell">⚡ Shell Command</span>
          </div>
          <div class="terminal-command-preview">
            <span class="term-prompt">$</span> <span class="term-cmd">${escapeHtml(cmd)}</span>
          </div>
        `;
      } else {
        return `
          <div class="action-preview-meta">
            <span class="action-tag general">Action: ${escapeHtml(name)}</span>
          </div>
          <div class="code-editor-preview">
            <pre class="code-lines" style="padding:10px;"><code>${escapeHtml(JSON.stringify(args, null, 2))}</code></pre>
          </div>
        `;
      }
    }

    // Event Streaming & Rendering
    function appendEvents(events) {
      if (!events || !events.length) return;
      for (const ev of events) {
        const agent = ev.subagent ? `[${ev.subagent}] ` : "";

        if (ev.type === "thinking") {
          if (!thinkingNode) {
            thinkingNode = document.createElement("div");
            thinkingNode.className = "msg-thinking";
            chatFeed.appendChild(thinkingNode);
          }
          thinkingNode.textContent = `${agent}Thinking...`;
          continue;
        }
        if (ev.type === "thinking_delta") {
          if (!thinkingNode) {
            thinkingNode = document.createElement("div");
            thinkingNode.className = "msg-thinking";
            chatFeed.appendChild(thinkingNode);
          }
          thinkingNode.dataset.buf = (thinkingNode.dataset.buf || "") + (ev.text || "");
          thinkingNode.textContent = `${agent}` + thinkingNode.dataset.buf.slice(-300);
          chatFeed.scrollTop = chatFeed.scrollHeight;
          continue;
        }
        thinkingNode = null;

        if (ev.type === "pending_approval") {
          renderApproval(ev);
          continue;
        }
        if (ev.type === "todo") {
          latestTodos = ev.todos || [];
          updateDrawer();
          continue;
        }

        if (ev.type === "tool_call") {
          const card = document.createElement("div");
          card.className = "tool-card";
          const toolArgs = ev.args || {};
          let title = `⟳ ${escapeHtml(agent)}${escapeHtml(ev.name)}`;
          if (ev.name === "write_file") title += `: ${escapeHtml(toolArgs.path || "")} (${(toolArgs.content || "").length} chars)`;
          else if (ev.name === "edit_file") title += `: ${escapeHtml(toolArgs.path || "")}`;
          else if (ev.name === "read_file") title += `: ${escapeHtml(toolArgs.path || "")}`;
          else if (ev.name === "run_shell") title += `: $ ${escapeHtml(toolArgs.command || "")}`;

          card.innerHTML = `
            <div class="tool-header">${title}</div>
          `;
          chatFeed.appendChild(card);
        } else if (ev.type === "tool_result") {
          const card = document.createElement("div");
          card.className = "tool-card";
          let diffHtml = "";
          if (ev.diff) {
            const m = /\\+\\+\\+ b\\/(.+)/.exec(ev.diff);
            const targetPath = m ? m[1].trim() : "";
            if (targetPath) changedFiles.add(targetPath);
            updateDrawer();
            diffHtml = renderCopilotDiff(ev.diff, targetPath);
          }
          card.innerHTML = `
            <div class="tool-header" style="color:var(--text-muted)">✓ ${escapeHtml(agent)}${escapeHtml(ev.name)} result</div>
            <div class="tool-body">${escapeHtml(String(ev.result || "").slice(0, 800))}</div>
            ${diffHtml}
          `;
          chatFeed.appendChild(card);
        } else if (ev.type === "answer") {
          const div = document.createElement("div");
          div.className = "msg-agent";
          div.innerHTML = `
            <div class="msg-agent-header">
              <span style="font-size:16px;">✦</span> Sova ${escapeHtml(agent)}
            </div>
            ${renderMarkdown(ev.text || "")}
          `;
          chatFeed.appendChild(div);
        } else if (ev.type === "error") {
          const div = document.createElement("div");
          div.className = "tool-card";
          div.style.borderColor = "var(--rose)";
          div.innerHTML = `<div class="tool-header" style="color:var(--rose)">[ERROR]</div><div class="tool-body">${escapeHtml(ev.message || "")}</div>`;
          chatFeed.appendChild(div);
        }
      }
      chatFeed.scrollTop = chatFeed.scrollHeight;
    }

    function renderApproval(ev) {
      const card = document.createElement("div");
      card.className = "approval-card";
      card.id = `card_${ev.approval_id}`;
      card.innerHTML = `
        <div class="approval-title-row">
          <div class="approval-badge">🛡️ Permission Required</div>
          <span style="font-size:11px;color:var(--text-muted);">Session approval requested &bull; Press [Y] Approve, [A] Always, [N] Deny</span>
        </div>
        ${renderActionPreview(ev.name, ev.args, ev.diff)}
        <div class="approval-actions">
          <button class="btn-action-choice btn-approve" id="appr_${ev.approval_id}">✓ Approve Once [Y]</button>
          <button class="btn-action-choice btn-always" id="alw_${ev.approval_id}">⚡ Always Approve [A]</button>
          <button class="btn-action-choice btn-deny" id="deny_${ev.approval_id}">✕ Deny [N]</button>
        </div>
      `;
      chatFeed.appendChild(card);
      chatFeed.scrollTop = chatFeed.scrollHeight;

      const approveAction = async () => {
        card.innerHTML = `<div style="color:var(--emerald);font-weight:600;padding:4px 0;">✓ Action approved once.</div>`;
        await post("/api/approve", { id: ev.approval_id });
      };
      const alwaysAction = async () => {
        card.innerHTML = `<div style="color:var(--accent);font-weight:600;padding:4px 0;">⚡ Always approved for this session.</div>`;
        autoApproveCheck.checked = true;
        await post("/api/approve", { id: ev.approval_id, always: true });
      };
      const denyAction = async () => {
        card.innerHTML = `<div style="color:var(--rose);font-weight:600;padding:4px 0;">✕ Action denied by user.</div>`;
        await post("/api/deny", { id: ev.approval_id });
      };

      document.getElementById(`appr_${ev.approval_id}`).onclick = approveAction;
      document.getElementById(`alw_${ev.approval_id}`).onclick = alwaysAction;
      document.getElementById(`deny_${ev.approval_id}`).onclick = denyAction;

      const keyHandler = (e) => {
        if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
        if (e.key === "y" || e.key === "Y") {
          window.removeEventListener("keydown", keyHandler);
          approveAction();
        } else if (e.key === "a" || e.key === "A") {
          window.removeEventListener("keydown", keyHandler);
          alwaysAction();
        } else if (e.key === "n" || e.key === "N" || e.key === "Escape") {
          window.removeEventListener("keydown", keyHandler);
          denyAction();
        }
      };
      window.addEventListener("keydown", keyHandler, { once: true });
    }

    // Right Drawer Updates
    document.querySelectorAll(".drawer-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".drawer-tab").forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        currentTab = tab.dataset.tab;
        updateDrawer();
      });
    });

    async function updateDrawer() {
      if (currentTab === "todos") {
        if (!latestTodos.length) {
          drawerContent.innerHTML = `<div style="color:var(--text-muted);font-size:12px;">No active todos</div>`;
          return;
        }
        const marks = { completed: "✔", in_progress: "➤", pending: "☐" };
        const colors = { completed: "var(--emerald)", in_progress: "var(--accent)", pending: "var(--text-muted)" };
        drawerContent.innerHTML = latestTodos.map((t) =>
          `<div class="todo-item"><span style="color:${colors[t.status] || '#999'}">${marks[t.status] || '☐'}</span> <span>${escapeHtml(t.content)}</span></div>`
        ).join("");
      } else if (currentTab === "changes") {
        if (!changedFiles.size) {
          drawerContent.innerHTML = `<div style="color:var(--text-muted);font-size:12px;">No files touched yet</div>`;
          return;
        }
        drawerContent.innerHTML = [...changedFiles].map((f) => `<div class="file-item">${escapeHtml(f)}</div>`).join("");
      } else if (currentTab === "logs") {
        try {
          const res = await fetch("/api/logs");
          const data = await res.json();
          const lines = data.logs || [];
          drawerContent.innerHTML = `<div class="log-box">${escapeHtml(lines.slice(-60).join("\\n") || "No logs yet")}</div>`;
        } catch {
          drawerContent.innerHTML = `<div style="color:var(--rose);font-size:12px;">Could not fetch logs</div>`;
        }
      }
    }

    // SSE Connection
    function connectStream() {
      const es = new EventSource("/api/stream?cursor=" + currentCursor);
      es.onmessage = (e) => {
        if (!e.data) return;
        try {
          const data = JSON.parse(e.data);
          const running = data.running;
          statusDot.className = "status-dot " + (running ? "running" : "idle");
          statusText.textContent = running ? "running" : "idle";
          btnRun.disabled = running;
          btnStop.disabled = !running;
          appendEvents(data.events || []);
          currentCursor = data.cursor || currentCursor;
        } catch {}
      };
      es.onerror = () => {
        statusDot.className = "status-dot";
        statusText.textContent = "reconnecting...";
        es.close();
        setTimeout(connectStream, 1500);
      };
    }

    // Task Execution Controls
    async function startTask() {
      const task = taskInput.value.trim();
      if (!task) return;

      const userMsg = document.createElement("div");
      userMsg.className = "msg-user";
      userMsg.textContent = task;
      chatFeed.appendChild(userMsg);
      chatFeed.scrollTop = chatFeed.scrollHeight;
      taskInput.value = "";

      const provider = providerSelect.value;
      const model = modelSelect.value === "__custom__" ? customModelInput.value.trim() : modelSelect.value;

      try {
        await post("/api/start", {
          task,
          provider,
          model,
          auto_approve: autoApproveCheck.checked,
        });
      } catch (err) {
        appendEvents([{ type: "error", message: err.message }]);
      }
    }

    btnRun.onclick = startTask;
    btnStop.onclick = async () => {
      try { await post("/api/stop", {}); } catch (e) { alert(e.message); }
    };
    btnNewChat.onclick = async () => {
      try {
        await post("/api/new", {});
        chatFeed.innerHTML = `<div class="welcome-state"><div class="welcome-logo">SOVA</div><div class="welcome-sub">Production Coding Agent Harness</div><div class="welcome-tips"><div class="welcome-tip"><span class="welcome-tip-icon">⚡</span> Type a coding task below — inspect, write, edit, or test code</div><div class="welcome-tip"><span class="welcome-tip-icon">🔑</span> Configure API keys via the <b>⚙ Settings</b> button</div></div></div>`;
        currentCursor = 0;
        thinkingNode = null;
        changedFiles.clear();
        latestTodos = [];
        updateDrawer();
        loadSessions();
      } catch (e) { alert(e.message); }
    };

    taskInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        startTask();
      }
    });

    initCatalog();
    loadSessions();
    connectStream();
    checkProviderConnection();
    setInterval(updateDrawer, 3000);
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
        "pending": {},           # approval_id -> threading.Event
        "pending_decision": {},  # approval_id -> bool
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
                return _text_response(self, 200, _PAGE)

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

            if parsed.path == "/api/session":
                query = parse_qs(parsed.query)
                sid = (query.get("id") or [""])[0]
                try:
                    msgs = sessions.load_session(state["root_dir"], sid)
                    trajectory = SessionTrajectoryLogger(state["root_dir"], sid).get_trajectory()
                    return _json_response(self, 200, {"id": sid, "messages": msgs, "trajectory": trajectory})
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
                masked = {}
                for k, v in saved.items():
                    masked[k] = credentials.mask_value(v)
                custom_keys = [{"key": k} for k in saved if k not in known_key_names]
                return _json_response(self, 200, {
                    "credentials": masked,
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
                _add_event(state, {"type": "answer", "text": f"Resumed past session {session_id}."})
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
                        if name == "edit_file" and isinstance(args, dict) and "path" in args and "old_str" in args and "new_str" in args:
                            full = os.path.abspath(os.path.join(state["root_dir"], args["path"]))
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
                            full = os.path.abspath(os.path.join(state["root_dir"], args["path"]))
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
                if not key:
                    return _json_response(self, 400, {"error": "key is required"})
                value = str(data.get("value") or "").strip()
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
                if not key:
                    return _json_response(self, 400, {"error": "key is required"})
                result = credentials.test_credential(root, key)
                return _json_response(self, 200, result)

            return _json_response(self, 404, {"error": "not found"})

        def do_DELETE(self):
            parsed = urlparse(self.path)
            data = self._read_json()
            if data is None:
                return _json_response(self, 400, {"error": "invalid json"})

            if parsed.path == "/api/credentials":
                root = state["root_dir"]
                key = str(data.get("key") or "").strip()
                if not key:
                    return _json_response(self, 400, {"error": "key is required"})
                deleted = credentials.delete_credential(root, key)
                if deleted:
                    llm.invalidate_client()
                return _json_response(self, 200, {"ok": True, "deleted": deleted})

            return _json_response(self, 404, {"error": "not found"})

    return Handler


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(prog="sova-web")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8787, help="Port to bind")
    parser.add_argument("--provider", choices=["groq", "ollama", "nvidia", "openai"], help="LLM provider")
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
