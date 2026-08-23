"""Sova interactive terminal entrypoint: run with `python -m agent.cli`."""
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
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

from . import llm, sessions
from .loop import run_agent

_theme = Theme({
    "brand": "bold bright_cyan",
    "muted": "grey62",
    "tool.name": "bold bright_blue",
    "tool.result": "grey62",
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
]

# Holds the live "thinking" spinner/stream, if any, and its accumulated streamed text.
_status = {"handle": None, "thread": None, "stop": threading.Event(), "buffer": ""}
_auto_approve = {"value": False}  # flipped by 'a' at a permission prompt for the rest of the process


def _cycle_phrases(handle, prefix) -> None:
    while not _status["stop"].wait(1.6):
        if not _status["buffer"]:
            handle.update(f"{prefix}{random.choice(_THINKING_PHRASES)}")


def _stop_spinner() -> None:
    if _status["handle"] is not None:
        _status["stop"].set()
        if _status["thread"] is not None:
            _status["thread"].join(timeout=0.5)
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
    console.print(Panel("\n".join(lines) or "(empty)", title="[muted]Todo[/muted]", border_style="muted"))


def _ask_permission(name: str, args: dict) -> bool:
    if _auto_approve["value"]:
        return True
    _stop_spinner()
    preview = json.dumps(args)[:300]
    answer = console.input(
        f"[error]Allow[/error] [tool.name]{name}[/tool.name]({preview})? [y/N/a=always] "
    ).strip().lower()
    if answer == "a":
        _auto_approve["value"] = True
        return True
    return answer == "y"


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
        console.print(f"{prefix}⟳ [tool.name]{event['name']}[/tool.name]({event['args']})")
    elif etype == "tool_result":
        text = str(event["result"])
        if len(text) > 500:
            text = text[:500] + " …"
        console.print(f"{prefix}  {text}", style="tool.result")
        if event.get("diff"):
            _print_diff(event["diff"], prefix)
    elif etype == "todo":
        _print_todo(event.get("todos") or [])
    elif etype == "answer":
        if event.get("subagent"):
            console.print(f"{prefix}✓ {event['text']}", style="tool.result")
        elif event.get("verified"):
            console.print(Panel(event["text"] or "", title="Sova", border_style="answer"))
        else:
            console.print(Panel(
                event["text"] or "",
                title="Sova (stopped without calling finish - not confirmed complete)",
                border_style="error",
            ))
    elif etype == "error":
        console.print(f"{prefix}{event['message']}", style="error")


def _print_banner(root_dir: str) -> None:
    provider = llm.get_provider()
    model = llm.get_model()
    body = (
        f"[brand]{_LOGO}[/brand]\n"
        f"[muted]terminal coding agent[/muted]\n\n"
        f"[muted]cwd[/muted]       {root_dir}\n"
        f"[muted]provider[/muted]  {provider}\n"
        f"[muted]model[/muted]     {model}"
    )
    console.print(Panel(body, border_style="brand", padding=(1, 2)))
    commands = Panel(
        "[muted]Commands:\n"
        "  /provider groq|ollama|nvidia  — Switch LLM provider\n"
        "  /model <name>                  — Override default model\n"
        "  /new                           — Reset conversation context\n"
        "  /resume [id]                   — List or resume a past session\n"
        "  /approve                       — Auto-approve write/edit/shell for this session\n"
        "  Alt+Enter                      — Insert a newline (multi-line task)\n"
        "  exit, quit, Ctrl+C             — Exit agent[/muted]",
        title="[muted]Help[/muted]",
        border_style="muted",
    )
    console.print(commands)


def _handle_command(task: str, model_override: list, conversation: list, root_dir: str, session_ref: list) -> bool:
    """Handle a leading-slash command. Returns True if it was handled."""
    parts = task.split(maxsplit=1)
    cmd = parts[0].lower()

    if cmd == "/new":
        conversation[0] = None
        session_ref[0] = sessions.new_session_id()
        console.print("Started a new conversation (previous context cleared).", style="muted")
        return True

    if cmd == "/provider":
        if len(parts) < 2 or parts[1].strip().lower() not in ("groq", "ollama", "nvidia"):
            console.print("Usage: /provider groq|ollama|nvidia", style="error")
            return True
        os.environ["SOVA_PROVIDER"] = parts[1].strip().lower()
        model_override[0] = None  # fall back to the new provider's default model
        console.print(f"Switched provider to '{os.environ['SOVA_PROVIDER']}' (model: {llm.get_model()})", style="muted")
        return True

    if cmd == "/model":
        if len(parts) < 2 or not parts[1].strip():
            console.print("Usage: /model <name>", style="error")
            return True
        model_override[0] = parts[1].strip()
        console.print(f"Switched model to '{model_override[0]}'", style="muted")
        return True

    if cmd == "/approve":
        _auto_approve["value"] = True
        console.print("Auto-approving write_file/edit_file/run_shell for the rest of this session.", style="muted")
        return True

    if cmd == "/resume":
        arg = parts[1].strip() if len(parts) > 1 else ""
        if not arg:
            rows = sessions.list_sessions(root_dir)
            if not rows:
                console.print("No saved sessions yet.", style="muted")
                return True
            lines = [f"  {r['id']}  {'✔' if r['finished'] else ' '}  {r['first_message']}" for r in rows]
            console.print(Panel("\n".join(lines), title="[muted]Recent sessions (/resume <id>)[/muted]", border_style="muted"))
            return True
        try:
            conversation[0] = sessions.load_session(root_dir, arg)
        except (OSError, json.JSONDecodeError, KeyError):
            console.print(f"Could not load session '{arg}'.", style="error")
            return True
        session_ref[0] = arg
        console.print(f"Resumed session '{arg}' ({len(conversation[0])} messages).", style="muted")
        return True

    return False


def _build_prompt_session(root_dir: str) -> PromptSession:
    history_path = os.path.join(root_dir, ".sova", "history")
    os.makedirs(os.path.dirname(history_path), exist_ok=True)
    completer = merge_completers([
        WordCompleter(["/provider", "/model", "/new", "/resume", "/approve", "exit", "quit"], sentence=True),
        PathCompleter(only_directories=False, expanduser=True),
    ])
    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _insert_newline(event):
        event.current_buffer.insert_text("\n")

    return PromptSession(
        history=FileHistory(history_path),
        completer=completer,
        key_bindings=bindings,
        multiline=False,
    )


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(prog="sova")
    parser.add_argument("--provider", "-p", choices=["groq", "ollama", "nvidia"], help="LLM provider to use")
    parser.add_argument("--model", "-m", help="Model name override")
    args = parser.parse_args()

    if args.provider:
        os.environ["SOVA_PROVIDER"] = args.provider

    root_dir = os.getcwd()
    _print_banner(root_dir)

    try:
        pt_session = _build_prompt_session(root_dir)
    except Exception:
        # No real console screen buffer available (piped input, some terminal emulators/CI) -
        # fall back to plain input so the CLI still works, just without history/completion.
        pt_session = None

    model_override = [args.model]  # boxed so _handle_command can mutate it
    conversation = [None]  # boxed conversation history, carried across turns until /new or exit
    session_ref = [sessions.new_session_id()]  # boxed current session id, autosaved after each turn
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
