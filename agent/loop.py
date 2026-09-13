"""The agentic tool-calling loop shared by interactive use and batch SWE-bench runs."""
import inspect
import json

from openai import APIConnectionError, APIError, APIStatusError, AuthenticationError, BadRequestError, RateLimitError

from . import sessions
from .llm import chat, chat_stream, get_model, get_provider, normalize_model
from .prompts import SYSTEM_PROMPT
from .tools import build_tools

_SENSITIVE_TOOLS = {"write_file", "edit_file", "run_shell"}
_COMPACT_THRESHOLD = 40  # message count that triggers summarizing older turns
_COMPACT_KEEP_RECENT = 16  # messages to always leave verbatim

_SPAWN_SUBAGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "spawn_subagent",
        "description": (
            "Delegate a self-contained subtask to a fresh sub-agent with its own tool-calling loop. "
            "Use this to break a large task into independent pieces you can solve separately, then "
            "combine the results yourself. The sub-agent shares this project's persistent memory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subtask": {"type": "string", "description": "A clear, self-contained description of the subtask."},
            },
            "required": ["subtask"],
        },
    },
}

_CLASSIFY_SYSTEM = """Classify the user's message as CHAT or TASK.
CHAT: a question, request for advice/explanation, or general conversation that does not require \
reading or changing files in this project.
TASK: a request to inspect, write, edit, run, or fix code/files in this project.
Reply with exactly one word: CHAT or TASK."""


def _is_chat(task, model):
    """Decide up front whether `task` is plain conversation or a real coding task, so a model
    that ignores prompt instructions can't turn a question into pointless tool calls."""
    try:
        response = chat(
            [{"role": "system", "content": _CLASSIFY_SYSTEM}, {"role": "user", "content": task}],
            tools=None, model=model,
        )
        return (response.choices[0].message.content or "").strip().upper().startswith("CHAT")
    except Exception:
        return False  # on any classification failure, fall back to the full tool-calling loop


def _maybe_compact(messages, model):
    """Summarize older turns into a single system message once the conversation gets long,
    so sessions don't silently blow past the model's context window. Only cuts right before a
    'user' message boundary so tool_calls/tool-result pairs are never split."""
    if len(messages) <= _COMPACT_THRESHOLD:
        return messages

    lead = 0
    while lead < len(messages) and messages[lead]["role"] == "system":
        lead += 1

    idx = max(lead, len(messages) - _COMPACT_KEEP_RECENT)
    while idx < len(messages) and messages[idx]["role"] != "user":
        idx += 1
    if idx >= len(messages) - 1:
        return messages  # not enough room to safely cut this round

    to_summarize = messages[lead:idx]
    try:
        transcript = json.dumps(to_summarize)[:12000]
        response = chat(
            [
                {
                    "role": "system",
                    "content": "Summarize this agent conversation excerpt concisely, preserving key "
                               "facts, decisions, and file paths mentioned. Output plain text only.",
                },
                {"role": "user", "content": transcript},
            ],
            tools=None, model=model,
        )
        summary = response.choices[0].message.content or "(summary unavailable)"
    except Exception:
        return messages  # never let a failed summarization block the loop

    return (
        messages[:lead]
        + [{"role": "system", "content": f"Earlier conversation summary: {summary}"}]
        + messages[idx:]
    )


def _safe_chat(messages, tools, model, emit):
    """Call the LLM, streaming content tokens out via emit('thinking_delta', ...). Returns (response, err, retry_hint):
    - success: (response, None, None)
    - fatal error (auth/connection/rate-limit/other bad request): (None, err_result_dict, None)
    - recoverable malformed tool call (e.g. Groq tool_use_failed): (None, None, hint_text)
    """
    def on_delta(text):
        emit("thinking_delta", text=text)

    requested_model = normalize_model(model) or get_model()

    try:
        return chat_stream(messages, tools=tools, model=requested_model, on_delta=on_delta), None, None
    except AuthenticationError:
        summary = f"[ERROR] Invalid API key for provider '{get_provider()}'. Check your .env file."
    except APIConnectionError:
        if get_provider() == "ollama":
            summary = "[ERROR] Cannot reach Ollama. Is it running? Try: ollama serve"
        else:
            summary = f"[ERROR] Cannot connect to {get_provider()}. Check your internet connection."
    except RateLimitError as exc:
        retry_after = exc.response.headers.get("retry-after") if exc.response is not None else None
        wait_hint = f" Retry after {retry_after}s." if retry_after else ""
        summary = (
            f"[ERROR] Rate limit hit on {get_provider()}.{wait_hint} "
            f"Try again shortly, or switch with '/provider ollama'."
        )
    except BadRequestError as exc:
        if exc.code == "tool_use_failed":
            return _malformed_tool_call_retry(emit)
        summary = _format_model_error(exc.message, requested_model) or f"[ERROR] {get_provider()} rejected the request: {exc.message}"
    except APIStatusError as exc:
        # Catch-all for any HTTP-status error the SDK doesn't have a dedicated subclass for
        # (e.g. Groq's 413 "request too large for TPM limit") - without this the whole process
        # crashes instead of ending the turn gracefully.
        body = exc.body if isinstance(exc.body, dict) else {}
        detail = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else None
        summary = _format_model_error(detail or exc.message, requested_model)
        if summary is None:
            summary = (
                f"[ERROR] {get_provider()} rejected the request (HTTP {exc.status_code}): "
                f"{detail or exc.message}. Try a shorter task, '/new' to reset context, or switch "
                f"provider/model."
            )
    except APIError as exc:
        # Broadest fallback: some providers (e.g. Groq) raise a plain APIError mid-stream, with no
        # HTTP response attached, when the model emits a tool call that fails schema validation
        # (e.g. a missing required argument) - treat that as recoverable instead of a fatal crash.
        message = str(exc)
        if "tool call validation failed" in message.lower() or "did not match schema" in message.lower():
            return _malformed_tool_call_retry(emit)
        summary = f"[ERROR] {get_provider()} error: {message}"
    emit("error", message=summary)
    return None, {"messages": messages, "finished": False, "summary": summary}, None


