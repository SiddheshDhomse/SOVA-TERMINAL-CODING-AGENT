"""The agentic tool-calling loop shared by interactive use, web console, and evaluation runs."""
import inspect
import json
import time

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from . import sessions
from .llm import (
    chat,
    chat_stream,
    estimate_tokens,
    get_model,
    get_provider,
    get_token_budget,
    normalize_model,
)
from .logger import SessionTrajectoryLogger, log_error, log_info, log_warn
from .prompts import SYSTEM_PROMPT
from .tools import build_tools, cleanup_jobs

_SENSITIVE_TOOLS = {"write_file", "edit_file", "run_shell"}
_COMPACT_THRESHOLD = 30  # message count threshold
_COMPACT_KEEP_RECENT = 12  # messages to preserve verbatim

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
    """Decide up front whether `task` is plain conversation or a real coding task."""
    try:
        response = chat(
            [{"role": "system", "content": _CLASSIFY_SYSTEM}, {"role": "user", "content": task}],
            tools=None, model=model,
        )
        return (response.choices[0].message.content or "").strip().upper().startswith("CHAT")
    except Exception:
        return False


def _force_compact(messages):
    """Emergency compaction: keep system prompt and last few messages to escape HTTP 413 TPM limits."""
    if len(messages) <= 4:
        return messages
    lead = 0
    while lead < len(messages) and messages[lead]["role"] == "system":
        lead += 1
    recent = messages[-6:]
    # Ensure recent starts at a user message to prevent dangling tool responses
    while recent and recent[0]["role"] not in ("user", "system"):
        recent = recent[1:]
    if not recent:
        recent = messages[-2:]
    return (
        messages[:lead]
        + [{"role": "system", "content": "[Notice: Context pruned aggressively to stay within rate/TPM limits]"}]
        + recent
    )


def _maybe_compact(messages, model, tools=None):
    """Summarize older turns safely without cutting raw JSON or breaking tool_call pairs."""
    provider = get_provider()
    requested_model = normalize_model(model) or get_model()
    budget = get_token_budget(provider, requested_model)
    est_tokens = estimate_tokens(messages, tools)

    if est_tokens <= int(budget * 0.82) and len(messages) <= _COMPACT_THRESHOLD:
        return messages

    lead = 0
    while lead < len(messages) and messages[lead]["role"] == "system":
        lead += 1

    idx = max(lead, len(messages) - _COMPACT_KEEP_RECENT)
    while idx < len(messages) and messages[idx]["role"] != "user":
        idx += 1
    if idx >= len(messages) - 1:
        # If still can't cut at a user message, try an earlier turn
        idx = max(lead, len(messages) - 6)
        while idx < len(messages) and messages[idx]["role"] != "user":
            idx += 1
        if idx >= len(messages) - 1:
            return _force_compact(messages) if est_tokens > budget else messages

    to_summarize = messages[lead:idx]
    try:
        # Format clean text transcript instead of slicing raw JSON
        text_lines = []
        for m in to_summarize:
            r = m.get("role")
            c = m.get("content") or ""
            if r == "user":
                text_lines.append(f"User: {c[:400]}")
            elif r == "assistant":
                tcs = m.get("tool_calls")
                if tcs:
                    t_names = [tc.get("function", {}).get("name", "tool") for tc in tcs]
                    text_lines.append(f"Assistant called tools: {', '.join(t_names)}")
                if c:
                    text_lines.append(f"Assistant: {c[:400]}")
            elif r == "tool":
                text_lines.append(f"Tool output: {str(c)[:250]}")
        transcript = "\n".join(text_lines)[:4500]

        response = chat(
            [
                {
                    "role": "system",
                    "content": "Summarize this conversation excerpt concisely, preserving key facts, "
                               "decisions, and file paths. Output plain text only.",
                },
                {"role": "user", "content": transcript},
            ],
            tools=None, model=model,
        )
        summary = response.choices[0].message.content or "(summary unavailable)"
    except Exception as exc:
        log_warn(f"Summarization error: {exc}")
        summary = "Earlier conversation pruned to preserve context budget."

    return (
        messages[:lead]
        + [{"role": "system", "content": f"Earlier conversation summary:\n{summary}"}]
        + messages[idx:]
    )


