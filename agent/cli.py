"""Sova interactive terminal entrypoint (CLI coding harness)."""
import argparse
import json
import os
import random
import threading

from dotenv import load_dotenv
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import PathCompleter, WordCompleter, merge_completers
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.theme import Theme

from . import llm, sessions
from .logger import get_recent_logs, log_info
from .loop import run_agent
from .tools import unified_diff

_theme = Theme({
    "brand": "bold bright_cyan",
    "muted": "grey62",
    "tool.name": "bold bright_blue",
    "tool.result": "grey74",
    "answer": "bold bright_cyan",
    "error": "bold red",
    "diff.add": "bright_green",
    "diff.del": "bright_red",
    "diff.hunk": "bright_cyan",
})

console = Console(theme=_theme, highlight=False)

_LOGO = r"""███████╗ ██████╗ ██╗   ██╗ █████╗
██╔════╝██╔═══██╗██║   ██║██╔══██╗
███████╗██║   ██║██║   ██║███████║
╚════██║██║   ██║╚██╗ ██╔╝██╔══██║
███████║╚██████╔╝ ╚████╔╝ ██║  ██║
╚══════╝ ╚═════╝   ╚═══╝  ╚═╝  ╚═╝"""

_THINKING_PHRASES = [
    "thinking...",
    "bribing the hamster...",
    "herding cats...",
    "consulting the rubber duck...",
    "reticulating splines...",
    "untangling the yarn...",
    "asking the magic 8-ball...",
    "buttering the toast...",
    "sharpening pencils...",
    "waking up the intern...",
    "polishing the bits...",
    "counting sheep...",
    "summoning the compiler gremlins...",
    "analyzing codebase...",
    "inspecting file structure...",
    "checking syntax & dependencies...",
    "synthesizing implementation plan...",
    "optimizing tool execution...",
    "evaluating solution...",
]

_status = {"handle": None, "thread": None, "stop": threading.Event(), "buffer": ""}
_auto_approve = {"value": False}


def _cycle_phrases(handle, prefix) -> None:
    while not _status["stop"].wait(1.6):
        if not _status["buffer"]:
            handle.update(f"{prefix}{random.choice(_THINKING_PHRASES)}")


def _stop_spinner() -> None:
    if _status["handle"] is not None:
        _status["stop"].set()
        if _status["thread"] is not None:
            _status["thread"].join(timeout=0.4)
        _status["handle"].stop()
        _status["handle"] = None
        _status["thread"] = None
        _status["buffer"] = ""


def _print_diff(diff_text: str, prefix: str = "") -> None:
    if not diff_text:
        return
    body = Text()
    lines = diff_text.splitlines()
    for line in lines[:200]:
        if line.startswith("+++") or line.startswith("---"):
            body.append(line + "\n", style="muted")
        elif line.startswith("+"):
            body.append(line + "\n", style="diff.add")
        elif line.startswith("-"):
            body.append(line + "\n", style="diff.del")
        elif line.startswith("@@"):
            body.append(line + "\n", style="diff.hunk")
        else:
            body.append(line + "\n", style="muted")
    if len(lines) > 200:
        body.append(f"… {len(lines) - 200} more lines\n", style="muted")
    console.print(Panel(body, border_style="muted", padding=(0, 1)))


def _print_todo(todos: list) -> None:
    marks = {"completed": "[green]✔[/green]", "in_progress": "[bright_cyan]➤[/bright_cyan]"}
    lines = [f"{marks.get(t.get('status'), '☐')} {t.get('content', '')}" for t in todos]
    console.print(Panel("\n".join(lines) or "(empty)", title="[muted]Todo Plan[/muted]", border_style="muted"))


