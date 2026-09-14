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

_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SOVA — Coding Agent Harness</title>
  <style>
    :root {
      --bg-canvas: #090a0f;
      --bg-sidebar: #0e1017;
      --bg-panel: #131622;
      --bg-card: #181c2b;
      --bg-input: #0a0c12;
      --border: #1e2334;
      --border-subtle: #161926;
      --text-primary: #f8fafc;
      --text-secondary: #94a3b8;
      --text-muted: #64748b;
      --accent: #38bdf8;
      --accent-soft: rgba(56, 189, 248, 0.12);
      --success: #10b981;
      --success-soft: rgba(16, 185, 129, 0.12);
      --warning: #f59e0b;
      --warning-soft: rgba(245, 158, 11, 0.12);
      --danger: #ef4444;
      --danger-soft: rgba(239, 68, 68, 0.12);
      --font-ui: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
      --font-mono: "JetBrains Mono", "Cascadia Code", Consolas, monospace;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #262b3d; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #3b4259; }

    body {
      background-color: var(--bg-canvas);
      color: var(--text-primary);
      font-family: var(--font-ui);
      height: 100vh;
      width: 100vw;
      overflow: hidden;
      display: flex;
      -webkit-font-smoothing: antialiased;
    }

    /* Left Sidebar */
    .sidebar {
      width: 260px;
      min-width: 260px;
      background-color: var(--bg-sidebar);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
      transition: margin-left 0.25s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .sidebar.collapsed {
      margin-left: -260px;
    }
    .sidebar-header {
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .logo-badge {
      font-weight: 700;
      letter-spacing: 0.05em;
      color: var(--text-primary);
      font-size: 14px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .logo-badge span {
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 10px;
      padding: 2px 6px;
      border-radius: 4px;
      font-weight: 600;
    }
    .btn-new-chat {
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-primary);
      padding: 5px 10px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 500;
      transition: all 0.15s ease;
      display: flex;
      align-items: center;
      gap: 4px;
    }
    .btn-new-chat:hover {
      border-color: var(--accent);
      color: var(--accent);
      background: var(--accent-soft);
    }

    .session-list {
      flex: 1;
      overflow-y: auto;
      padding: 12px 8px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .session-card {
      position: relative;
      padding: 9px 12px;
      border-radius: 6px;
      background: transparent;
      border: 1px solid transparent;
      cursor: pointer;
      transition: all 0.15s ease;
      display: flex;
      flex-direction: column;
      gap: 3px;
    }
    .session-card:hover {
      background: var(--bg-panel);
      border-color: var(--border-subtle);
    }
    .session-card.active {
      background: var(--bg-panel);
      border-color: var(--accent);
    }
    .session-card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 11px;
      color: var(--text-muted);
      font-family: var(--font-sans);
    }
    .session-card-title {
      font-weight: 600;
      color: var(--text-primary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 140px;
    }
    .session-snippet {
      font-size: 12px;
      color: var(--text-secondary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .session-card:hover .btn-del-sess {
      opacity: 1;
    }
    .btn-del-sess {
      opacity: 0;
      background: transparent;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      padding: 2px 4px;
      border-radius: 4px;
      font-size: 11px;
      line-height: 1;
      transition: all 0.15s ease;
    }
    .btn-del-sess:hover {
      color: var(--danger);
      background: rgba(239, 68, 68, 0.15);
    }

    .sidebar-footer {
      padding: 12px 16px;
      border-top: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: var(--bg-sidebar);
    }

    /* Main Workspace */
    .main-workspace {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }

    /* Top Control Bar */
    .topbar {
      height: 48px;
      background: var(--bg-sidebar);
      border-bottom: 1px solid var(--border);
      padding: 0 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-shrink: 0;
    }
    .topbar-left {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }
    .topbar-right {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-shrink: 0;
    }

    .icon-btn-toggle {
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-secondary);
      width: 30px;
      height: 30px;
      border-radius: 6px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.15s ease;
      flex-shrink: 0;
    }
    .icon-btn-toggle:hover {
      border-color: var(--accent);
      color: var(--text-primary);
      background: var(--bg-panel);
    }

    .status-pill {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      padding: 3px 10px;
      border-radius: 20px;
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-secondary);
      font-weight: 500;
      flex-shrink: 0;
    }
    .status-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--text-muted);
    }
    .status-dot.running {
      background: var(--warning);
      box-shadow: 0 0 8px var(--warning);
    }
    .status-dot.idle { background: var(--success); }

    .conn-badge {
      font-size: 11px;
      padding: 3px 8px;
      border-radius: 4px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-secondary);
      flex-shrink: 0;
    }
    .conn-dot-sm {
      width: 6px;
      height: 6px;
      border-radius: 50%;
    }
    .conn-dot-sm.ok { background: var(--success); }
    .conn-dot-sm.error { background: var(--danger); }
    .conn-dot-sm.none { background: var(--text-muted); }

    /* Central Content Area */
    .content-area {
      flex: 1;
      min-width: 0;
      display: flex;
      overflow: hidden;
    }

    .chat-container {
      flex: 1;
      min-width: 0;
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
      gap: 16px;
    }

    /* Messages */
    .msg-user {
      align-self: flex-end;
      max-width: 80%;
      background: var(--accent-soft);
      border: 1px solid rgba(56, 189, 248, 0.25);
      color: var(--text-primary);
      padding: 12px 16px;
      border-radius: 10px;
      font-size: 13px;
      line-height: 1.5;
      white-space: pre-wrap;
    }
    .msg-agent {
      align-self: flex-start;
      max-width: 92%;
      background: var(--bg-panel);
      border: 1px solid var(--border);
      padding: 16px 20px;
      border-radius: 10px;
      font-size: 13px;
      line-height: 1.6;
    }
    .msg-agent-header {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
      color: var(--accent);
      font-weight: 600;
      font-size: 12px;
      letter-spacing: 0.02em;
    }
    .msg-thinking {
      color: var(--text-muted);
      font-size: 12px;
      padding: 8px 12px;
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid var(--border-subtle);
      display: flex;
      align-items: center;
      gap: 8px;
      font-family: var(--font-mono);
    }

    /* Markdown styling */
    .md-body h1, .md-body h2, .md-body h3 {
      margin: 12px 0 6px 0;
      color: var(--text-primary);
      font-weight: 600;
    }
    .md-body p { margin-bottom: 8px; }
    .md-body ul { margin: 6px 0 10px 20px; }
    .md-body li { margin-bottom: 4px; }
    .inline-code {
      background: var(--bg-card);
      color: var(--accent);
      padding: 2px 6px;
      border-radius: 4px;
      font-family: var(--font-mono);
      font-size: 12px;
    }
    .code-block-wrapper {
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: 6px;
      margin: 10px 0;
      overflow: hidden;
    }
    .code-block-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 6px 12px;
      background: var(--bg-card);
      border-bottom: 1px solid var(--border);
      font-size: 11px;
      color: var(--text-muted);
      font-family: var(--font-mono);
    }
    .copy-btn {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text-secondary);
      padding: 2px 8px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 10px;
    }
    .copy-btn:hover { color: var(--text-primary); border-color: var(--accent); }
    .code-block-wrapper pre {
      padding: 12px 14px;
      overflow-x: auto;
      font-size: 12px;
      line-height: 1.45;
      font-family: var(--font-mono);
    }

    /* Sub-Agent Hierarchy Cards */
    .subagent-card {
      background: #11141d;
      border: 1px solid var(--border);
      border-left: 3px solid #3b82f6;
      border-radius: 8px;
      margin: 8px 0;
      overflow: hidden;
      transition: border-color 0.2s;
    }
    .subagent-card.role-researcher { border-left-color: #06b6d4; }
    .subagent-card.role-coder { border-left-color: #10b981; }
    .subagent-card.role-reviewer { border-left-color: #a855f7; }
    .subagent-card.role-general { border-left-color: #3b82f6; }
    .subagent-card-header {
      padding: 8px 12px;
      background: rgba(255, 255, 255, 0.03);
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      user-select: none;
    }
    .subagent-card-header:hover {
      background: rgba(255, 255, 255, 0.05);
    }
    .subagent-chevron {
      font-size: 10px;
      color: var(--text-muted);
      transition: transform 0.2s ease;
      display: inline-block;
    }
    .subagent-card.collapsed .subagent-chevron {
      transform: rotate(-90deg);
    }
    .subagent-card.collapsed .subagent-body {
      display: none;
    }
    .subagent-role-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 4px;
      letter-spacing: 0.2px;
      flex-shrink: 0;
    }
    .badge-researcher {
      color: #06b6d4;
      background: rgba(6, 182, 212, 0.12);
      border: 1px solid rgba(6, 182, 212, 0.3);
    }
    .badge-coder {
      color: #10b981;
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.3);
    }
    .badge-reviewer {
      color: #a855f7;
      background: rgba(168, 85, 247, 0.12);
      border: 1px solid rgba(168, 85, 247, 0.3);
    }
    .badge-general {
      color: #3b82f6;
      background: rgba(59, 130, 246, 0.12);
      border: 1px solid rgba(59, 130, 246, 0.3);
    }
    .subagent-task-title {
      flex: 1;
      font-size: 12.5px;
      color: var(--text-primary);
      font-weight: 500;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .subagent-status-pill {
      font-size: 10.5px;
      padding: 2px 7px;
      border-radius: 12px;
      font-weight: 600;
      flex-shrink: 0;
    }
    .pill-running {
      color: #f59e0b;
      background: rgba(245, 158, 11, 0.12);
      border: 1px solid rgba(245, 158, 11, 0.3);
      animation: pulse 1.8s infinite;
    }
    .pill-completed {
      color: #10b981;
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.3);
    }
    .pill-incomplete {
      color: #ef4444;
      background: rgba(239, 68, 68, 0.12);
      border: 1px solid rgba(239, 68, 68, 0.3);
    }
    .subagent-body {
      padding: 8px 12px 10px 20px;
      border-top: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 6px;
      background: rgba(0, 0, 0, 0.15);
    }
    .subagent-summary-box {
      font-size: 11.5px;
      color: var(--text-secondary);
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 6px;
      padding: 6px 10px;
      margin-top: 4px;
      white-space: pre-wrap;
    }
    .drawer-subagent-item {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      margin-bottom: 6px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    /* Tool Cards */
    .tool-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      font-size: 12px;
      margin-top: 4px;
    }
    .tool-header {
      padding: 8px 12px;
      background: rgba(255,255,255,0.02);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: var(--text-secondary);
      font-family: var(--font-mono);
    }
    .tool-body {
      padding: 10px 12px;
      color: var(--text-secondary);
      white-space: pre-wrap;
      max-height: 240px;
      overflow-y: auto;
      font-family: var(--font-mono);
      font-size: 11.5px;
      line-height: 1.4;
    }

    /* Diff Viewer */
    .copilot-diff-viewer {
      background: #0b0d13;
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      margin: 8px 0;
      font-family: var(--font-mono);
      font-size: 11.5px;
    }
    .diff-header {
      background: var(--bg-card);
      border-bottom: 1px solid var(--border);
      padding: 6px 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .diff-file-path {
      color: var(--text-primary);
      font-weight: 500;
    }
    .diff-body {
      max-height: 380px;
      overflow: auto;
    }
    .diff-inline-row {
      display: flex;
      min-height: 20px;
      line-height: 20px;
      width: 100%;
    }
    .diff-inline-row.row-add { background: rgba(16, 185, 129, 0.12); }
    .diff-inline-row.row-del { background: rgba(239, 68, 68, 0.12); }
    .diff-inline-row.row-hunk { background: rgba(56, 189, 248, 0.08); color: var(--accent); }
    .gutter-sign {
      width: 20px;
      text-align: center;
      user-select: none;
      flex-shrink: 0;
      font-weight: 600;
    }
    .line-code {
      flex: 1;
      padding: 0 8px;
      white-space: pre;
      color: var(--text-primary);
    }

    /* Approval Card */
    .approval-card {
      background: var(--bg-panel);
      border: 1px solid var(--warning);
      border-radius: 8px;
      padding: 16px;
      margin: 10px 0;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .approval-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .approval-badge {
      font-weight: 600;
      font-size: 12px;
      color: var(--warning);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .approval-actions {
      display: flex;
      gap: 8px;
    }
    .btn-approve {
      background: var(--success);
      color: #042f2e;
      border: 0;
      padding: 6px 14px;
      border-radius: 6px;
      font-weight: 600;
      cursor: pointer;
      font-size: 12px;
    }
    .btn-always {
      background: var(--accent);
      color: #082f49;
      border: 0;
      padding: 6px 14px;
      border-radius: 6px;
      font-weight: 600;
      cursor: pointer;
      font-size: 12px;
    }
    .btn-deny {
      background: var(--danger);
      color: #4c0519;
      border: 0;
      padding: 6px 14px;
      border-radius: 6px;
      font-weight: 600;
      cursor: pointer;
      font-size: 12px;
    }

    /* Prompt Box with embedded Model Controls & Professional Buttons */
    .prompt-container {
      padding: 16px 24px 20px;
      background: var(--bg-sidebar);
      border-top: 1px solid var(--border);
      min-width: 0;
    }
    .prompt-wrapper {
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      min-width: 0;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }
    .prompt-wrapper:focus-within {
      border-color: var(--accent);
      box-shadow: 0 0 0 1px var(--accent-soft);
    }
    textarea#taskInput {
      width: 100%;
      min-height: 60px;
      max-height: 200px;
      background: transparent;
      border: 0;
      outline: none;
      color: var(--text-primary);
      font-family: inherit;
      font-size: 13px;
      line-height: 1.5;
      resize: none;
    }

    .prompt-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding-top: 6px;
      border-top: 1px solid var(--border-subtle);
      flex-wrap: wrap;
    }

    .prompt-controls-left {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      min-width: 0;
    }
    select.prompt-select {
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-primary);
      border-radius: 6px;
      padding: 4px 10px;
      font-family: inherit;
      font-size: 11.5px;
      outline: none;
      cursor: pointer;
      max-width: 220px;
      text-overflow: ellipsis;
      transition: all 0.15s ease;
    }
    select.prompt-select:hover {
      border-color: var(--accent);
    }

    .prompt-approve-label {
      font-size: 11.5px;
      color: var(--text-muted);
      display: inline-flex;
      align-items: center;
      gap: 4px;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }

    /* Action Buttons */
    .prompt-buttons {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-shrink: 0;
    }
    .btn-run {
      background: #38bdf8;
      color: #090a0f;
      border: 0;
      padding: 7px 16px;
      border-radius: 6px;
      font-weight: 600;
      font-size: 12px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      box-shadow: 0 2px 8px rgba(56, 189, 248, 0.25);
      transition: all 0.15s ease;
      white-space: nowrap;
    }
    .btn-run:hover {
      background: #7dd3fc;
      transform: translateY(-1px);
      box-shadow: 0 4px 14px rgba(56, 189, 248, 0.35);
    }
    .btn-run:disabled {
      background: #1e293b;
      color: #64748b;
      box-shadow: none;
      transform: none;
      cursor: not-allowed;
      opacity: 0.7;
    }

    .btn-stop {
      background: rgba(239, 68, 68, 0.12);
      border: 1px solid rgba(239, 68, 68, 0.3);
      color: #fca5a5;
      padding: 7px 14px;
      border-radius: 6px;
      font-weight: 600;
      font-size: 12px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
      white-space: nowrap;
    }
    .btn-stop:hover:not(:disabled) {
      background: rgba(239, 68, 68, 0.25);
      border-color: #ef4444;
      color: #ffffff;
    }
    .btn-stop:disabled {
      opacity: 0.35;
      background: transparent;
      border-color: transparent;
      color: #64748b;
      cursor: not-allowed;
    }

    /* Right Drawer */
    .drawer {
      width: 280px;
      min-width: 280px;
      background: var(--bg-sidebar);
      border-left: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
      transition: margin-right 0.25s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .drawer.collapsed {
      margin-right: -280px;
    }
    .drawer-tabs { display: flex; border-bottom: 1px solid var(--border); }
    .drawer-tab {
      flex: 1;
      padding: 10px 4px;
      text-align: center;
      font-size: 11px;
      white-space: nowrap;
      color: var(--text-muted);
      cursor: pointer;
      border-bottom: 2px solid transparent;
    }
    .drawer-tab.active {
      color: var(--text-primary);
      border-bottom-color: var(--accent);
      background: var(--bg-panel);
    }
    .drawer-content {
      flex: 1;
      overflow-y: auto;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    /* Settings Slide-Over Drawer */
    .settings-modal-overlay {
      position: fixed;
      inset: 0;
      z-index: 1000;
      background: rgba(0,0,0,0.6);
      backdrop-filter: blur(2px);
      display: none;
      align-items: center;
      justify-content: flex-end;
    }
    .settings-modal-overlay.open { display: flex; }
    .settings-drawer {
      width: 480px;
      max-width: 90vw;
      height: 100vh;
      background: var(--bg-sidebar);
      border-left: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      box-shadow: -10px 0 30px rgba(0,0,0,0.5);
    }
    .settings-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .settings-title {
      font-size: 14px;
      font-weight: 600;
      color: var(--text-primary);
      letter-spacing: 0.02em;
    }
    .btn-close-modal {
      background: transparent;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 16px;
      padding: 4px;
    }
    .btn-close-modal:hover { color: var(--text-primary); }

    .settings-tabs {
      display: flex;
      border-bottom: 1px solid var(--border);
      padding: 0 16px;
    }
    .settings-tab-btn {
      padding: 10px 14px;
      font-size: 12px;
      font-weight: 500;
      color: var(--text-muted);
      cursor: pointer;
      border-bottom: 2px solid transparent;
    }
    .settings-tab-btn.active {
      color: var(--accent);
      border-bottom-color: var(--accent);
    }

    .settings-content {
      flex: 1;
      overflow-y: auto;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    /* Key Card Item */
    .key-card {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .key-card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .key-card-title {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-primary);
    }
    .key-card-hint {
      font-size: 11px;
      color: var(--text-muted);
    }
    .key-status-pill {
      font-size: 10px;
      padding: 2px 6px;
      border-radius: 4px;
      font-weight: 600;
      text-transform: uppercase;
    }
    .key-status-pill.active { background: var(--success-soft); color: var(--success); }
    .key-status-pill.none { background: rgba(255,255,255,0.05); color: var(--text-muted); }
    .key-status-pill.error { background: var(--danger-soft); color: var(--danger); }

    .key-input-group {
      display: flex;
      gap: 6px;
    }
    .key-input-group input {
      flex: 1;
      background: var(--bg-input);
      border: 1px solid var(--border);
      color: var(--text-primary);
      border-radius: 6px;
      padding: 6px 10px;
      font-family: var(--font-mono);
      font-size: 12px;
    }
    .btn-key-act {
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-secondary);
      padding: 6px 10px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 500;
      cursor: pointer;
      white-space: nowrap;
    }
    .btn-key-act:hover { border-color: var(--accent); color: var(--text-primary); }
    .btn-key-act.primary { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); }

    .btn-undo {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text-secondary);
      border-radius: 6px;
      padding: 6px 10px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }
    .btn-undo:hover {
      background: var(--bg-card);
      border-color: #f59e0b;
      color: #f59e0b;
    }

    /* Toast Container */
    .toast-stack {
      position: fixed;
      bottom: 20px;
      right: 20px;
      z-index: 2000;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .toast-item {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px 14px;
      font-size: 12px;
      color: var(--text-primary);
      box-shadow: 0 4px 12px rgba(0,0,0,0.4);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .toast-item.success { border-color: var(--success); }
    .toast-item.error { border-color: var(--danger); }
  </style>
</head>
<body>

  <!-- Left Sidebar -->
  <aside class="sidebar" id="leftSidebar">
    <div class="sidebar-header">
      <div class="logo-badge">SOVA <span>v0.1.0</span></div>
      <button class="btn-new-chat" id="btnNewChat">+ New</button>
    </div>
    <div class="session-list" id="sessionList"></div>
    <div class="sidebar-footer">
      <button class="btn-new-chat" id="btnOpenSettings" style="width:100%;justify-content:center;">
        ⚙ Settings & Keys
      </button>
    </div>
  </aside>

  <!-- Main Workspace -->
  <main class="main-workspace">
    <!-- Topbar -->
    <header class="topbar">
      <div class="topbar-left">
        <button class="icon-btn-toggle" id="btnToggleSidebar" title="Toggle Sidebar">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="9" y1="3" x2="9" y2="21"></line></svg>
        </button>
        <div class="status-pill">
          <div class="status-dot idle" id="statusDot"></div>
          <span id="statusText">Idle</span>
        </div>
        <div style="font-size:11px;color:var(--text-muted);font-family:var(--font-mono);" id="sessionIdDisplay"></div>
      </div>

      <div class="topbar-right">
        <div class="conn-badge" id="connBadge">
          <div class="conn-dot-sm none" id="connDot"></div>
          <span id="connText">Checking</span>
        </div>
        <button class="icon-btn-toggle" id="btnToggleDrawer" title="Toggle Inspector Panel">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="15" y1="3" x2="15" y2="21"></line></svg>
        </button>
      </div>
    </header>

    <!-- Content Area -->
    <div class="content-area">
      <!-- Chat Container -->
      <section class="chat-container">
        <div class="chat-feed" id="chatFeed">
          <div style="text-align:center;padding:40px 20px;color:var(--text-muted);font-size:13px;">
            <div style="font-size:18px;font-weight:600;color:var(--text-primary);margin-bottom:6px;">SOVA Coding Agent</div>
            <div>Enter a task below to inspect, edit, or execute code in this workspace.</div>
          </div>
        </div>

        <!-- Prompt Container with Embedded Model Selectors & Upgraded Action Buttons -->
        <div class="prompt-container">
          <div class="prompt-wrapper">
            <textarea id="taskInput" placeholder="Type a task (e.g. 'implement simple calculator', 'run tests')..."></textarea>
            <div class="prompt-footer">
              <div class="prompt-controls-left">
                <select id="providerSelect" class="prompt-select" title="LLM Provider"></select>
                <select id="modelSelect" class="prompt-select" title="Model"></select>
                <label class="prompt-approve-label">
                  <input type="checkbox" id="autoApproveCheck" /> Auto-approve
                </label>
              </div>

              <div class="prompt-buttons">
                <button class="btn-undo" id="btnUndo" title="Rollback the last file edit made by SOVA (/undo)">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"></polyline><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path></svg>
                  <span>Undo Edit</span>
                </button>
                <button class="btn-stop" id="btnStop" disabled>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>
                  <span>Stop</span>
                </button>
                <button class="btn-run" id="btnRun">
                  <span>Run Task</span>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- Right Drawer -->
      <aside class="drawer" id="rightDrawer">
        <div class="drawer-tabs">
          <div class="drawer-tab active" data-tab="todos">Todos</div>
          <div class="drawer-tab" data-tab="subagents">Sub-Agents</div>
          <div class="drawer-tab" data-tab="changes">Changes</div>
          <div class="drawer-tab" data-tab="logs">Logs</div>
        </div>
        <div class="drawer-content" id="drawerContent"></div>
      </aside>
    </div>
  </main>

  <!-- Settings Slide-Over Drawer -->
  <div class="settings-modal-overlay" id="settingsOverlay">
    <div class="settings-drawer">
      <div class="settings-header">
        <div class="settings-title">Settings & API Keys</div>
        <button class="btn-close-modal" id="btnCloseSettings">✕</button>
      </div>
      <div class="settings-tabs">
        <div class="settings-tab-btn active" data-stab="keys" id="tabKeysBtn">API Keys & Integrations</div>
        <div class="settings-tab-btn" data-stab="sys" id="tabSysBtn">System Info</div>
      </div>
      <div class="settings-content" id="settingsContent"></div>
    </div>
  </div>

  <!-- Toast Stack -->
  <div class="toast-stack" id="toastStack"></div>

  <script>
    // Global Scope & Helper Functions
    function escapeHtml(s) {
      return String(s || "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    }

    function showToast(message, type = "info") {
      const stack = document.getElementById("toastStack");
      const toast = document.createElement("div");
      toast.className = `toast-item ${type}`;
      toast.textContent = message;
      stack.appendChild(toast);
      setTimeout(() => { toast.remove(); }, 3500);
    }

    const chatFeed = document.getElementById("chatFeed");
    const taskInput = document.getElementById("taskInput");
    const btnRun = document.getElementById("btnRun");
    const btnStop = document.getElementById("btnStop");
    const btnUndo = document.getElementById("btnUndo");
    const btnNewChat = document.getElementById("btnNewChat");
    const statusDot = document.getElementById("statusDot");
    const statusText = document.getElementById("statusText");
    const sessionIdDisplay = document.getElementById("sessionIdDisplay");
    const sessionList = document.getElementById("sessionList");
    const providerSelect = document.getElementById("providerSelect");
    const modelSelect = document.getElementById("modelSelect");
    const autoApproveCheck = document.getElementById("autoApproveCheck");
    const drawerContent = document.getElementById("drawerContent");
    const connDot = document.getElementById("connDot");
    const connText = document.getElementById("connText");

    const leftSidebar = document.getElementById("leftSidebar");
    const rightDrawer = document.getElementById("rightDrawer");
    const btnToggleSidebar = document.getElementById("btnToggleSidebar");
    const btnToggleDrawer = document.getElementById("btnToggleDrawer");

    const settingsOverlay = document.getElementById("settingsOverlay");
    const btnOpenSettings = document.getElementById("btnOpenSettings");
    const btnCloseSettings = document.getElementById("btnCloseSettings");
    const settingsContent = document.getElementById("settingsContent");
    const tabKeysBtn = document.getElementById("tabKeysBtn");
    const tabSysBtn = document.getElementById("tabSysBtn");

    let currentCursor = 0;
    let thinkingNode = null;
    let currentTab = "todos";
    let catalog = {};
    let latestTodos = [];
    let changedFiles = new Set();
    let currentSettingsTab = "keys";
    let subagentCards = new Map();
    let subagentList = [];

    // Collapsible Panels Toggle
    btnToggleSidebar.onclick = () => {
      leftSidebar.classList.toggle("collapsed");
    };
    btnToggleDrawer.onclick = () => {
      rightDrawer.classList.toggle("collapsed");
    };

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
                <button class="copy-btn" onclick="navigator.clipboard.writeText(this.closest('.code-block-wrapper').querySelector('code').innerText); this.innerText='Copied'; setTimeout(()=>this.innerText='Copy', 1500);">Copy</button>
              </div>
              <pre><code>${code}</code></pre>
            </div>
          `;
        }
      }
      return `<div class="md-body">${html}</div>`;
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

    // Catalog & Connection Checking
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
        checkActiveConnection();
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
    }

    async function checkActiveConnection() {
      const p = providerSelect.value;
      const keyMap = { groq: "GROQ_API_KEY", openai: "OPENAI_API_KEY", nvidia: "NVIDIA_API_KEY", ollama: "SOVA_OLLAMA_HOST" };
      const targetKey = keyMap[p];
      if (p === "ollama") {
        connDot.className = "conn-dot-sm ok";
        connText.textContent = "Local";
        return;
      }
      try {
        const res = await fetch("/api/credentials");
        const data = await res.json();
        const info = (data.key_info || {})[targetKey];
        if (info && info.is_set) {
          connDot.className = "conn-dot-sm ok";
          connText.textContent = "Configured";
        } else {
          connDot.className = "conn-dot-sm error";
          connText.textContent = "Key Missing";
        }
      } catch {
        connDot.className = "conn-dot-sm none";
        connText.textContent = "Unknown";
      }
    }

    providerSelect.addEventListener("change", () => {
      updateModelDropdown();
      checkActiveConnection();
    });

    function formatTime(ts) {
      if (!ts) return "";
      const d = new Date(ts * 1000);
      const now = new Date();
      if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      }
      return d.toLocaleDateString([], { month: "short", day: "numeric" });
    }

    async function loadSessions() {
      try {
        const res = await fetch("/api/sessions");
        const data = await res.json();
        const rows = data.sessions || [];
        sessionList.innerHTML = "";
        if (!rows.length) {
          sessionList.innerHTML = `<div style="padding:12px;font-size:12px;color:var(--text-muted);text-align:center;">No saved sessions</div>`;
          return;
        }
        for (const r of rows) {
          const card = document.createElement("div");
          const isActive = r.id === sessionIdDisplay.textContent;
          card.className = "session-card" + (isActive ? " active" : "");
          const title = r.title || r.first_message || "Chat";
          const timeStr = formatTime(r.updated_at);
          const msgCount = r.message_count || 0;
          card.innerHTML = `
            <div class="session-card-header">
              <span class="session-card-title" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
              <div style="display:flex;align-items:center;gap:4px;">
                <span>${escapeHtml(timeStr)}</span>
                <button class="btn-del-sess" title="Delete conversation" onclick="deleteSession('${escapeHtml(r.id)}', event)">✕</button>
              </div>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:2px;">
              <span class="session-snippet" style="max-width:140px;">${escapeHtml(r.first_message || "Empty chat")}</span>
              <span style="font-size:10px;color:var(--text-muted);background:var(--bg-canvas);border:1px solid var(--border-subtle);border-radius:4px;padding:1px 5px;">${msgCount} msgs</span>
            </div>
          `;
          card.onclick = (e) => {
            if (e.target.classList.contains("btn-del-sess")) return;
            resumeSession(r.id);
          };
          sessionList.appendChild(card);
        }
      } catch (e) {
        sessionList.innerHTML = `<div style="padding:10px;font-size:12px;color:var(--danger);">Error loading sessions</div>`;
      }
    }

    async function deleteSession(id, e) {
      if (e) e.stopPropagation();
      if (!confirm("Delete this conversation?")) return;
      try {
        await post("/api/delete_session", { id });
        showToast("Conversation deleted", "info");
        if (sessionIdDisplay.textContent === id) {
          btnNewChat.click();
        } else {
          loadSessions();
        }
      } catch (err) {
        showToast("Delete failed: " + err.message, "error");
      }
    }

    function renderLegacyMessages(messages) {
      for (const m of messages) {
        if (m.role === "user") {
          const userMsg = document.createElement("div");
          userMsg.className = "msg-user";
          userMsg.textContent = m.content || "";
          chatFeed.appendChild(userMsg);
        } else if (m.role === "assistant" && m.content) {
          const div = document.createElement("div");
          div.className = "msg-agent";
          div.innerHTML = `<div class="msg-agent-header">SOVA Agent</div>${renderMarkdown(m.content)}`;
          chatFeed.appendChild(div);
        }
      }
      chatFeed.scrollTop = chatFeed.scrollHeight;
    }

    async function resumeSession(id) {
      try {
        await post("/api/resume_session", { id });
        sessionIdDisplay.textContent = id;
        chatFeed.innerHTML = "";
        currentCursor = 0;
        thinkingNode = null;
        changedFiles.clear();
        latestTodos = [];
        subagentCards.clear();
        subagentList = [];

        const sRes = await fetch("/api/session?id=" + encodeURIComponent(id));
        if (sRes.ok) {
          const sData = await sRes.json();
          const trajectory = sData.trajectory || [];
          const events = sData.events || [];
          const allEvents = trajectory.length ? trajectory : events;

          if (allEvents.length > 0) {
            appendEvents(allEvents, true);
            currentCursor = allEvents.length;
          } else if (sData.messages && sData.messages.length) {
            renderLegacyMessages(sData.messages);
          }
        }
        updateDrawer();
        loadSessions();
        showToast(`Loaded conversation (${id.slice(0, 14)})`, "success");
      } catch (err) {
        showToast("Resume failed: " + err.message, "error");
      }
    }

    // Settings Drawer Logic
    btnOpenSettings.onclick = () => {
      settingsOverlay.classList.add("open");
      renderSettingsDrawer();
    };
    btnCloseSettings.onclick = () => {
      settingsOverlay.classList.remove("open");
    };

    tabKeysBtn.onclick = () => {
      currentSettingsTab = "keys";
      tabKeysBtn.classList.add("active");
      tabSysBtn.classList.remove("active");
      renderSettingsDrawer();
    };
    tabSysBtn.onclick = () => {
      currentSettingsTab = "sys";
      tabSysBtn.classList.add("active");
      tabKeysBtn.classList.remove("active");
      renderSettingsDrawer();
    };

    async function renderSettingsDrawer() {
      if (currentSettingsTab === "keys") {
        settingsContent.innerHTML = `<div style="color:var(--text-muted);font-size:12px;">Loading credentials...</div>`;
        try {
          const res = await fetch("/api/credentials");
          const data = await res.json();
          const keyInfo = data.key_info || {};
          const known = data.known_keys || [];
          const custom = data.custom_keys || [];

          let html = `<div><div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;margin-bottom:10px;">Provider API Keys</div>`;
          for (const k of known) {
            const info = keyInfo[k.key] || {};
            const statusCls = info.is_set ? "active" : "none";
            const statusText = info.is_set ? "Active" : "Not Set";
            html += `
              <div class="key-card">
                <div class="key-card-header">
                  <div>
                    <div class="key-card-title">${escapeHtml(k.label)}</div>
                    <div class="key-card-hint">${escapeHtml(k.hint)}</div>
                  </div>
                  <span class="key-status-pill ${statusCls}">${statusText}</span>
                </div>
                <div class="key-input-group">
                  <input type="password" id="inp_${k.key}" placeholder="${info.masked || 'Enter API Key...'}" />
                  <button class="btn-key-act" onclick="togglePassVis('inp_${k.key}')">Show</button>
                  <button class="btn-key-act" onclick="testKey('${k.key}')">Test</button>
                  <button class="btn-key-act primary" onclick="saveKey('${k.key}')">Save</button>
                  ${info.is_saved ? `<button class="btn-key-act" onclick="deleteKey('${k.key}')">Remove</button>` : ''}
                </div>
              </div>
            `;
          }
          html += `</div>`;

          if (custom.length) {
            html += `<div style="margin-top:16px;"><div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;margin-bottom:10px;">Custom Keys</div>`;
            for (const c of custom) {
              html += `
                <div class="key-card">
                  <div class="key-card-header">
                    <div class="key-card-title">${escapeHtml(c.key)}</div>
                  </div>
                  <div class="key-input-group">
                    <input type="password" id="inp_${c.key}" placeholder="${c.masked || 'Value...'}" />
                    <button class="btn-key-act" onclick="togglePassVis('inp_${c.key}')">Show</button>
                    <button class="btn-key-act primary" onclick="saveKey('${c.key}')">Save</button>
                    <button class="btn-key-act" onclick="deleteKey('${c.key}')">Remove</button>
                  </div>
                </div>
              `;
            }
            html += `</div>`;
          }

          html += `
            <div style="margin-top:16px;">
              <div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;margin-bottom:10px;">Add Custom Key</div>
              <div class="key-card">
                <div class="key-input-group">
                  <input type="text" id="newKeyName" placeholder="KEY_NAME" style="flex:0.8;" />
                  <input type="password" id="newKeyValue" placeholder="Value..." />
                  <button class="btn-key-act primary" onclick="addCustomKey()">Add</button>
                </div>
              </div>
            </div>
          `;
          settingsContent.innerHTML = html;
        } catch (err) {
          settingsContent.innerHTML = `<div style="color:var(--danger);font-size:12px;">Failed to load credentials: ${escapeHtml(err.message)}</div>`;
        }
      } else {
        settingsContent.innerHTML = `<div style="color:var(--text-muted);font-size:12px;">Loading system info...</div>`;
        try {
          const res = await fetch("/api/system-info");
          const info = await res.json();
          settingsContent.innerHTML = `
            <div class="key-card">
              <div style="display:grid;grid-template-columns:120px 1fr;gap:8px;font-size:12px;">
                <span style="color:var(--text-muted);">Python</span><span>${escapeHtml(info.python_version.split(' ')[0])}</span>
                <span style="color:var(--text-muted);">Platform</span><span>${escapeHtml(info.platform)}</span>
                <span style="color:var(--text-muted);">Workspace</span><span>${escapeHtml(info.workspace)}</span>
                <span style="color:var(--text-muted);">Provider</span><span>${escapeHtml(info.provider)}</span>
                <span style="color:var(--text-muted);">Model</span><span>${escapeHtml(info.model)}</span>
                <span style="color:var(--text-muted);">Session</span><span>${escapeHtml(info.session_id)}</span>
              </div>
            </div>
          `;
        } catch (err) {
          settingsContent.innerHTML = `<div style="color:var(--danger);font-size:12px;">Failed: ${escapeHtml(err.message)}</div>`;
        }
      }
    }

    function togglePassVis(id) {
      const el = document.getElementById(id);
      if (el) el.type = el.type === "password" ? "text" : "password";
    }

    async function saveKey(key) {
      const el = document.getElementById(`inp_${key}`);
      const val = el ? el.value.trim() : "";
      if (!val) { showToast("Key value cannot be empty", "error"); return; }
      try {
        await post("/api/credentials", { key, value: val });
        showToast(`Saved ${key}`, "success");
        renderSettingsDrawer();
        checkActiveConnection();
      } catch (err) {
        showToast("Save failed: " + err.message, "error");
      }
    }

    async function testKey(key) {
      const el = document.getElementById(`inp_${key}`);
      const val = el ? el.value.trim() : "";
      showToast(`Testing ${key}...`, "info");
      try {
        const res = await post("/api/credentials/test", { key, value: val });
        if (res.ok) showToast(res.message, "success");
        else showToast(res.message, "error");
      } catch (err) {
        showToast("Test failed: " + err.message, "error");
      }
    }

    async function deleteKey(key) {
      try {
        await post("/api/credentials/delete", { key });
        showToast(`Removed ${key}`, "success");
        renderSettingsDrawer();
        checkActiveConnection();
      } catch (err) {
        showToast("Delete failed: " + err.message, "error");
      }
    }

    async function addCustomKey() {
      const kEl = document.getElementById("newKeyName");
      const vEl = document.getElementById("newKeyValue");
      const k = kEl ? kEl.value.trim().toUpperCase().replace(/[^A-Z0-9_]/g, "_") : "";
      const v = vEl ? vEl.value.trim() : "";
      if (!k || !v) { showToast("Both Key Name and Value required", "error"); return; }
      try {
        await post("/api/credentials", { key: k, value: v });
        showToast(`Added ${k}`, "success");
        renderSettingsDrawer();
      } catch (err) {
        showToast("Failed to add key: " + err.message, "error");
      }
    }

    // Event Stream & Rendering
    function appendEvents(events, isReplay = false) {
      if (!events || !events.length) return;
      for (const ev of events) {
        const agent = ev.subagent ? `[${ev.subagent}] ` : "";

        if (ev.type === "user_prompt") {
          if (isReplay) {
            const userMsg = document.createElement("div");
            userMsg.className = "msg-user";
            userMsg.textContent = ev.text || "";
            chatFeed.appendChild(userMsg);
          }
          continue;
        }

        if (ev.type === "thinking" || ev.type === "thinking_delta") {
          if (isReplay) continue;
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
            thinkingNode.textContent = `${agent}` + thinkingNode.dataset.buf.slice(-200);
            chatFeed.scrollTop = chatFeed.scrollHeight;
            continue;
          }
        }
        thinkingNode = null;

        if (ev.type === "subagent_start") {
          const subId = ev.id || `sub-${Math.random().toString(36).substr(2, 6)}`;
          const role = ev.role || "general";
          const name = ev.name || "Sub-Agent";
          const icon = ev.icon || "🤖";
          const task = ev.task || "";

          const card = document.createElement("div");
          card.className = `subagent-card role-${escapeHtml(role)}`;
          card.id = `subcard-${escapeHtml(subId)}`;

          const header = document.createElement("div");
          header.className = "subagent-card-header";
          header.innerHTML = `
            <span class="subagent-chevron">▼</span>
            <span class="subagent-role-badge badge-${escapeHtml(role)}">${escapeHtml(icon)} ${escapeHtml(name)}</span>
            <span class="subagent-task-title" title="${escapeHtml(task)}">${escapeHtml(task)}</span>
            <span class="subagent-status-pill pill-running">Running</span>
          `;

          const body = document.createElement("div");
          body.className = "subagent-body";

          header.onclick = () => {
            card.classList.toggle("collapsed");
          };

          card.appendChild(header);
          card.appendChild(body);
          chatFeed.appendChild(card);

          const record = {
            id: subId,
            role,
            name,
            icon,
            task,
            status: "running",
            summary: "",
            stepsCount: 0,
            element: card,
            body: body,
            pill: header.querySelector(".subagent-status-pill"),
          };
          subagentCards.set(subId, record);
          subagentList.push(record);
          updateDrawer();
          continue;
        }

        if (ev.type === "subagent_finish") {
          const subId = ev.id;
          const record = subagentCards.get(subId);
          const success = ev.success !== false;
          const summary = ev.summary || "";
          if (record) {
            record.status = success ? "completed" : "incomplete";
            record.summary = summary;
            if (record.pill) {
              record.pill.className = `subagent-status-pill ${success ? "pill-completed" : "pill-incomplete"}`;
              record.pill.textContent = success ? "Completed" : "Incomplete";
            }
            if (summary) {
              const summaryBox = document.createElement("div");
              summaryBox.className = "subagent-summary-box";
              summaryBox.innerHTML = `<strong>Outcome:</strong> ${escapeHtml(summary)}`;
              record.body.appendChild(summaryBox);
            }
          }
          updateDrawer();
          continue;
        }

        if (ev.type === "pending_approval") {
          renderApproval(ev);
          continue;
        }
        if (ev.type === "todo") {
          latestTodos = ev.todos || [];
          updateDrawer();
          continue;
        }

        // Determine destination: subagent body or main feed
        let targetContainer = chatFeed;
        if (ev.subagent_id && subagentCards.has(ev.subagent_id)) {
          const sRec = subagentCards.get(ev.subagent_id);
          targetContainer = sRec.body;
          sRec.stepsCount++;
        }

        if (ev.type === "tool_call") {
          const card = document.createElement("div");
          card.className = "tool-card";
          const toolArgs = ev.args || {};
          let title = `Running ${ev.name}`;
          if (ev.name === "write_file") title += `: ${escapeHtml(toolArgs.path || "")}`;
          else if (ev.name === "edit_file") title += `: ${escapeHtml(toolArgs.path || "")}`;
          else if (ev.name === "run_shell") title += `: $ ${escapeHtml(toolArgs.command || "")}`;

          card.innerHTML = `<div class="tool-header">${title}</div>`;
          targetContainer.appendChild(card);
        } else if (ev.type === "tool_result") {
          const card = document.createElement("div");
          card.className = "tool-card";
          if (ev.name === "undo") {
            card.style.borderColor = "#f59e0b";
            card.innerHTML = `
              <div class="tool-header" style="color:#f59e0b">↺ File Rollback</div>
              <div class="tool-body">${escapeHtml(String(ev.result || ""))}</div>
            `;
            targetContainer.appendChild(card);
            continue;
          }
          let diffHtml = "";
          if (ev.diff) {
            const m = /\\+\\+\\+ b\\/(.+)/.exec(ev.diff);
            const targetPath = m ? m[1].trim() : "";
            if (targetPath) changedFiles.add(targetPath);
            updateDrawer();
            diffHtml = renderDiff(ev.diff, targetPath);
          }
          card.innerHTML = `
            <div class="tool-header">${escapeHtml(ev.name)} completed</div>
            <div class="tool-body">${escapeHtml(String(ev.result || "").slice(0, 600))}</div>
            ${diffHtml}
          `;
          targetContainer.appendChild(card);
        } else if (ev.type === "answer") {
          if (ev.subagent_id && subagentCards.has(ev.subagent_id)) {
            const div = document.createElement("div");
            div.className = "subagent-summary-box";
            div.innerHTML = renderMarkdown(ev.text || "");
            targetContainer.appendChild(div);
          } else {
            const div = document.createElement("div");
            div.className = "msg-agent";
            div.innerHTML = `
              <div class="msg-agent-header">SOVA Agent</div>
              ${renderMarkdown(ev.text || "")}
            `;
            targetContainer.appendChild(div);
          }
        } else if (ev.type === "error") {
          const div = document.createElement("div");
          div.className = "tool-card";
          div.style.borderColor = "var(--danger)";
          div.innerHTML = `<div class="tool-header" style="color:var(--danger)">Error</div><div class="tool-body">${escapeHtml(ev.message || "")}</div>`;
          targetContainer.appendChild(div);
        }
      }
      chatFeed.scrollTop = chatFeed.scrollHeight;
    }

    function renderDiff(diffText, path) {
      if (!diffText) return "";
      const lines = diffText.split("\\n").slice(0, 150);
      let rows = "";
      for (const line of lines) {
        let cls = "row-ctx";
        let sign = " ";
        if (line.startsWith("+")) { cls = "row-add"; sign = "+"; }
        else if (line.startsWith("-")) { cls = "row-del"; sign = "-"; }
        else if (line.startsWith("@@")) { cls = "row-hunk"; sign = "@"; }
        rows += `
          <div class="diff-inline-row ${cls}">
            <div class="gutter-sign">${sign}</div>
            <div class="line-code">${escapeHtml(line.slice(1)) || "&nbsp;"}</div>
          </div>
        `;
      }
      return `
        <div class="copilot-diff-viewer">
          <div class="diff-header">
            <span class="diff-file-path">${escapeHtml(path || "file")}</span>
          </div>
          <div class="diff-body">${rows}</div>
        </div>
      `;
    }

    function renderApproval(ev) {
      const card = document.createElement("div");
      card.className = "approval-card";
      card.innerHTML = `
        <div class="approval-header">
          <span class="approval-badge">Approval Required</span>
          <span style="font-size:11px;color:var(--text-muted);">Action: ${escapeHtml(ev.name)}</span>
        </div>
        <div style="font-size:12px;font-family:var(--font-mono);">${escapeHtml(JSON.stringify(ev.args || {}))}</div>
        <div class="approval-actions">
          <button class="btn-approve" id="appr_${ev.approval_id}">Approve Once</button>
          <button class="btn-always" id="alw_${ev.approval_id}">Always Approve</button>
          <button class="btn-deny" id="deny_${ev.approval_id}">Deny</button>
        </div>
      `;
      chatFeed.appendChild(card);
      chatFeed.scrollTop = chatFeed.scrollHeight;

      document.getElementById(`appr_${ev.approval_id}`).onclick = async () => {
        card.innerHTML = `<div style="color:var(--success);font-size:12px;font-weight:500;">Action approved once.</div>`;
        await post("/api/approve", { id: ev.approval_id });
      };
      document.getElementById(`alw_${ev.approval_id}`).onclick = async () => {
        card.innerHTML = `<div style="color:var(--accent);font-size:12px;font-weight:500;">Always approved for session.</div>`;
        autoApproveCheck.checked = true;
        await post("/api/approve", { id: ev.approval_id, always: true });
      };
      document.getElementById(`deny_${ev.approval_id}`).onclick = async () => {
        card.innerHTML = `<div style="color:var(--danger);font-size:12px;font-weight:500;">Action denied.</div>`;
        await post("/api/deny", { id: ev.approval_id });
      };
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
          drawerContent.innerHTML = `<div style="color:var(--text-muted);font-size:12px;">No active tasks</div>`;
          return;
        }
        drawerContent.innerHTML = latestTodos.map((t) =>
          `<div style="font-size:12px;color:var(--text-secondary);padding:4px 0;">• ${escapeHtml(t.content)}</div>`
        ).join("");
      } else if (currentTab === "subagents") {
        if (!subagentList.length) {
          drawerContent.innerHTML = `<div style="color:var(--text-muted);font-size:12px;text-align:center;padding:24px 8px;">No sub-agents active in this session</div>`;
          return;
        }
        drawerContent.innerHTML = subagentList.map((s) => {
          const pillCls = s.status === "completed" ? "pill-completed" : (s.status === "running" ? "pill-running" : "pill-incomplete");
          const pillText = s.status === "completed" ? "Completed" : (s.status === "running" ? "Running" : "Incomplete");
          const borderCol = s.role === "researcher" ? "#06b6d4" : (s.role === "coder" ? "#10b981" : (s.role === "reviewer" ? "#a855f7" : "#3b82f6"));
          return `
            <div class="drawer-subagent-item" style="border-left: 3px solid ${borderCol}">
              <div style="display:flex;align-items:center;justify-content:space-between;">
                <span class="subagent-role-badge badge-${escapeHtml(s.role)}">${escapeHtml(s.icon)} ${escapeHtml(s.name)}</span>
                <span class="subagent-status-pill ${pillCls}">${pillText}</span>
              </div>
              <div style="font-size:12px;color:var(--text-primary);font-weight:500;margin-top:4px;">${escapeHtml(s.task)}</div>
              ${s.summary ? `<div style="font-size:11px;color:var(--text-secondary);margin-top:4px;font-family:var(--font-mono);line-height:1.3;">${escapeHtml(s.summary.slice(0, 140))}</div>` : ""}
            </div>
          `;
        }).join("");
      } else if (currentTab === "changes") {
        if (!changedFiles.size) {
          drawerContent.innerHTML = `<div style="color:var(--text-muted);font-size:12px;">No modified files</div>`;
          return;
        }
        drawerContent.innerHTML = [...changedFiles].map((f) => `<div style="font-size:12px;color:var(--accent);">${escapeHtml(f)}</div>`).join("");
      } else if (currentTab === "logs") {
        try {
          const res = await fetch("/api/logs");
          const data = await res.json();
          const lines = data.logs || [];
          drawerContent.innerHTML = `<div style="font-family:var(--font-mono);font-size:11px;color:var(--text-muted);white-space:pre-wrap;">${escapeHtml(lines.slice(-40).join("\\n"))}</div>`;
        } catch {
          drawerContent.innerHTML = `<div style="color:var(--danger);font-size:12px;">Error reading logs</div>`;
        }
      }
    }

    // SSE Stream
    function connectStream() {
      const es = new EventSource("/api/stream?cursor=" + currentCursor);
      es.onmessage = (e) => {
        if (!e.data) return;
        try {
          const data = JSON.parse(e.data);
          const running = data.running;
          statusDot.className = "status-dot " + (running ? "running" : "idle");
          statusText.textContent = running ? "Running" : "Idle";
          const wasRunning = btnRun.disabled;
          btnRun.disabled = running;
          btnStop.disabled = !running;
          if (wasRunning && !running) {
            loadSessions();
          }
          appendEvents(data.events || []);
          currentCursor = data.cursor || currentCursor;
        } catch {}
      };
      es.onerror = () => {
        statusDot.className = "status-dot";
        statusText.textContent = "Reconnecting...";
        es.close();
        setTimeout(connectStream, 1500);
      };
    }

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
      const model = modelSelect.value;

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
      try { await post("/api/stop", {}); } catch (e) { showToast(e.message, "error"); }
    };
    btnUndo.onclick = async () => {
      try {
        const res = await post("/api/undo", {});
        if (res.ok) {
          showToast(res.message || "Last edit undone.", "success");
        } else {
          showToast(res.message || "Nothing to undo.", "info");
        }
      } catch (err) {
        showToast("Undo failed: " + err.message, "error");
      }
    };
    btnNewChat.onclick = async () => {
      try {
        const res = await post("/api/new", {});
        sessionIdDisplay.textContent = res.session_id || "";
        chatFeed.innerHTML = `<div style="text-align:center;padding:40px 20px;color:var(--text-muted);font-size:13px;"><div style="font-size:18px;font-weight:600;color:var(--text-primary);margin-bottom:6px;">SOVA Coding Agent</div><div>New conversation context started.</div></div>`;
        currentCursor = 0;
        thinkingNode = null;
        changedFiles.clear();
        latestTodos = [];
        subagentCards.clear();
        subagentList = [];
        updateDrawer();
        loadSessions();
      } catch (e) { showToast(e.message, "error"); }
    };

    taskInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        startTask();
      }
    });

    // Init
    initCatalog();
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

            if parsed.path == "/api/checkpoints":
                from .checkpoints import CheckpointManager
                sid = state["session_id"]
                cm = CheckpointManager(state["root_dir"], sid)
                return _json_response(self, 200, {"session_id": sid, "checkpoints": cm.list_checkpoints()})

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
