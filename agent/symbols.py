"""AST symbol extraction, workspace indexing, and codebase intelligence for SOVA."""
import ast
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SymbolInfo:
    name: str
    kind: str  # "class", "function", "async_function", "method", "interface", "type"
    file_path: str  # relative path
    line: int
    end_line: int
    signature: str = ""
    docstring: str = ""
    parent: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SymbolInfo":
        return cls(**data)


_IGNORE_DIRS = {
    ".git",
    ".sova",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "build",
    "dist",
    "eggs",
    ".egg-info",
}

_CODE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
}


def _format_py_arg(arg: ast.arg, default: Optional[ast.AST] = None) -> str:
    res = arg.arg
    if arg.annotation:
        try:
            res += f": {ast.unparse(arg.annotation)}"
        except Exception:
            pass
    if default:
        try:
            res += f"={ast.unparse(default)}"
        except Exception:
            pass
    return res


def _extract_py_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    arg_strs: List[str] = []

    # Positional only args (Python 3.8+)
    posonly = getattr(args, "posonlyargs", [])
    for a in posonly:
        arg_strs.append(_format_py_arg(a))
    if posonly:
        arg_strs.append("/")

    # Standard args and defaults
    num_defaults = len(args.defaults)
    num_args = len(args.args)
    default_offset = num_args - num_defaults
    for i, a in enumerate(args.args):
        default = args.defaults[i - default_offset] if i >= default_offset else None
        arg_strs.append(_format_py_arg(a, default))

    # Vararg *args
    if args.vararg:
        var_ann = f": {ast.unparse(args.vararg.annotation)}" if getattr(args.vararg, "annotation", None) else ""
        arg_strs.append(f"*{args.vararg.arg}{var_ann}")

    # Kwonly args
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        arg_strs.append(_format_py_arg(a, d))

    # Kwarg **kwargs
    if args.kwarg:
        kw_ann = f": {ast.unparse(args.kwarg.annotation)}" if getattr(args.kwarg, "annotation", None) else ""
        arg_strs.append(f"**{args.kwarg.arg}{kw_ann}")

    ret = ""
    if node.returns:
        try:
            ret = f" -> {ast.unparse(node.returns)}"
        except Exception:
            pass

    return f"({', '.join(arg_strs)}){ret}"


def _extract_py_docstring(node: ast.AST) -> str:
    doc = ast.get_docstring(node) or ""
    if doc:
        first_line = doc.strip().splitlines()[0]
        return first_line[:120]
    return ""


def parse_python_symbols(rel_path: str, content: str) -> List[SymbolInfo]:
    symbols: List[SymbolInfo] = []
    try:
        tree = ast.parse(content, filename=rel_path)
    except SyntaxError:
        return symbols

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = []
            for b in node.bases:
                try:
                    bases.append(ast.unparse(b))
                except Exception:
                    pass
            base_str = f"({', '.join(bases)})" if bases else ""
            doc = _extract_py_docstring(node)
            end_line = getattr(node, "end_lineno", node.lineno)

            symbols.append(
                SymbolInfo(
                    name=node.name,
                    kind="class",
                    file_path=rel_path,
                    line=node.lineno,
                    end_line=end_line,
                    signature=base_str,
                    docstring=doc,
                )
            )

            # Extract methods inside class
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m_kind = "method"
                    m_sig = _extract_py_signature(item)
                    m_doc = _extract_py_docstring(item)
                    m_end = getattr(item, "end_lineno", item.lineno)
                    symbols.append(
                        SymbolInfo(
                            name=item.name,
                            kind=m_kind,
                            file_path=rel_path,
                            line=item.lineno,
                            end_line=m_end,
                            signature=m_sig,
                            docstring=m_doc,
                            parent=node.name,
                        )
                    )

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            f_kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            f_sig = _extract_py_signature(node)
            f_doc = _extract_py_docstring(node)
            f_end = getattr(node, "end_lineno", node.lineno)
            symbols.append(
                SymbolInfo(
                    name=node.name,
                    kind=f_kind,
                    file_path=rel_path,
                    line=node.lineno,
                    end_line=f_end,
                    signature=f_sig,
                    docstring=f_doc,
                )
            )

    return symbols