def _safe_chat(messages, tools, model, emit):
    """Call LLM with streaming and robust error handling for Groq TPM 413 and malformed calls."""
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
        err_msg = f"{getattr(exc, 'message', '')} {exc} {getattr(exc, 'body', '')}".lower()
        if exc.code == "tool_use_failed" or "failed to parse tool call" in err_msg or "tool call argument" in err_msg:
            return _malformed_tool_call_retry(emit)
        summary = _format_model_error(getattr(exc, "message", None), requested_model) or f"[ERROR] {get_provider()} rejected the request: {exc}"
    except APIStatusError as exc:
        body = exc.body if isinstance(exc.body, dict) else {}
        detail = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else None
        err_msg = f"{detail} {getattr(exc, 'message', '')} {exc}".lower()

        # Detect Groq HTTP 413 or tokens-per-minute rate limit
        if exc.status_code == 413 or "tokens per minute" in err_msg or "tpm" in err_msg:
            emit(
                "tool_result",
                name="(auto-compaction)",
                result=f"Hit TPM limit ({err_msg[:120]}). Auto-pruning context to retry under quota...",
            )
            return None, None, "__FORCE_COMPACT_RETRY__"

        if "failed to parse tool call" in err_msg or "tool call argument" in err_msg or "tool_use_failed" in err_msg:
            return _malformed_tool_call_retry(emit)

        summary = _format_model_error(err_msg, requested_model)
        if summary is None:
            summary = (
                f"[ERROR] {get_provider()} rejected request (HTTP {exc.status_code}): {err_msg}. "
                f"Try switching to a model with higher TPM (e.g. '/model llama-3.3-70b-versatile' or '/provider ollama')."
            )
    except APIError as exc:
        message = f"{exc} {getattr(exc, 'message', '')} {getattr(exc, 'body', '')}".lower()
        if (
            "tool call validation failed" in message
            or "did not match schema" in message
            or "failed to parse tool call" in message
            or "tool call argument" in message
            or "tool_use_failed" in message
        ):
            return _malformed_tool_call_retry(emit)
        if "413" in message or "tokens per minute" in message:
            return None, None, "__FORCE_COMPACT_RETRY__"
        summary = f"[ERROR] {get_provider()} error: {exc}"
    except Exception as exc:
        message = f"{exc} {getattr(exc, 'message', '')}".lower()
        if (
            "tool call validation failed" in message
            or "did not match schema" in message
            or "failed to parse tool call" in message
            or "tool call argument" in message
            or "tool_use_failed" in message
        ):
            return _malformed_tool_call_retry(emit)
        if "413" in message or "tokens per minute" in message:
            return None, None, "__FORCE_COMPACT_RETRY__"
        summary = f"[ERROR] Unexpected error from {get_provider()}: {exc}"

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
            f"Try '/model openai/gpt-oss-120b' or '/model llama-3.3-70b-versatile'. "
            f"(Resolved model: '{normalized_requested or suggested}')"
        )
    return (
        f"[ERROR] {provider} rejected model '{requested_model}'. "
        f"Try a provider-supported model name or clear SOVA_MODEL to use the default '{suggested}'."
    )


def _malformed_tool_call_retry(emit):
    emit(
        "tool_result", name="(malformed tool call)",
        result="Model produced malformed or invalid JSON arguments in tool call; automatically retrying with escaping instructions.",
    )
    return None, None, (
        "Your previous tool call failed because the arguments were not valid JSON or could not be parsed. "
        "Please retry your tool call, ensuring that all string arguments (e.g. 'content', 'old_str', 'new_str') "
        "have quotes and newlines properly JSON-escaped, and invoke only one tool call."
    )


