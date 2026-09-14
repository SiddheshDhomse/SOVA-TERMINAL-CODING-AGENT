"""Tool implementations and their JSON-schema definitions for LLM tool-calling."""
import atexit
import difflib
import fnmatch
import os
import re
import shutil
import subprocess
import uuid
from typing import Any, Dict, List, Optional, Tuple

_EXCLUDED_DIRS = {".git", ".venv", "venv", ".sova", "__pycache__", "node_modules", ".egg-info"}

_RG_PATH = shutil.which("rg")

# Global registry of active background jobs to prevent zombie process leaks
_GLOBAL_JOBS: Dict[str, Dict[str, Any]] = {}


def cleanup_jobs(job_id: Optional[str] = None) -> None:
    """Terminate running background jobs to avoid zombie processes."""
    targets = [job_id] if job_id else list(_GLOBAL_JOBS.keys())
    for jid in targets:
        job = _GLOBAL_JOBS.get(jid)
        if not job:
            continue
        proc = job.get("proc")
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=0.4)
                except (subprocess.TimeoutExpired, OSError):
                    proc.kill()
            except OSError:
                pass
        log_file = job.get("log_file")
        if log_file and not log_file.closed:
            try:
                log_file.close()
            except OSError:
                pass
        if job_id:
            _GLOBAL_JOBS.pop(jid, None)
    if not job_id:
        _GLOBAL_JOBS.clear()


atexit.register(cleanup_jobs)


def _prune_dirs(dirnames):
    dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS and not d.endswith(".egg-info")]


def _find_fuzzy_candidates(root_dir: str, path: str) -> List[str]:
    """Find relative paths of files in root_dir matching path by suffix or basename."""
    if not path or not isinstance(path, str):
        return []
    norm_target = path.replace("\\", "/").strip().lstrip("./")
    target_base = os.path.basename(norm_target)
    if not target_base:
        return []

    suffix_matches: List[str] = []
    base_matches: List[str] = []
    case_insensitive_matches: List[str] = []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        _prune_dirs(dirnames)
        for fname in filenames:
            full_f = os.path.join(dirpath, fname)
            rel = os.path.relpath(full_f, root_dir).replace(os.sep, "/")

            if rel == norm_target or rel.endswith("/" + norm_target):
                suffix_matches.append(rel)
            elif fname == target_base:
                base_matches.append(rel)
            elif fname.lower() == target_base.lower() or rel.lower().endswith("/" + norm_target.lower()):
                case_insensitive_matches.append(rel)

    if suffix_matches:
        return sorted(list(dict.fromkeys(suffix_matches)))
    if base_matches:
        return sorted(list(dict.fromkeys(base_matches)))
    if case_insensitive_matches:
        return sorted(list(dict.fromkeys(case_insensitive_matches)))
    return []



def unified_diff(path, old_text, new_text):
    """Return a unified diff string (empty if identical), used for change previews."""
    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    )
    return "".join(diff)