def _ask_permission(name: str, args: dict) -> bool:
    """interactive permission options box with formatted previews."""
    if _auto_approve["value"]:
        return True
    _stop_spinner()

    if name == "write_file":
        path = args.get("path", "")
        content = str(args.get("content", ""))
        full = os.path.abspath(path)
        existing_content = ""
        if os.path.exists(full):
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    existing_content = f.read()
            except Exception:
                existing_content = ""

        if existing_content:
            old_lines = len(existing_content.splitlines())
            new_lines = len(content.splitlines())
            console.print(Panel(
                f"[bold bright_red]⚠️  WARNING: OVERWRITING EXISTING FILE![/bold bright_red]\n"
                f"[white]{path}[/white] currently has [bold yellow]{old_lines}[/bold yellow] lines. "
                f"This will replace the entire file with [bold yellow]{new_lines}[/bold yellow] lines.",
                title="[bold red]Destructive Overwrite Alert[/bold red]",
                border_style="bright_red",
                padding=(0, 2),
            ))
            diff = unified_diff(path, existing_content, content)
            if diff:
                _print_diff(diff)
        else:
            lang = "python" if path.endswith(".py") else "text"
            preview_lines = content.splitlines()[:20]
            preview_code = "\n".join(preview_lines)
            if len(content.splitlines()) > 20:
                preview_code += f"\n... ({len(content.splitlines()) - 20} more lines)"
            syntax_preview = Syntax(preview_code, lang, theme="monokai", line_numbers=True, word_wrap=True)
            console.print(Panel(
                syntax_preview,
                title=f"[bold bright_cyan]📄 New File: [white]{path}[/white] ({len(content)} chars)[/bold bright_cyan]",
                border_style="bright_cyan",
                padding=(0, 1),
            ))
    elif name == "edit_file":
        path = args.get("path", "")
        old_str = str(args.get("old_str", ""))
        new_str = str(args.get("new_str", ""))
        diff = unified_diff(path, old_str, new_str)
        if diff:
            _print_diff(diff)
        else:
            console.print(Panel(
                f"[bright_red]- {old_str}[/bright_red]\n[bright_green]+ {new_str}[/bright_green]",
                title=f"[bold bright_cyan]✏️ Edit File: [white]{path}[/white][/bold bright_cyan]",
                border_style="bright_cyan",
            ))
    elif name == "run_shell":
        cmd = args.get("command", "")
        console.print(Panel(
            f"[bold bright_green]$[/bold bright_green] [white]{cmd}[/white]",
            title="[bold yellow]⚡ Execute Shell Command[/bold yellow]",
            border_style="yellow",
            padding=(0, 1),
        ))
    else:
        preview = json.dumps(args, indent=2)
        console.print(Panel(preview[:600], title=f"[bold cyan]Action: {name}[/bold cyan]", border_style="cyan"))

    options_box = (
        "[bold white]Permission Request[/bold white]\n"
        "  [bright_green][y][/bright_green] Yes, allow once\n"
        "  [bright_cyan][a][/bright_cyan] Always allow sensitive tools for this session\n"
        "  [bright_red][n][/bright_red] No, deny this tool call\n"
        "  [yellow][c][/yellow] Cancel task"
    )
    console.print(Panel(options_box, border_style="yellow", padding=(0, 2)))

    while True:
        try:
            choice = console.input("[bold yellow]❯ Select [y/a/n/c] (default: y): [/bold yellow]").strip().lower()
        except (KeyboardInterrupt, EOFError):
            return False

        if not choice or choice == "y":
            return True
        elif choice == "a":
            _auto_approve["value"] = True
            console.print("[bright_cyan]✓ Auto-approval enabled for the rest of this session.[/bright_cyan]")
            return True
        elif choice == "n":
            console.print("[bright_red]✗ Tool execution denied by user.[/bright_red]")
            return False
        elif choice == "c":
            console.print("[yellow]Task cancelled by user.[/yellow]")
            return False
        else:
            console.print("[muted]Please type y, a, n, or c.[/muted]")