_JS_TS_CLASS = re.compile(r"^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z0-9_$]+)(?:\s+extends\s+([A-Za-z0-9_$.]+))?", re.MULTILINE)
_JS_TS_FUNC = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*([A-Za-z0-9_$]+)\s*\((.*?)\)", re.MULTILINE)
_JS_TS_CONST_FUNC = re.compile(r"^(?:export\s+)?const\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\((.*?)\)\s*=>", re.MULTILINE)
_GO_FUNC = re.compile(r"^func\s+(?:\((?:[A-Za-z0-9_*]+\s+)?\*?([A-Za-z0-9_]+)\)\s+)?([A-Za-z0-9_]+)\s*\((.*?)\)", re.MULTILINE)
_RUST_FN = re.compile(r"^(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z0-9_]+)\s*(?:<.*?>)?\s*\((.*?)\)", re.MULTILINE)


def parse_regex_symbols(rel_path: str, content: str, lang: str) -> List[SymbolInfo]:
    symbols: List[SymbolInfo] = []
    lines = content.splitlines()

    def _line_num(char_pos: int) -> int:
        return content.count("\n", 0, char_pos) + 1

    if lang in ("javascript", "typescript"):
        for m in _JS_TS_CLASS.finditer(content):
            name = m.group(1)
            ext = f" extends {m.group(2)}" if m.group(2) else ""
            ln = _line_num(m.start())
            symbols.append(SymbolInfo(name=name, kind="class", file_path=rel_path, line=ln, end_line=ln, signature=ext))
        for m in _JS_TS_FUNC.finditer(content):
            name = m.group(1)
            sig = f"({m.group(2).strip()})"
            ln = _line_num(m.start())
            symbols.append(SymbolInfo(name=name, kind="function", file_path=rel_path, line=ln, end_line=ln, signature=sig))
        for m in _JS_TS_CONST_FUNC.finditer(content):
            name = m.group(1)
            sig = f"({m.group(2).strip()}) =>"
            ln = _line_num(m.start())
            symbols.append(SymbolInfo(name=name, kind="function", file_path=rel_path, line=ln, end_line=ln, signature=sig))

    elif lang == "go":
        for m in _GO_FUNC.finditer(content):
            receiver = m.group(1)
            name = m.group(2)
            sig = f"({m.group(3).strip()})"
            ln = _line_num(m.start())
            kind = "method" if receiver else "function"
            symbols.append(SymbolInfo(name=name, kind=kind, file_path=rel_path, line=ln, end_line=ln, signature=sig, parent=receiver))

    elif lang == "rust":
        for m in _RUST_FN.finditer(content):
            name = m.group(1)
            sig = f"({m.group(2).strip()})"
            ln = _line_num(m.start())
            symbols.append(SymbolInfo(name=name, kind="function", file_path=rel_path, line=ln, end_line=ln, signature=sig))

    return symbols