def _parse_content_tool_calls(content, known_tool_names):
    """Detect and parse tool calls emitted as raw text JSON in message.content.
    
    This is common in smaller open-source models (e.g. Llama 3 on Ollama) which
    print JSON objects directly into content instead of using the API tool_calls channel.
    """
    if not content or not isinstance(content, str):
        return []

    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    class _SyntheticFn:
        def __init__(self, name, arguments):
            self.name = name
            self.arguments = arguments

    class _SyntheticToolCall:
        def __init__(self, fn):
            self.id = f"call_{int(time.time() * 1000)}"
            self.type = "function"
            self.function = fn

    # Attempt 1: direct parse of entire text as single object or list
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            name = data.get("name") or data.get("tool") or data.get("function")
            params = data.get("parameters") or data.get("arguments") or data.get("args")
            if params is None:
                params = {k: v for k, v in data.items() if k not in {"name", "tool", "function"}}
            if isinstance(name, str) and name in known_tool_names:
                args_str = json.dumps(params) if isinstance(params, dict) else str(params)
                return [_SyntheticToolCall(_SyntheticFn(name, args_str))]
        elif isinstance(data, list):
            res = []
            for item in data:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("tool") or item.get("function")
                    params = item.get("parameters") or item.get("arguments") or item.get("args") or {}
                    if isinstance(name, str) and name in known_tool_names:
                        args_str = json.dumps(params) if isinstance(params, dict) else str(params)
                        res.append(_SyntheticToolCall(_SyntheticFn(name, args_str)))
            if res:
                return res
    except Exception:
        pass

    # Attempt 2: regex extract JSON objects from text
    import re
    candidates = re.findall(r'(\{(?:[^{}]|(?:\{[^{}]*\}))*\})', text)
    extracted = []
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                name = data.get("name") or data.get("tool") or data.get("function")
                params = data.get("parameters") or data.get("arguments") or data.get("args")
                if params is None:
                    params = {k: v for k, v in data.items() if k not in {"name", "tool", "function"}}
                if isinstance(name, str) and name in known_tool_names and isinstance(params, dict):
                    args_str = json.dumps(params)
                    extracted.append(_SyntheticToolCall(_SyntheticFn(name, args_str)))
        except Exception:
            continue

    return extracted


def run_agent(
    root_dir, task, model=None, max_iterations=25, verbose=True, on_event=None, allow_subagents=True,
    messages=None, stop_event=None, on_permission=None, session_id=None,
):
    """Run the agent on `task` inside `root_dir` until completion, stop, or max_iterations."""
    trajectory = SessionTrajectoryLogger(root_dir, session_id) if session_id else None
    log_info(f"Starting agent task in {root_dir} (session={session_id}, provider={get_provider()})", root_dir)

    def emit(event_type, **data):
        if trajectory:
            trajectory.record_step(event_type, data)
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
        cleanup_jobs()
        log_info(f"Task finished: session={session_id}, finished={result.get('finished')}", root_dir)
        return result

    def _stopped():
        emit("error", message="Stopped by user")
        cleanup_jobs()
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
                f"ERROR: sub-agent did not confirm completion. "
                f"Last message: {sub_result['summary']}. Verify touched files before proceeding."
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

    last_failure = None
    repeat_count = 0
    did_any_tool_call = False
    nudged_to_finish = False
    malformed_retries = 0

    for _ in range(max_iterations):
        if stop_event is not None and stop_event.is_set():
            return _stopped()

        messages = _maybe_compact(messages, model, schemas)

        emit("thinking")
        response, err, retry_hint = _safe_chat(messages, schemas, model, emit)
        if err:
            return _finish(err)
        if retry_hint:
            if retry_hint == "__FORCE_COMPACT_RETRY__":
                messages = _force_compact(messages)
                continue
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

        tool_calls = message.tool_calls or []
        if not tool_calls and message.content:
            tool_calls = _parse_content_tool_calls(message.content, set(impls.keys()))
            if tool_calls and "tool_calls" not in messages[-1]:
                messages[-1]["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ]

        if not tool_calls:
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
        for tool_call in tool_calls:
            name = tool_call.function.name
            raw_arguments = tool_call.function.arguments or "{}"
            try:
                args = json.loads(raw_arguments)
            except json.JSONDecodeError as decode_err:
                args = None
                arg_err_msg = (
                    f"ERROR: Failed to parse tool call arguments as valid JSON ({decode_err}). "
                    f"Please re-call '{name}' ensuring quotes and newlines in arguments are properly formatted."
                )

            emit("tool_call", name=name, args=args if args is not None else {"_raw": raw_arguments[:200]})

            func = impls.get(name)
            diff = None
            if args is None:
                result = arg_err_msg
            elif func is None:
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
    """Default plain-print rendering of agent events."""
    if event_type == "tool_call":
        print(f"[tool] {data['name']}({data['args']})")
    elif event_type == "tool_result":
        print(f"[result] {str(data['result'])[:500]}")
    elif event_type == "answer":
        print(f"[agent] {data['text']}")
    elif event_type == "error":
        print(data["message"])