def _render_event(event: dict) -> None:
    etype = event["type"]
    prefix = f"  ↳ [muted]({event['subagent']})[/muted] " if event.get("subagent") else ""

    if etype == "thinking":
        if _status["handle"] is None:
            handle = console.status(f"{prefix}thinking...", spinner="dots")
            handle.start()
            _status["handle"] = handle
            _status["buffer"] = ""
            _status["stop"].clear()
            _status["thread"] = threading.Thread(target=_cycle_phrases, args=(handle, prefix), daemon=True)
            _status["thread"].start()
        return

    if etype == "thinking_delta":
        if _status["handle"] is not None:
            _status["buffer"] += event.get("text") or ""
            shown = _status["buffer"][-300:].replace("[", "\\[")
            _status["handle"].update(f"{prefix}{shown}")
        return

    _stop_spinner()
    if etype == "tool_call":
        name = event["name"]
        args = event.get("args") or {}
        if name == "write_file":
            console.print(f"{prefix}⟳ [tool.name]write_file[/tool.name] [white]{args.get('path')}[/white] ({len(str(args.get('content', '')))} chars)")
        elif name == "edit_file":
            console.print(f"{prefix}⟳ [tool.name]edit_file[/tool.name] [white]{args.get('path')}[/white]")
        elif name == "read_file":
            lines_range = f" (lines {args.get('start_line', 1)}-{args.get('end_line', 'end')})" if args.get('start_line') or args.get('end_line') else ""
            console.print(f"{prefix}⟳ [tool.name]read_file[/tool.name] [white]{args.get('path')}{lines_range}[/white]")
        elif name == "run_shell":
            console.print(f"{prefix}⟳ [tool.name]run_shell[/tool.name] [bold green]$[/bold green] {args.get('command')}")
        elif name == "grep":
            console.print(f"{prefix}⟳ [tool.name]grep[/tool.name] '{args.get('pattern')}' in {args.get('path', '.')}")
        elif name == "find_files":
            console.print(f"{prefix}⟳ [tool.name]find_files[/tool.name] '{args.get('pattern')}'")
        elif name == "finish":
            console.print(f"{prefix}✓ [bold bright_green]finish[/bold bright_green]")
        else:
            console.print(f"{prefix}⟳ [tool.name]{name}[/tool.name]({args})")
    elif etype == "tool_result":
        text = str(event["result"])
        if len(text) > 400:
            text = text[:400] + " …"
        console.print(f"{prefix}  {text}", style="tool.result")
        if event.get("diff"):
            _print_diff(event["diff"], prefix)
    elif etype == "todo":
        _print_todo(event.get("todos") or [])
    elif etype == "answer":
        text = event.get("text") or ""
        rendered = Markdown(text) if text.strip() else Text("")
        if event.get("subagent"):
            console.print(f"{prefix}✓ {text}", style="tool.result")
        elif event.get("verified"):
            console.print(Panel(rendered, title="[bold bright_green]Sova[/bold bright_green]", border_style="answer"))
        else:
            console.print(Panel(
                rendered,
                title="[bold red]Sova (stopped without calling finish)[/bold red]",
                border_style="error",
            ))
    elif etype == "error":
        console.print(f"{prefix}[bold red]{event['message']}[/bold red]")


def _print_banner(root_dir: str, session_id: str) -> None:
    provider = llm.get_provider()
    model = llm.get_model()
    budget = llm.get_token_budget(provider, model)
    body = (
        f"[brand]{_LOGO}[/brand]\n"
        f"[muted]Autonomous Coding Harness[/muted]\n\n"
        f"[muted]cwd[/muted]          {root_dir}\n"
        f"[muted]provider[/muted]     [bold]{provider}[/bold]\n"
        f"[muted]model[/muted]        [bold]{model}[/bold]\n"
        f"[muted]token budget[/muted] {budget} tokens/turn\n"
        f"[muted]session[/muted]      {session_id}"
    )
    console.print(Panel(body, border_style="brand", padding=(1, 2)))
    commands = Panel(
        "[muted]Commands:\n"
        "  /provider     — Select LLM provider (Groq, Ollama, Nvidia, OpenAI)\n"
        "  /model        — Select or override model (with dynamic recommendations)\n"
        "  /resume       — List and resume past conversations\n"
        "  /new          — Start a new conversation context\n"
        "  /approve      — Toggle auto-approval of sensitive actions\n"
        "  /logs         — View recent execution logs\n"
        "  Alt+Enter     — Insert newline in multi-line prompt\n"
        "  exit, quit    — Exit agent[/muted]",
        title="[muted]Quick Commands[/muted]",
        border_style="muted",
    )
    console.print(commands)