_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file's contents, with line numbers, from the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the working directory."},
                    "start_line": {"type": "integer", "description": "First line to read (1-indexed)."},
                    "end_line": {"type": "integer", "description": "Last line to read (inclusive)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a file or overwrite it entirely with new content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the working directory."},
                    "content": {"type": "string", "description": "Full file content to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace an occurrence of old_str with new_str in an existing file. "
                "Specify optional start_line and end_line if old_str appears in multiple places."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the working directory."},
                    "old_str": {"type": "string", "description": "Exact existing text to replace."},
                    "new_str": {"type": "string", "description": "Text to replace old_str with."},
                    "start_line": {
                        "type": "integer",
                        "description": "Optional starting line (1-indexed) to constrain replacement range.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional ending line (1-indexed) to constrain replacement range.",
                    },
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List entries in a directory relative to the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path relative to the working directory. Defaults to '.'."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search files under a path for a text or regex pattern, returning matching lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Text or regex pattern to search for."},
                    "path": {"type": "string", "description": "Path to search under. Defaults to '.'."},
                    "regex": {"type": "boolean", "description": "Treat pattern as a regex. Defaults to false."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "Run a shell command in the working directory and return its output. "
                "Use background=true for long-running commands (dev servers, watch/build tasks) "
                "that never exit on their own - it returns immediately with a job_id you poll "
                "via shell_output instead of blocking until timeout."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (foreground only). Defaults to 60."},
                    "background": {"type": "boolean", "description": "Run without blocking; returns a job_id. Defaults to false."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_output",
            "description": "Fetch the status and latest output of a background job started with run_shell(background=true).",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The job_id returned by run_shell."},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Find files under a directory whose relative path matches a glob pattern, e.g. '**/*.py'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern to match, e.g. '**/*.py' or 'src/**/*.ts'."},
                    "path": {"type": "string", "description": "Directory to search under. Defaults to '.'."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_read",
            "description": "Read this project's persistent memory notes, shared across all past and future runs.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_append",
            "description": (
                "Append a short note to this project's persistent memory, for use in future runs "
                "(e.g. build/test commands, conventions, or facts worth remembering)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "The note to remember."},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": (
                "Replace the current task checklist with the given list. Use this to plan and "
                "track progress on multi-step tasks - call it again whenever a step's status changes. "
                "Keep exactly one item 'in_progress' at a time when work is underway."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_read",
            "description": "Read the current task checklist.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Call this once the task is fully complete, with a short summary of the changes made.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Short summary of what was done."},
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_outline",
            "description": (
                "Get a concise architectural outline of classes, functions, and methods with line numbers "
                "and signatures for a file. Use this instead of reading full files to save token budget."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path to inspect (e.g. 'requests/adapters.py')."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_definition",
            "description": (
                "Find where a class, function, or method is defined across the workspace using the AST symbol index. "
                "Returns the file path, line bounds, signature, and docstring."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Name of the class, function, or method to find."},
                    "path": {"type": "string", "description": "Optional file path or directory filter."},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_references",
            "description": (
                "Find usages and call sites of a symbol across project files, distinguishing definitions from references."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Name of the symbol to find references for."},
                    "path": {"type": "string", "description": "Optional file path or directory filter."},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_summary",
            "description": (
                "Get high-level summary of the workspace: detected programming languages, test runners, "
                "entry points, and indexed file statistics."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo",
            "description": "Undo the most recent file modification by rolling back the file to its previous checkpoint.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Search code by natural language query or concept across the workspace using Okapi BM25 ranking. "
                "Useful when you don't know the exact symbol name or regex, e.g. 'JWT token expiration', "
                "'sqlite database pool', 'calculate discount'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query or keywords to search for."},
                    "path": {"type": "string", "description": "Optional file path or substring filter."},
                    "top_k": {"type": "integer", "description": "Maximum number of code snippets to return (default 5)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": (
                "Run test suites (auto-detects pytest, unittest, npm test, cargo test) with concise failure diagnosis. "
                "Use this tool to reproduce issues, verify bug fixes, or run specific test scripts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Optional test file or test target (e.g. 'test_auth.py')."},
                    "command": {"type": "string", "description": "Optional custom test shell command override."},
                },
                "required": [],
            },
        },
    },
]


def build_tools(root_dir, session_id=None):
    """Return (schemas, impls) with all tools bound to root_dir, restricting file access to it."""
    root_dir = os.path.abspath(root_dir)
    from .checkpoints import CheckpointManager
    checkpoint_mgr = CheckpointManager(root_dir, session_id=session_id)

    def _resolve(path):
        full = os.path.abspath(os.path.join(root_dir, path))
        if not (full == root_dir or full.startswith(root_dir + os.sep)):
            raise ValueError(f"Path '{path}' escapes the working directory")
        return full

    def _resolve_existing(path: str) -> Tuple[Optional[str], str, Optional[str], bool]:
        """Resolve path to an existing file within root_dir, with fuzzy subdirectory fallback.

        Returns:
            (full_path, relative_path, error_message, is_auto_resolved)
        """
        if not path or not isinstance(path, str) or not path.strip():
            return None, path or "", "ERROR: path cannot be empty.", False

        clean_path = path.strip()
        try:
            full = _resolve(clean_path)
        except ValueError as exc:
            return None, clean_path, f"ERROR: {exc}", False

        # 1. Exact path match
        if os.path.exists(full):
            if os.path.isdir(full):
                return None, clean_path, f"ERROR: '{clean_path}' is a directory, not a file.", False
            rel = os.path.relpath(full, root_dir).replace(os.sep, "/")
            return full, rel, None, False

        # 2. Fuzzy workspace candidate search
        candidates = _find_fuzzy_candidates(root_dir, clean_path)
        if len(candidates) == 1:
            resolved_rel = candidates[0]
            resolved_full = os.path.join(root_dir, resolved_rel)
            return resolved_full, resolved_rel, None, True
        elif len(candidates) > 1:
            return None, clean_path, f"ERROR: file '{clean_path}' not found. Did you mean one of: {candidates}?", False
        else:
            return None, clean_path, f"ERROR: file '{clean_path}' does not exist in workspace.", False

    def read_file(path, start_line=1, end_line=None, line_start=None, line_end=None):
        if line_start is not None and start_line == 1:
            start_line = line_start
        if line_end is not None and end_line is None:
            end_line = line_end

        # Handle line suffixes like path.py:10-50 or path.py#L10
        if isinstance(path, str):
            m = re.match(r"^(.*?)(?::(\d+)(?:-(\d+))?|#L?(\d+)(?:-L?(\d+))?)$", path.strip())
            if m:
                path = m.group(1)
                s_val = m.group(2) or m.group(4)
                e_val = m.group(3) or m.group(5)
                if (start_line == 1 or start_line is None) and s_val:
                    start_line = int(s_val)
                if end_line is None and e_val:
                    end_line = int(e_val)

        try:
            start_line = int(start_line) if start_line is not None else 1
        except (ValueError, TypeError):
            start_line = 1
        if start_line < 1:
            start_line = 1

        try:
            end_line = int(end_line) if end_line is not None else None
        except (ValueError, TypeError):
            end_line = None

        full, rel, err, is_auto_resolved = _resolve_existing(path)
        if err:
            return err

        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as exc:
            return f"ERROR: cannot read '{rel}': {exc}"

        prefix = f"[Auto-resolved '{path}' -> '{rel}']\n" if is_auto_resolved else ""

        if not lines:
            return prefix + "(empty file)"
        end_line = end_line or len(lines)
        chunk = lines[start_line - 1:end_line]

        # Prevent large file reads from overflowing TPM budgets
        if len(chunk) > 250:
            truncated = chunk[:250]
            output = prefix + "".join(f"{i}: {line}" for i, line in enumerate(truncated, start=start_line))
            output += f"\n... [Truncated {len(chunk) - 250} lines to preserve token budget. Use start_line={start_line + 250} to read further]\n"
            return output

        output = prefix + "".join(f"{i}: {line}" for i, line in enumerate(chunk, start=start_line))
        if len(output) > 5000:
            cut_idx = output.rfind("\n", 0, 5000)
            if cut_idx == -1:
                cut_idx = 5000
            output = output[:cut_idx] + "\n... [Truncated to stay within token limits. Use start_line/end_line to inspect specific lines]\n"
        return output

    from .diagnostics import format_diagnostic_feedback, run_fast_diagnostics

    def _normalize_content(content):
        if not isinstance(content, str):
            return content
        # Detect and repair double-escaped newlines emitted by some LLMs
        if "\\n" in content and content.count("\n") <= 1:
            content = content.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
        return content

    def write_file(path, content):
        content = _normalize_content(content)
        try:
            full = _resolve(path)
        except ValueError as exc:
            return f"ERROR: {exc}"
        old_content = ""
        if os.path.exists(full):
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    old_content = f.read()
            except Exception:
                old_content = ""
        os.makedirs(os.path.dirname(full) or root_dir, exist_ok=True)
        checkpoint_mgr.record_before_change(path, "write_file")
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        diag = run_fast_diagnostics(path, content)
        suffix = format_diagnostic_feedback(diag) if diag else ""
        return f"Wrote {len(content)} chars to {path}.{suffix}", unified_diff(path, old_content, content)

    def edit_file(path, old_str, new_str, start_line=None, end_line=None, line_start=None, line_end=None):
        if line_start is not None and start_line is None:
            start_line = line_start
        if line_end is not None and end_line is None:
            end_line = line_end

        # Handle line suffixes like path.py:10-50 or path.py#L10
        if isinstance(path, str):
            m = re.match(r"^(.*?)(?::(\d+)(?:-(\d+))?|#L?(\d+)(?:-L?(\d+))?)$", path.strip())
            if m:
                path = m.group(1)
                s_val = m.group(2) or m.group(4)
                e_val = m.group(3) or m.group(5)
                if start_line is None and s_val:
                    start_line = int(s_val)
                if end_line is None and e_val:
                    end_line = int(e_val)

        def _clean_line_no(val):
            if val is None:
                return None
            if isinstance(val, str):
                val_s = val.strip().lower()
                if val_s in ("none", "null", ""):
                    return None
                try:
                    return int(val)
                except ValueError:
                    return None
            try:
                return int(val)
            except (TypeError, ValueError):
                return None

        start_line = _clean_line_no(start_line)
        end_line = _clean_line_no(end_line)
        if not old_str:
            return (
                "ERROR: old_str cannot be empty. To insert or prepend code into an existing file, "
                "read the file first, select an existing anchor line for old_str, and include both "
                "your new code and the anchor line in new_str (for example, to add at the top of a file: "
                "old_str='import tkinter as tk', new_str='# build by SOVA\\nimport tkinter as tk'). "
                "DO NOT use write_file to insert a line or comment, as write_file will destroy the rest of the file!"
            )

        full, rel, err, is_auto_resolved = _resolve_existing(path)
        if err:
            return err

        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as exc:
            return f"ERROR: cannot read '{rel}': {exc}"

        has_crlf = "\r\n" in content

        # Normalize CRLF/LF to prevent cross-platform line ending mismatch
        content_norm = content.replace("\r\n", "\n")
        old_norm = old_str.replace("\r\n", "\n")
        new_norm = _normalize_content(new_str).replace("\r\n", "\n")

        if start_line is not None or end_line is not None:
            lines = content_norm.splitlines(keepends=True)
            s_idx = max(0, (start_line - 1)) if start_line else 0
            e_idx = min(len(lines), end_line) if end_line else len(lines)
            target_slice = "".join(lines[s_idx:e_idx])
            count = target_slice.count(old_norm)
            if count == 0:
                return f"ERROR: old_str not found in {rel} between lines {s_idx + 1} and {e_idx}."
            if count > 1:
                return f"ERROR: old_str matches {count} times between lines {s_idx + 1} and {e_idx}. Narrow the range."
            new_slice = target_slice.replace(old_norm, new_norm, 1)
            new_content = "".join(lines[:s_idx]) + new_slice + "".join(lines[e_idx:])
        else:
            count = content_norm.count(old_norm)
            if count == 0:
                if old_norm.strip() in content_norm:
                    return f"ERROR: old_str not found in {rel}. Check indentation or whitespace differences against read_file."
                return f"ERROR: old_str not found in {rel}."
            if count > 1:
                return f"ERROR: old_str matches {count} times in {rel}. Provide start_line and end_line to narrow down target location."
            new_content = content_norm.replace(old_norm, new_norm, 1)

        final_content = new_content.replace("\r\n", "\n").replace("\n", "\r\n") if has_crlf else new_content

        checkpoint_mgr.record_before_change(rel, "edit_file")
        with open(full, "w", encoding="utf-8") as f:
            f.write(final_content)
        diag = run_fast_diagnostics(rel, final_content)
        suffix = format_diagnostic_feedback(diag) if diag else ""
        notice = f" (auto-resolved from '{path}')" if is_auto_resolved else ""
        return f"Edited {rel}{notice}.{suffix}", unified_diff(rel, content, final_content)

    def list_dir(path="."):
        try:
            full = _resolve(path)
        except ValueError as exc:
            return f"ERROR: {exc}"
        if not os.path.exists(full):
            return f"ERROR: directory '{path}' does not exist."
        if not os.path.isdir(full):
            return f"ERROR: '{path}' is a file, not a directory."
        try:
            entries = sorted(os.listdir(full))
        except OSError as exc:
            return f"ERROR: cannot list '{path}': {exc}"
        return "\n".join(entries[:60]) if entries else "(empty)"

    def _find_files_fallback(pattern, full):
        matches = []
        for dirpath, dirnames, filenames in os.walk(full):
            _prune_dirs(dirnames)
            for name in filenames:
                fp = os.path.join(dirpath, name)
                rel = os.path.relpath(fp, full).replace(os.sep, "/")
                if fnmatch.fnmatch(rel, pattern):
                    matches.append(rel)
                    if len(matches) >= 60:
                        return matches
        return matches

    def find_files(pattern, path="."):
        try:
            full = _resolve(path)
        except ValueError as exc:
            return f"ERROR: {exc}"
        if not os.path.exists(full) or not os.path.isdir(full):
            return f"ERROR: directory '{path}' does not exist."
        if _RG_PATH:
            try:
                result = subprocess.run(
                    [_RG_PATH, "--files", "--hidden", "--glob", "!.git", "-g", pattern],
                    cwd=full, capture_output=True, text=True, timeout=20,
                )
                matches = [line for line in result.stdout.splitlines() if line][:60]
            except (OSError, subprocess.SubprocessError):
                matches = _find_files_fallback(pattern, full)
        else:
            matches = _find_files_fallback(pattern, full)
        return "\n".join(matches) if matches else "No files match that pattern"

    memory_path = os.path.join(root_dir, ".sova", "memory.md")

    def memory_read():
        if not os.path.exists(memory_path):
            return "(memory is empty)"
        with open(memory_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return content or "(memory is empty)"

    def memory_append(note):
        os.makedirs(os.path.dirname(memory_path), exist_ok=True)
        with open(memory_path, "a", encoding="utf-8") as f:
            f.write(f"- {note.strip()}\n")
        return "Saved to memory"

    def _grep_fallback(pattern, full, regex):
        matcher = re.compile(pattern).search if regex else (lambda line: pattern in line)
        matches = []
        for dirpath, dirnames, filenames in os.walk(full):
            _prune_dirs(dirnames)
            for name in filenames:
                fp = os.path.join(dirpath, name)
                rel = os.path.relpath(fp, root_dir)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f, start=1):
                            if matcher(line):
                                matches.append(f"{rel}:{i}: {line.strip()}")
                                if len(matches) >= 50:
                                    return matches
                except IsADirectoryError:
                    continue
        return matches

    def grep(pattern, path=".", regex=False):
        try:
            full = _resolve(path)
        except ValueError as exc:
            return f"ERROR: {exc}"
        if not os.path.exists(full):
            return f"ERROR: path '{path}' does not exist."
        if _RG_PATH:
            cmd = [_RG_PATH, "--line-number", "--hidden", "--glob", "!.git", "--max-count", "50"]
            if not regex:
                cmd.append("--fixed-strings")
            cmd += [pattern, "."]
            try:
                result = subprocess.run(cmd, cwd=full, capture_output=True, text=True, timeout=20)
                if result.returncode not in (0, 1):
                    matches = _grep_fallback(pattern, full, regex)
                else:
                    matches = [line.lstrip("./").replace("\\", "/") for line in result.stdout.splitlines()][:50]
            except (OSError, subprocess.SubprocessError, re.error):
                matches = _grep_fallback(pattern, full, regex)
        else:
            try:
                matches = _grep_fallback(pattern, full, regex)
            except re.error as exc:
                return f"ERROR: invalid regex: {exc}"
        return "\n".join(matches) if matches else "No matches"

    jobs_dir = os.path.join(root_dir, ".sova", "jobs")

    def run_shell(command, timeout=60, background=False):
        if background:
            os.makedirs(jobs_dir, exist_ok=True)
            job_id = uuid.uuid4().hex[:8]
            log_path = os.path.join(jobs_dir, f"{job_id}.log")
            log_file = open(log_path, "w", encoding="utf-8")
            proc = subprocess.Popen(
                command, shell=True, cwd=root_dir, stdout=log_file, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True,
            )
            job_record = {"proc": proc, "log_path": log_path, "log_file": log_file}
            _GLOBAL_JOBS[job_id] = job_record
            return f"Started background job {job_id}. Poll with shell_output(job_id='{job_id}')."
        try:
            result = subprocess.run(
                command, shell=True, cwd=root_dir, capture_output=True, text=True, timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
            output = result.stdout + result.stderr
            # Cap shell output to prevent token budget explosion
            if len(output) > 2500:
                output = output[:1000] + f"\n... [Truncated: {len(output) - 2000} chars omitted to stay under TPM limits] ...\n" + output[-1000:]
            return f"exit_code={result.returncode}\n{output}"
        except subprocess.TimeoutExpired:
            return f"ERROR: command timed out after {timeout}s"

    def shell_output(job_id):
        job = _GLOBAL_JOBS.get(job_id)
        if job is None:
            return f"ERROR: unknown job_id '{job_id}'"
        proc = job["proc"]
        finished = proc.poll() is not None
        status = f"exited with code {proc.returncode}" if finished else "running"
        if finished and not job["log_file"].closed:
            job["log_file"].close()
        try:
            with open(job["log_path"], "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
                output = raw[-2000:] if len(raw) > 2000 else raw
        except OSError:
            output = ""
        return f"status={status}\n{output}"

    _todos = []

    def todo_write(todos):
        _todos[:] = todos
        return f"Saved {len(_todos)} todo(s)."

    def todo_read():
        return _todos if _todos else "(no todos yet)"

    def finish(summary):
        return summary

    from .symbols import WorkspaceSymbolIndex
    symbol_index = WorkspaceSymbolIndex(root_dir)

    def get_outline(path):
        if isinstance(path, str):
            m = re.match(r"^(.*?)(?::\d+(?:-\d+)?|#L?\d+(?:-L?\d+)?)$", path.strip())
            if m:
                path = m.group(1)
        full, rel, err, _ = _resolve_existing(path)
        if err:
            return err
        return symbol_index.get_outline(rel)

    def find_definition(symbol, path=None):
        if path:
            _, rel, err, _ = _resolve_existing(path)
            if not err:
                path = rel
        return symbol_index.find_definition(symbol, path_filter=path)

    def find_references(symbol, path=None):
        if path:
            _, rel, err, _ = _resolve_existing(path)
            if not err:
                path = rel
        return symbol_index.find_references(symbol, path_filter=path)

    def workspace_summary():
        return symbol_index.get_workspace_summary()

    from .search import BM25Index
    search_index = BM25Index(root_dir)

    def search_code(query, path=None, top_k=5):
        if path:
            _, rel, err, _ = _resolve_existing(path)
            if not err:
                path = rel
        return search_index.format_search_results(query, path_filter=path, top_k=top_k)

    from .testing import run_tests as _exec_tests

    def run_tests(target=None, command=None):
        return _exec_tests(root_dir, target=target, command=command)

    impls = {
        "read_file": read_file,
        "write_file": write_file,
        "edit_file": edit_file,
        "list_dir": list_dir,
        "find_files": find_files,
        "memory_read": memory_read,
        "memory_append": memory_append,
        "grep": grep,
        "run_shell": run_shell,
        "shell_output": shell_output,
        "todo_write": todo_write,
        "todo_read": todo_read,
        "get_outline": get_outline,
        "find_definition": find_definition,
        "find_references": find_references,
        "workspace_summary": workspace_summary,
        "search_code": search_code,
        "run_tests": run_tests,
        "finish": finish,
        "undo": checkpoint_mgr.undo_last,
        "_checkpoints": checkpoint_mgr,
    }
    return _SCHEMAS, impls
