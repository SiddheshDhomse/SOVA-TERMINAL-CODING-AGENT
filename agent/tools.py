"""Tool implementations and their JSON-schema definitions for LLM tool-calling."""
import difflib
import fnmatch
import os
import re
import shutil
import subprocess
import uuid

_EXCLUDED_DIRS = {".git", ".venv", "venv", ".sova", "__pycache__", "node_modules", ".egg-info"}

_RG_PATH = shutil.which("rg")


def _prune_dirs(dirnames):
    dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS and not d.endswith(".egg-info")]


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
            "description": "Replace one unique occurrence of old_str with new_str in an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the working directory."},
                    "old_str": {"type": "string", "description": "Exact existing text to replace, must be unique in the file."},
                    "new_str": {"type": "string", "description": "Text to replace old_str with."},
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
]


def build_tools(root_dir):
    """Return (schemas, impls) with all tools bound to root_dir, restricting file access to it."""
    root_dir = os.path.abspath(root_dir)

    def _resolve(path):
        full = os.path.abspath(os.path.join(root_dir, path))
        if not (full == root_dir or full.startswith(root_dir + os.sep)):
            raise ValueError(f"Path '{path}' escapes the working directory")
        return full

    def read_file(path, start_line=1, end_line=None):
        full = _resolve(path)
        with open(full, "r", encoding="utf-8") as f:
            lines = f.readlines()
        end_line = end_line or len(lines)
        chunk = lines[start_line - 1:end_line]
        return "".join(f"{i}: {line}" for i, line in enumerate(chunk, start=start_line))

    def _check_python_syntax(path, content):
        if not path.endswith(".py"):
            return None
        try:
            compile(content, path, "exec")
            return None
        except SyntaxError as exc:
            return f"SyntaxError: {exc.msg} (line {exc.lineno})"

    def write_file(path, content):
        full = _resolve(path)
        old_content = ""
        if os.path.exists(full):
            try:
                with open(full, "r", encoding="utf-8") as f:
                    old_content = f.read()
            except UnicodeDecodeError:
                old_content = ""
        os.makedirs(os.path.dirname(full) or root_dir, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        error = _check_python_syntax(path, content)
        suffix = f" ERROR: {error}" if error else ""
        return f"Wrote {len(content)} chars to {path}.{suffix}", unified_diff(path, old_content, content)

    def edit_file(path, old_str, new_str):
        full = _resolve(path)
        if not old_str:
            return "ERROR: old_str cannot be empty. Use write_file to replace a file's entire contents."
        with open(full, "r", encoding="utf-8") as f:
            content = f.read()
        count = content.count(old_str)
        if count == 0:
            return f"ERROR: old_str not found in {path}"
        if count > 1:
            return f"ERROR: old_str matches {count} times in {path}, must be unique"
        new_content = content.replace(old_str, new_str, 1)
        with open(full, "w", encoding="utf-8") as f:
            f.write(new_content)
        error = _check_python_syntax(path, new_content)
        suffix = f" ERROR: {error}" if error else ""
        return f"Edited {path}.{suffix}", unified_diff(path, content, new_content)

    def list_dir(path="."):
        full = _resolve(path)
        entries = sorted(os.listdir(full))
        return "\n".join(entries) if entries else "(empty)"

    def _find_files_fallback(pattern, full):
        matches = []
        for dirpath, dirnames, filenames in os.walk(full):
            _prune_dirs(dirnames)
            for name in filenames:
                fp = os.path.join(dirpath, name)
                rel = os.path.relpath(fp, full).replace(os.sep, "/")
                if fnmatch.fnmatch(rel, pattern):
                    matches.append(rel)
                    if len(matches) >= 200:
                        return matches
        return matches

    def find_files(pattern, path="."):
        full = _resolve(path)
        if _RG_PATH:
            try:
                result = subprocess.run(
                    [_RG_PATH, "--files", "--hidden", "--glob", "!.git", "-g", pattern],
                    cwd=full, capture_output=True, text=True, timeout=20,
                )
                matches = [line for line in result.stdout.splitlines() if line][:200]
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
                                if len(matches) >= 200:
                                    return matches
                except IsADirectoryError:
                    continue
        return matches

    def grep(pattern, path=".", regex=False):
        full = _resolve(path)
        if _RG_PATH:
            cmd = [_RG_PATH, "--line-number", "--hidden", "--glob", "!.git", "--max-count", "200"]
            if not regex:
                cmd.append("--fixed-strings")
            cmd += [pattern, "."]
            try:
                result = subprocess.run(cmd, cwd=full, capture_output=True, text=True, timeout=20)
                if result.returncode not in (0, 1):
                    matches = _grep_fallback(pattern, full, regex)
                else:
                    matches = [line.lstrip("./").replace("\\", "/") for line in result.stdout.splitlines()][:200]
            except (OSError, subprocess.SubprocessError, re.error):
                matches = _grep_fallback(pattern, full, regex)
        else:
            try:
                matches = _grep_fallback(pattern, full, regex)
            except re.error as exc:
                return f"ERROR: invalid regex: {exc}"
        return "\n".join(matches) if matches else "No matches"

    _jobs = {}  # job_id -> {"proc": Popen, "log_path": str}
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
            _jobs[job_id] = {"proc": proc, "log_path": log_path, "log_file": log_file}
            return f"Started background job {job_id}. Poll with shell_output(job_id='{job_id}')."
        try:
            result = subprocess.run(
                command, shell=True, cwd=root_dir, capture_output=True, text=True, timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
            output = (result.stdout + result.stderr)[-4000:]
            return f"exit_code={result.returncode}\n{output}"
        except subprocess.TimeoutExpired:
            return f"ERROR: command timed out after {timeout}s"

    def shell_output(job_id):
        job = _jobs.get(job_id)
        if job is None:
            return f"ERROR: unknown job_id '{job_id}'"
        proc = job["proc"]
        finished = proc.poll() is not None
        status = f"exited with code {proc.returncode}" if finished else "running"
        if finished and not job["log_file"].closed:
            job["log_file"].close()
        try:
            with open(job["log_path"], "r", encoding="utf-8", errors="ignore") as f:
                output = f.read()[-4000:]
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
        "finish": finish,
    }
    return _SCHEMAS, impls