class WorkspaceSymbolIndex:
    """Incremental, high-performance AST symbol index for the active workspace."""

    def __init__(self, root_dir: str):
        self.root_dir = os.path.abspath(root_dir)
        self.cache_dir = os.path.join(self.root_dir, ".sova")
        self.cache_file = os.path.join(self.cache_dir, "symbols_cache.json")
        self.files_cache: Dict[str, Dict[str, Any]] = {}
        self._load_cache()
        self.scan_workspace()

    def _load_cache(self) -> None:
        if not os.path.exists(self.cache_file):
            return
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("version") == 1 and isinstance(data.get("files"), dict):
                    self.files_cache = data["files"]
        except Exception:
            self.files_cache = {}

    def _save_cache(self) -> None:
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "files": self.files_cache}, f, indent=2)
        except Exception:
            pass

    def scan_workspace(self, max_files: int = 1000) -> int:
        """Scan workspace incrementally, re-parsing only changed files."""
        indexed_count = 0
        current_rel_paths = set()

        for dirpath, dirnames, filenames in os.walk(self.root_dir):
            # Prune ignored directories in-place
            dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]

            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                lang = _CODE_EXTENSIONS.get(ext)
                if not lang:
                    continue

                full_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(full_path, self.root_dir).replace("\\", "/")
                current_rel_paths.add(rel_path)

                try:
                    mtime = os.path.getmtime(full_path)
                except OSError:
                    continue

                cached_entry = self.files_cache.get(rel_path)
                if cached_entry and cached_entry.get("mtime") == mtime:
                    continue  # Up to date

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except OSError:
                    continue

                if lang == "python":
                    symbols = parse_python_symbols(rel_path, content)
                else:
                    symbols = parse_regex_symbols(rel_path, content, lang)

                self.files_cache[rel_path] = {
                    "mtime": mtime,
                    "symbols": [s.to_dict() for s in symbols],
                }
                indexed_count += 1
                if len(current_rel_paths) >= max_files:
                    break
            if len(current_rel_paths) >= max_files:
                break

        # Prune deleted files from cache
        stale_keys = [k for k in self.files_cache if k not in current_rel_paths]
        for k in stale_keys:
            del self.files_cache[k]

        if indexed_count > 0 or stale_keys:
            self._save_cache()

        return indexed_count

    def get_file_symbols(self, rel_path: str) -> List[SymbolInfo]:
        norm_path = rel_path.replace("\\", "/").lstrip("./")
        entry = self.files_cache.get(norm_path)
        if not entry:
            # Try on-demand scan for this single file
            full_path = os.path.join(self.root_dir, norm_path)
            if os.path.exists(full_path):
                ext = os.path.splitext(norm_path)[1].lower()
                lang = _CODE_EXTENSIONS.get(ext)
                if lang:
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        syms = parse_python_symbols(norm_path, content) if lang == "python" else parse_regex_symbols(norm_path, content, lang)
                        mtime = os.path.getmtime(full_path)
                        self.files_cache[norm_path] = {"mtime": mtime, "symbols": [s.to_dict() for s in syms]}
                        self._save_cache()
                        return syms
                    except OSError:
                        pass
            return []
        return [SymbolInfo.from_dict(d) for d in entry.get("symbols", [])]

    def get_outline(self, rel_path: str) -> str:
        """Format a high-density architectural outline for the given file."""
        norm_path = rel_path.replace("\\", "/").lstrip("./")
        full_path = os.path.join(self.root_dir, norm_path)
        if not os.path.exists(full_path):
            return f"ERROR: file '{rel_path}' does not exist."

        symbols = self.get_file_symbols(norm_path)
        if not symbols:
            # Check line count
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    line_count = len(f.readlines())
                return f"{norm_path} ({line_count} lines): No top-level classes or functions found."
            except OSError as e:
                return f"ERROR: cannot read '{rel_path}': {e}"

        total_lines = max((s.end_line for s in symbols), default=1)
        lines: List[str] = [f"{norm_path} (lines 1-{total_lines}):"]

        classes = [s for s in symbols if s.kind == "class"]
        methods_by_parent: Dict[str, List[SymbolInfo]] = {}
        for s in symbols:
            if s.parent:
                methods_by_parent.setdefault(s.parent, []).append(s)

        for cls in classes:
            doc = f" - {cls.docstring}" if cls.docstring else ""
            span = f" (lines {cls.line}-{cls.end_line})"
            lines.append(f"  class {cls.name}{cls.signature}{span}:{doc}")
            for m in methods_by_parent.get(cls.name, []):
                m_doc = f" - {m.docstring}" if m.docstring else ""
                lines.append(f"    def {m.name}{m.signature} (L{m.line}-{m.end_line}){m_doc}")

        # Top-level functions
        top_funcs = [s for s in symbols if s.kind in ("function", "async_function") and not s.parent]
        for f in top_funcs:
            f_prefix = "async def " if f.kind == "async_function" else "def "
            f_doc = f" - {f.docstring}" if f.docstring else ""
            span = f" (lines {f.line}-{f.end_line})"
            lines.append(f"  {f_prefix}{f.name}{f.signature}{span}{f_doc}")

        return "\n".join(lines)

    def find_definition(self, symbol_name: str, path_filter: Optional[str] = None) -> str:
        """Search the symbol index for classes, functions, or methods matching symbol_name."""
        target = symbol_name.strip()
        if not target:
            return "ERROR: symbol_name cannot be empty."

        matches: List[SymbolInfo] = []
        filter_norm = path_filter.replace("\\", "/").lstrip("./") if path_filter else None

        for rel_path, entry in self.files_cache.items():
            if filter_norm and filter_norm not in rel_path:
                continue
            for item in entry.get("symbols", []):
                sym = SymbolInfo.from_dict(item)
                if sym.name == target:
                    matches.append(sym)

        # Fallback: case-insensitive match if zero exact matches
        if not matches:
            target_lower = target.lower()
            for rel_path, entry in self.files_cache.items():
                if filter_norm and filter_norm not in rel_path:
                    continue
                for item in entry.get("symbols", []):
                    sym = SymbolInfo.from_dict(item)
                    if sym.name.lower() == target_lower:
                        matches.append(sym)

        if not matches:
            return f"No definitions found for symbol '{target}'."

        res_lines = [f"Found {len(matches)} definition(s) for '{target}':"]
        for m in matches[:25]:
            p_info = f" in {m.parent}" if m.parent else ""
            doc = f"\n    Doc: {m.docstring}" if m.docstring else ""
            res_lines.append(
                f"- {m.kind} {m.name}{m.signature}{p_info}\n"
                f"  File: {m.file_path}:{m.line} (lines {m.line}-{m.end_line}){doc}"
            )

        if len(matches) > 25:
            res_lines.append(f"... and {len(matches) - 25} more definitions omitted.")

        return "\n".join(res_lines)

    def find_references(self, symbol_name: str, path_filter: Optional[str] = None) -> str:
        """Find occurrences and usages of symbol_name across project source files."""
        target = symbol_name.strip()
        if not target:
            return "ERROR: symbol_name cannot be empty."

        # Find known definitions to annotate them
        def_lines = set()
        for rel_path, entry in self.files_cache.items():
            for item in entry.get("symbols", []):
                if item.get("name") == target:
                    def_lines.add((rel_path, item.get("line")))

        pattern = re.compile(rf"\b{re.escape(target)}\b")
        matches: List[str] = []
        filter_norm = path_filter.replace("\\", "/").lstrip("./") if path_filter else None

        for rel_path in sorted(self.files_cache.keys()):
            if filter_norm and filter_norm not in rel_path:
                continue

            full_path = os.path.join(self.root_dir, rel_path)
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    for line_no, line in enumerate(f, start=1):
                        if pattern.search(line):
                            is_def = (rel_path, line_no) in def_lines
                            tag = " [DEF]" if is_def else ""
                            matches.append(f"{rel_path}:{line_no}{tag}: {line.strip()[:140]}")
                            if len(matches) >= 40:
                                break
            except OSError:
                continue
            if len(matches) >= 40:
                break

        if not matches:
            return f"No references found for symbol '{target}'."

        res = [f"Found {len(matches)} reference(s) for '{target}':"] + matches
        return "\n".join(res)

    def get_workspace_summary(self) -> str:
        """Auto-detect tech stack, test runners, and key entry points."""
        summary_parts: List[str] = []
        entries = set(os.listdir(self.root_dir))

        # Detect tech stack
        stacks = []
        if any(f in entries for f in ("pyproject.toml", "setup.py", "requirements.txt", "Pipfile")):
            stacks.append("Python")
        if any(f in entries for f in ("package.json", "tsconfig.json")):
            stacks.append("Node.js/TypeScript")
        if "Cargo.toml" in entries:
            stacks.append("Rust (Cargo)")
        if "go.mod" in entries:
            stacks.append("Go")
        if stacks:
            summary_parts.append(f"Stack: {', '.join(stacks)}")

        # Detect test runner
        test_commands = []
        if os.path.exists(os.path.join(self.root_dir, "pytest.ini")) or any("test" in e for e in entries):
            test_commands.append("pytest")
        elif "package.json" in entries:
            test_commands.append("npm test")
        if test_commands:
            summary_parts.append(f"Test runner: {', '.join(test_commands)}")

        # Detect entry points
        entry_points = []
        for cand in ("app.py", "main.py", "run.py", "index.ts", "index.js", "src/index.ts", "src/main.rs"):
            if os.path.exists(os.path.join(self.root_dir, cand)):
                entry_points.append(cand)
        if entry_points:
            summary_parts.append(f"Entry points: {', '.join(entry_points)}")

        # Indexed statistics
        total_symbols = sum(len(e.get("symbols", [])) for e in self.files_cache.values())
        summary_parts.append(f"Indexed: {len(self.files_cache)} files ({total_symbols} symbols)")

        return " | ".join(summary_parts) if summary_parts else "Workspace initialized."