def _handle_command(task: str, model_override: list, conversation: list, root_dir: str, session_ref: list) -> bool:
    parts = task.split(maxsplit=1)
    cmd = parts[0].lower()

    if cmd == "/new":
        conversation[0] = None
        session_ref[0] = sessions.new_session_id()
        console.print("[bright_cyan]Started a new conversation session.[/bright_cyan]", style="muted")
        return True

    if cmd == "/provider":
        providers = llm.get_available_providers()
        if len(parts) > 1 and parts[1].strip().lower() in providers:
            chosen = parts[1].strip().lower()
        else:
            lines = []
            for i, p in enumerate(providers, start=1):
                cfg = llm.PROVIDERS_CONFIG.get(p, {})
                active_mark = " (active)" if p == llm.get_provider() else ""
                lines.append(f"  [bright_cyan][{i}][/bright_cyan] [bold]{p}[/bold] — {cfg.get('name', '')}{active_mark}")
            console.print(Panel("\n".join(lines), title="[bold bright_cyan]Select LLM Provider[/bold bright_cyan]", border_style="bright_cyan"))
            choice = console.input("[bold bright_cyan]❯ Select [1-N or name, Enter to cancel]: [/bold bright_cyan]").strip().lower()
            if not choice:
                return True
            if choice.isdigit() and 1 <= int(choice) <= len(providers):
                chosen = providers[int(choice) - 1]
            elif choice in providers:
                chosen = choice
            else:
                console.print(f"Unknown provider '{choice}'.", style="error")
                return True

        os.environ["SOVA_PROVIDER"] = chosen
        model_override[0] = None
        console.print(f"Switched provider to '[bold]{chosen}[/bold]' (Model: {llm.get_model()})", style="muted")
        return True

    if cmd == "/model":
        current_prov = llm.get_provider()
        recommended = llm.get_models_for_provider(current_prov)
        if len(parts) > 1 and parts[1].strip():
            chosen = parts[1].strip()
        else:
            lines = []
            for i, m in enumerate(recommended, start=1):
                active_mark = " (active)" if m == llm.get_model() else ""
                lines.append(f"  [bright_cyan][{i}][/bright_cyan] {m}{active_mark}")
            lines.append("  [bright_cyan][c][/bright_cyan] Enter custom model name...")
            console.print(Panel("\n".join(lines), title=f"[bold bright_cyan]Select Model for '{current_prov}'[/bold bright_cyan]", border_style="bright_cyan"))
            choice = console.input("[bold bright_cyan]❯ Select [1-N, 'c' for custom, Enter to cancel]: [/bold bright_cyan]").strip()
            if not choice:
                return True
            if choice.isdigit() and 1 <= int(choice) <= len(recommended):
                chosen = recommended[int(choice) - 1]
            elif choice.lower() == "c":
                chosen = console.input("[bold bright_cyan]Enter custom model name: [/bold bright_cyan]").strip()
                if not chosen:
                    return True
            else:
                chosen = choice

        model_override[0] = chosen
        console.print(f"Switched model to '[bold]{chosen}[/bold]'", style="muted")
        return True

    if cmd == "/approve":
        _auto_approve["value"] = not _auto_approve["value"]
        state = "ENABLED" if _auto_approve["value"] else "DISABLED"
        console.print(f"Auto-approval of sensitive actions is now [bold]{state}[/bold].", style="muted")
        return True

    if cmd == "/resume":
        arg = parts[1].strip() if len(parts) > 1 else ""
        if not arg:
            rows = sessions.list_sessions(root_dir)
            if not rows:
                console.print("No saved sessions yet.", style="muted")
                return True
            lines = []
            for i, r in enumerate(rows[:10], start=1):
                mark = "[bright_green]✔[/bright_green]" if r["finished"] else "[muted]…[/muted]"
                lines.append(f"  [bright_cyan][{i}][/bright_cyan] {r['id']}  {mark}  {r['first_message']}")
            console.print(Panel("\n".join(lines), title="[bold bright_cyan]Select Session to Resume[/bold bright_cyan]", border_style="bright_cyan"))
            choice = console.input("[bold bright_cyan]❯ Select [1-N or ID, Enter to cancel]: [/bold bright_cyan]").strip()
            if not choice:
                return True
            if choice.isdigit() and 1 <= int(choice) <= len(rows[:10]):
                arg = rows[int(choice) - 1]["id"]
            else:
                arg = choice
        try:
            conversation[0] = sessions.load_session(root_dir, arg)
        except (OSError, json.JSONDecodeError, KeyError):
            console.print(f"Could not load session '{arg}'.", style="error")
            return True
        session_ref[0] = arg
        console.print(f"Resumed session '{arg}' ({len(conversation[0])} messages).", style="muted")
        return True

    if cmd == "/logs":
        lines = get_recent_logs(root_dir, max_lines=35)
        if not lines:
            console.print("No logs found in .sova/logs/sova.log.", style="muted")
        else:
            console.print(Panel("\n".join(lines), title="[bold bright_cyan]Recent Sova Logs[/bold bright_cyan]", border_style="muted"))
        return True

    return False