def _format_model_error(message, requested_model):
    if not message:
        return None

    lowered = message.lower()
    if "model_not_found" not in lowered and "does not exist" not in lowered and "do not have access" not in lowered:
        return None

    provider = get_provider()
    suggested = get_model()
    normalized_requested = normalize_model(requested_model, provider=provider)

    if provider == "groq":
        return (
            f"[ERROR] Groq rejected model '{requested_model}'. "
            f"Try '/model openai/gpt-oss-120b' or clear SOVA_MODEL to use the default. "
            f"(Resolved model: '{normalized_requested or suggested}')"
        )

    return (
        f"[ERROR] {provider} rejected model '{requested_model}'. "
        f"Try a provider-supported model name or clear SOVA_MODEL to use the default '{suggested}'."
    )


def _malformed_tool_call_retry(emit):
    emit(
        "tool_result", name="(malformed tool call)",
        result="Model tried to call a tool with invalid or missing arguments; asking it to retry.",
    )
    return None, None, (
        "Your last tool call was invalid (missing or malformed arguments), so it did not run. "
        "Re-call the tool with all required arguments filled in correctly, one tool call at a time."
    )


def run_agent(
    root_dir, task, model=None, max_iterations=20, verbose=True, on_event=None, allow_subagents=True,
    messages=None, stop_event=None, on_permission=None, session_id=None,
):
    """Run the agent on `task` inside `root_dir` until it calls finish or hits max_iterations.

    If `on_event` is given, it is called with dicts like {"type": ..., ...} for each
    step (thinking/tool_call/tool_result/answer/error) instead of printing directly.
    `allow_subagents` controls whether the agent may delegate work via `spawn_subagent`;
    sub-agents themselves run with it disabled to avoid unbounded recursion.
    `messages` continues an existing conversation (as returned in a previous result's
    "messages") so follow-up tasks keep context of what was done before; omit it to
    start a fresh conversation.
    `stop_event`, if given, is checked between turns and between tool calls; when set the
    run stops early and returns a "Stopped by user" result.
    `on_permission(name, args) -> bool`, if given, is asked before running a sensitive tool
    (write_file/edit_file/run_shell); a False return denies the call.
    `session_id`, if given, persists the conversation to .sova/sessions/<session_id>.json
    after every turn via `sessions.save_session` so it can be resumed later.
    """
    def emit(event_type, **data):
        if on_event:
            on_event({"type": event_type, **data})
        elif verbose:
            _print_event(event_type, data)

    def _finish(result):
        if session_id:
            try:
                sessions.save_session(root_dir, session_id, messages, result)
            except OSError:
                pass
        return result

    def _stopped():
        emit("error", message="Stopped by user")
        return _finish({"messages": messages, "finished": False, "summary": "Stopped by user"})

    schemas, impls = build_tools(root_dir)

    if allow_subagents:
        def spawn_subagent(subtask):
            def _sub_event(event):
                if on_event:
                    on_event({**event, "subagent": subtask[:40]})

            sub_result = run_agent(
                root_dir, subtask, model=model, max_iterations=8, verbose=False,
                on_event=_sub_event if on_event else None, allow_subagents=False,
                stop_event=stop_event, on_permission=on_permission,
            )
            if sub_result["finished"]:
                return f"[SUBAGENT COMPLETED] {sub_result['summary']}"
            return (
                f"ERROR: sub-agent did not confirm completion (never called finish). "
                f"Its last message was: {sub_result['summary']}. Verify what it actually did "
                f"yourself (e.g. read_file) before trusting or retrying this."
            )

        schemas = schemas + [_SPAWN_SUBAGENT_SCHEMA]
        impls = {**impls, "spawn_subagent": spawn_subagent}

    if messages is None:
        memory_note = impls["memory_read"]()
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if memory_note != "(memory is empty)":
            messages.append({"role": "system", "content": f"Project memory (persisted notes from previous runs):\n{memory_note}"})
        messages.append({"role": "user", "content": task})
    else:
        messages.append({"role": "user", "content": task})

    if _is_chat(task, model):
        emit("thinking")
        response, err, retry_hint = _safe_chat(messages, None, model, emit)
        if err:
            return _finish(err)
        if retry_hint:
            summary = "[ERROR] Model failed to produce a valid response; try again."
            emit("error", message=summary)
            return _finish({"messages": messages, "finished": False, "summary": summary})
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))
        emit("answer", text=message.content, verified=False)
        return _finish({"messages": messages, "finished": False, "summary": message.content})

    last_failure = None  # (name, args_json) of the last failing tool call
    repeat_count = 0
    did_any_tool_call = False
    nudged_to_finish = False
    malformed_retries = 0

    for _ in range(max_iterations):
        if stop_event is not None and stop_event.is_set():
            return _stopped()

        messages = _maybe_compact(messages, model)

        emit("thinking")
        response, err, retry_hint = _safe_chat(messages, schemas, model, emit)
        if err:
            return _finish(err)
        if retry_hint:
            malformed_retries += 1
            if malformed_retries >= 3:
                summary = "[ERROR] Model repeatedly failed to produce a valid tool call; stopping."
                emit("error", message=summary)
                return _finish({"messages": messages, "finished": False, "summary": summary})
            messages.append({"role": "user", "content": retry_hint})
            continue
        malformed_retries = 0

        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            if did_any_tool_call and not nudged_to_finish:
                nudged_to_finish = True
                messages.append({
                    "role": "user",
                    "content": "If the task is fully complete, call the `finish` tool now with a short "
                                "summary of what you changed. If it is not complete, keep working.",
                })
                continue
            emit("answer", text=message.content, verified=False)
            return _finish({"messages": messages, "finished": False, "summary": message.content})

        did_any_tool_call = True
        finished_summary = None
        for tool_call in message.tool_calls:
            name = tool_call.function.name
            try:
                args = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            emit("tool_call", name=name, args=args)

            func = impls.get(name)
            diff = None
            if func is None:
                result = f"ERROR: unknown tool '{name}'"
            elif name in _SENSITIVE_TOOLS and on_permission is not None and not on_permission(name, args):
                result = "ERROR: user denied this tool call"
            else:
                try:
                    raw = func(**args)
                    result, diff = raw if isinstance(raw, tuple) else (raw, None)
                except TypeError as exc:
                    expected = list(inspect.signature(func).parameters)
                    result = f"ERROR: invalid arguments for '{name}' ({exc}). Expected keys: {expected}"
                except Exception as exc:
                    result = f"ERROR: {exc}"

            emit("tool_result", name=name, result=result, diff=diff)

            is_error = isinstance(result, str) and result.startswith("ERROR")
            if name == "todo_write" and not is_error:
                emit("todo", todos=args.get("todos", []))

            failure_key = (name, json.dumps(args, sort_keys=True))
            if is_error and failure_key == last_failure:
                repeat_count += 1
            else:
                last_failure = failure_key if is_error else None
                repeat_count = 1 if is_error else 0
            if repeat_count >= 3:
                summary = f"[ERROR] Stuck retrying the same failing '{name}' call 3 times in a row; stopping."
                emit("error", message=summary)
                return _finish({"messages": messages, "finished": False, "summary": summary})

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(result),
            })

            if name == "finish":
                finished_summary = result

            if stop_event is not None and stop_event.is_set():
                return _stopped()

        if finished_summary is not None:
            emit("answer", text=str(finished_summary), verified=True)
            return _finish({"messages": messages, "finished": True, "summary": finished_summary})

    return _finish({"messages": messages, "finished": False, "summary": "Max iterations reached"})


def _print_event(event_type, data):
    """Default plain-print rendering of agent events, used when no on_event callback is given."""
    if event_type == "tool_call":
        print(f"[tool] {data['name']}({data['args']})")
    elif event_type == "tool_result":
        print(f"[result] {str(data['result'])[:500]}")
    elif event_type == "answer":
        print(f"[agent] {data['text']}")
    elif event_type == "error":
        print(data["message"])
