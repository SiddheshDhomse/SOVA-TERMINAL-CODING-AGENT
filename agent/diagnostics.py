"""Post-edit diagnostics and syntax validation for SOVA's self-healing code loop."""
import ast
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class DiagnosticError:
    path: str
    line: int
    column: int
    message: str
    error_type: str
    snippet: str = ""

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.column}: [{self.error_type}] {self.message}"


_RUFF_PATH = shutil.which("ruff")


def check_python_syntax(path: str, content: str) -> Optional[DiagnosticError]:
    """Check Python content for syntax errors and AST compilation errors."""
    try:
        ast.parse(content, filename=path)
        compile(content, path, "exec")
        return None
    except SyntaxError as exc:
        line_no = exc.lineno or 1
        col_no = exc.offset or 0
        lines = content.splitlines()
        snippet = ""
        if 1 <= line_no <= len(lines):
            snippet = lines[line_no - 1]
            if col_no > 0:
                snippet += "\n" + " " * (col_no - 1) + "^"

        return DiagnosticError(
            path=path,
            line=line_no,
            column=col_no,
            message=exc.msg or "Invalid syntax",
            error_type="SyntaxError",
            snippet=snippet,
        )


def check_json_syntax(path: str, content: str) -> Optional[DiagnosticError]:
    """Check JSON content for decode errors."""
    try:
        json.loads(content)
        return None
    except json.JSONDecodeError as exc:
        lines = content.splitlines()
        snippet = ""
        if 1 <= exc.lineno <= len(lines):
            snippet = lines[exc.lineno - 1]
            if exc.colno > 0:
                snippet += "\n" + " " * (exc.colno - 1) + "^"

        return DiagnosticError(
            path=path,
            line=exc.lineno,
            column=exc.colno,
            message=exc.msg,
            error_type="JSONDecodeError",
            snippet=snippet,
        )


def check_bracket_balance(path: str, content: str) -> Optional[DiagnosticError]:
    """Simple syntactic balance checker for JS, TS, and related brace languages."""
    stack = []
    pairs = {')': '(', ']': '[', '}': '{'}
    openers = set(pairs.values())
    closers = set(pairs.keys())

    # Strip comments and strings to avoid false positives
    cleaned = []
    in_single = False
    in_double = False
    in_backtick = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    n = len(content)

    line = 1
    col = 1
    line_starts = [0]

    while i < n:
        c = content[i]
        nxt = content[i + 1] if i + 1 < n else ""

        if c == "\n":
            line += 1
            col = 1
            line_starts.append(i + 1)
            in_line_comment = False
            i += 1
            continue

        if in_line_comment:
            i += 1
            col += 1
            continue

        if in_block_comment:
            if c == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                col += 2
                continue
            i += 1
            col += 1
            continue

        if in_single:
            if c == "\\" and nxt:
                i += 2
                col += 2
                continue
            if c == "'":
                in_single = False
            i += 1
            col += 1
            continue

        if in_double:
            if c == "\\" and nxt:
                i += 2
                col += 2
                continue
            if c == '"':
                in_double = False
            i += 1
            col += 1
            continue

        if in_backtick:
            if c == "\\" and nxt:
                i += 2
                col += 2
                continue
            if c == "`":
                in_backtick = False
            i += 1
            col += 1
            continue

        # Check comment starters
        if c == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            col += 2
            continue
        if c == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            col += 2
            continue

        # Check string starters
        if c == "'":
            in_single = True
            i += 1
            col += 1
            continue
        if c == '"':
            in_double = True
            i += 1
            col += 1
            continue
        if c == "`":
            in_backtick = True
            i += 1
            col += 1
            continue

        # Check brackets
        if c in openers:
            stack.append((c, line, col))
        elif c in closers:
            if not stack:
                lines = content.splitlines()
                snippet = lines[line - 1] if 1 <= line <= len(lines) else ""
                return DiagnosticError(
                    path=path,
                    line=line,
                    column=col,
                    message=f"Unmatched closing bracket '{c}'",
                    error_type="BracketMismatch",
                    snippet=snippet,
                )
            opener, o_line, o_col = stack.pop()
            if pairs[c] != opener:
                lines = content.splitlines()
                snippet = lines[line - 1] if 1 <= line <= len(lines) else ""
                return DiagnosticError(
                    path=path,
                    line=line,
                    column=col,
                    message=f"Mismatched bracket: expected closing for '{opener}' (opened at line {o_line}:{o_col}), got '{c}'",
                    error_type="BracketMismatch",
                    snippet=snippet,
                )

        i += 1
        col += 1

    if stack:
        opener, o_line, o_col = stack[-1]
        lines = content.splitlines()
        snippet = lines[o_line - 1] if 1 <= o_line <= len(lines) else ""
        return DiagnosticError(
            path=path,
            line=o_line,
            column=o_col,
            message=f"Unclosed bracket '{opener}' opened at line {o_line}:{o_col}",
            error_type="UnclosedBracket",
            snippet=snippet,
        )

    return None


def run_fast_diagnostics(path: str, content: str) -> Optional[DiagnosticError]:
    """Inspect file content for syntax and parse errors across known file types."""
    ext = os.path.splitext(path)[1].lower()

    if ext == ".py":
        err = check_python_syntax(path, content)
        if err:
            return err
        # If ruff is installed, optionally check for critical errors
        if _RUFF_PATH:
            try:
                proc = subprocess.run(
                    [_RUFF_PATH, "check", "--stdin-filename", path, "--select=E9,F63,F7,F82", "-"],
                    input=content, text=True, capture_output=True, timeout=2.0
                )
                if proc.returncode != 0 and proc.stdout:
                    first_line = proc.stdout.strip().splitlines()[0]
                    # Parse ruff output: path:line:col: code message
                    match = re.match(r"^.*?:(\d+):(\d+):\s*(.*)$", first_line)
                    if match:
                        l = int(match.group(1))
                        c = int(match.group(2))
                        msg = match.group(3)
                        lines = content.splitlines()
                        snippet = lines[l - 1] if 1 <= l <= len(lines) else ""
                        return DiagnosticError(
                            path=path, line=l, column=c, message=msg, error_type="LinterError", snippet=snippet
                        )
            except Exception:
                pass
        return None

    if ext == ".json":
        return check_json_syntax(path, content)

    if ext in (".js", ".jsx", ".ts", ".tsx", ".c", ".cpp", ".java", ".rs", ".go"):
        return check_bracket_balance(path, content)

    return None


def format_diagnostic_feedback(diag: DiagnosticError) -> str:
    """Format a diagnostic error into a standardized actionable warning for the LLM."""
    snippet_str = f"\n  {diag.snippet}" if diag.snippet else ""
    return (
        f"\n[WARNING] SYNTAX/LINT ERROR DETECTED in {diag.path}:{diag.line}:{diag.column} [{diag.error_type}]: {diag.message}"
        f"{snippet_str}\n"
        f"Please auto-repair this file before proceeding to the next step."
    )