def _build_prompt_session(root_dir: str, model_override: list, session_ref: list) -> PromptSession:
    history_path = os.path.join(root_dir, ".sova", "history")
    os.makedirs(os.path.dirname(history_path), exist_ok=True)
    completer = merge_completers([
        WordCompleter(["/provider", "/model", "/new", "/resume", "/approve", "/logs", "exit", "quit"], sentence=True),
        PathCompleter(only_directories=False, expanduser=True),
    ])
    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _insert_newline(event):
        event.current_buffer.insert_text("\n")

    def _bottom_toolbar():
        provider = llm.get_provider()
        model = model_override[0] or llm.get_model()
        sid = session_ref[0]
        return f" [{provider}:{model}] | session: {sid} | Alt+Enter: newline "

    return PromptSession(
        history=FileHistory(history_path),
        completer=completer,
        key_bindings=bindings,
        multiline=False,
        bottom_toolbar=_bottom_toolbar,
    )


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(prog="sova")
    parser.add_argument("--provider", "-p", choices=["groq", "ollama", "nvidia", "openai"], help="LLM provider to use")
    parser.add_argument("--model", "-m", help="Model name override")
    args = parser.parse_args()

    if args.provider:
        os.environ["SOVA_PROVIDER"] = args.provider

    root_dir = os.getcwd()
    model_override = [args.model]
    conversation = [None]
    session_ref = [sessions.new_session_id()]

    _print_banner(root_dir, session_ref[0])

    try:
        pt_session = _build_prompt_session(root_dir, model_override, session_ref)
    except Exception:
        pt_session = None

    while True:
        try:
            if pt_session is not None:
                task = pt_session.prompt("\n❯ Enter task: ").strip()
            else:
                task = console.input("\n[brand]❯ Enter task:[/brand] ").strip()
        except EOFError:
            break
        except KeyboardInterrupt:
            console.print("\n[muted]Bye![/muted]")
            break
        if task.lower() in {"exit", "quit"}:
            break
        if not task:
            continue
        if task.startswith("/") and _handle_command(task, model_override, conversation, root_dir, session_ref):
            continue

        try:
            result = run_agent(
                root_dir, task, model=model_override[0], on_event=_render_event,
                messages=conversation[0], on_permission=_ask_permission, session_id=session_ref[0],
            )
            conversation[0] = result["messages"]
        except KeyboardInterrupt:
            console.print("\n[muted]Stopped.[/muted]")
        finally:
            _stop_spinner()


if __name__ == "__main__":
    main()
