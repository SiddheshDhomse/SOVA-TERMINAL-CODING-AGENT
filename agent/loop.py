"""The agentic tool-calling loop shared by interactive use, web console, and evaluation runs."""
import inspect
import json
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

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
from .subagents import (
    ROLE_GENERAL,
    filter_tools_for_role,
    get_role_config,
)
from .tools import build_tools, cleanup_jobs

_SENSITIVE_TOOLS = {"write_file", "edit_file", "run_shell"}
_COMPACT_THRESHOLD = 30  # message count threshold
_COMPACT_KEEP_RECENT = 12  # messages to preserve verbatim

_SPAWN_SUBAGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "spawn_subagent",
        "description": (
            "Delegate a self-contained subtask to a specialized sub-agent with its own tool-calling loop. "
            "Select the appropriate role: "
            "'researcher' (strictly read-only codebase exploration, symbol lookups, zero edit/shell permissions), "
            "'coder' (precision code edits, file creation, and local testing), "
            "'reviewer' (diff inspection and test suite verification with zero code edits), or "
            "'general' (general execution). Sub-agents cannot recursively spawn child sub-agents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subtask": {
                    "type": "string",
                    "description": "A clear, self-contained description of the subtask.",
                },
                "role": {
                    "type": "string",
                    "enum": ["researcher", "coder", "reviewer", "general"],
                    "description": "Role specialization of the sub-agent: 'researcher', 'coder', 'reviewer', or 'general' (default: 'general').",
                },
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
    task_str = str(task or "").strip()
    if not task_str:
        return True
    task_lower = task_str.lower()
    task_indicators = [
        "bug", "issue", "error", "exception", "traceback", "fix", "implement", "create",
        "add", "edit", "write", "test", "def ", "class ", "import ", "python", "diff",
        ".py", ".js", ".ts", ".html", ".css", ".md", ".json", "failed", "failing",
    ]
    if any(ind in task_lower for ind in task_indicators):
        return False
    if len(task_str) > 120 or "\n" in task_str:
        return False

    try:
        response = chat(
            [{"role": "system", "content": _CLASSIFY_SYSTEM}, {"role": "user", "content": task_str}],
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
        "Your previous tool call failed because the arguments were not valid JSON or could not be parsed by the API. "
        "Please retry your tool call as a single, valid tool call with properly formatted JSON arguments. "
        "Note: In file contents or commands, newlines should be actual line breaks, NOT literal '\\n' strings."
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

    # Attempt 2: balanced brace extraction of JSON objects from text
    def _extract_balanced_json(s):
        candidates = []
        n = len(s)
        i = 0
        while i < n:
            if s[i] == '{':
                start = i
                depth = 0
                in_str = False
                esc = False
                j = i
                while j < n:
                    ch = s[j]
                    if in_str:
                        if esc:
                            esc = False
                        elif ch == '\\':
                            esc = True
                        elif ch == '"':
                            in_str = False
                    else:
                        if ch == '"':
                            in_str = True
                        elif ch == '{':
                            depth += 1
                        elif ch == '}':
                            depth -= 1
                            if depth == 0:
                                candidates.append(s[start:j + 1])
                                i = j
                                break
                    j += 1
            i += 1
        return candidates

    extracted = []
    for cand in _extract_balanced_json(text):
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
    messages=None, stop_event=None, on_permission=None, session_id=None, force_task=False,
    system_prompt=None, custom_tools=None, subagent_id=None, subagent_role=None,
    sandbox=None,
):
    """Run the agent on `task` inside `root_dir` (or sandbox worktree if provided) until completion, stop, or max_iterations."""
    exec_dir = root_dir
    if sandbox is not None:
        if not getattr(sandbox, "created", False):
            sandbox.create()
        exec_dir = sandbox.worktree_dir

    import time
    start_time = time.time()
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cost_usd = 0.0
    turn_count = 0

    trajectory = SessionTrajectoryLogger(root_dir, session_id) if session_id else None
    log_info(f"Starting agent task in {exec_dir} (session={session_id}, provider={get_provider()}, sandbox={bool(sandbox)})", root_dir)

    def emit(event_type, **data):
        if trajectory:
            trajectory.record_step(event_type, data)
        if on_event:
            on_event({"type": event_type, **data})
        elif verbose:
            _print_event(event_type, data)

    def _finish(result):
        total_tokens = total_prompt_tokens + total_completion_tokens
        elapsed = round(time.time() - start_time, 2)
        result["metrics"] = {
            "turns": turn_count,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": round(total_cost_usd, 6),
            "elapsed_seconds": elapsed,
        }
        if sandbox is not None:
            result["sandbox"] = {
                "active": True,
                "branch": getattr(sandbox, "branch_name", ""),
                "worktree": exec_dir,
                "has_changes": sandbox.has_changes(),
            }
        if session_id:
            try:
                events = trajectory.get_trajectory() if trajectory else None
                sessions.save_session(
                    root_dir,
                    session_id,
                    messages,
                    result=result,
                    events=events,
                    metadata={
                        "provider": get_provider(),
                        "model": model or get_model(),
                        "metrics": result.get("metrics"),
                    },
                )
            except OSError:
                pass
        cleanup_jobs()
        try:
            cost_display = f"${float(total_cost_usd):.4f}"
        except (TypeError, ValueError):
            cost_display = "$0.00"
        log_info(f"Task finished: session={session_id}, finished={result.get('finished')}, tokens={total_tokens}, cost={cost_display}", root_dir)
        return result

    def _stopped():
        emit("error", message="Stopped by user")
        cleanup_jobs()
        return _finish({"messages": messages, "finished": False, "summary": "Stopped by user"})

    if sandbox is not None:
        emit("sandbox_status", active=True, branch=getattr(sandbox, "branch_name", ""), worktree=exec_dir)

    emit("user_prompt", text=task)
    if custom_tools is not None:
        schemas, impls = custom_tools
    else:
        schemas, impls = build_tools(exec_dir, session_id=session_id)

    if allow_subagents:
        import uuid

        def spawn_subagent(subtask, role="general", **kwargs):
            role_cfg = get_role_config(role)
            sub_id = f"sub-{uuid.uuid4().hex[:6]}"

            # Emit start event for hierarchy rendering
            emit(
                "subagent_start",
                id=sub_id,
                role=role_cfg.role,
                name=role_cfg.name,
                icon=role_cfg.icon,
                color=role_cfg.color,
                task=subtask,
            )

            # Partition tools according to role permissions
            base_schemas, base_impls = build_tools(exec_dir, session_id=session_id)
            sub_schemas, sub_impls = filter_tools_for_role(base_schemas, base_impls, role_cfg.allowed_tools)

            def _sub_event(event):
                enriched = {
                    **event,
                    "subagent": subtask[:40],
                    "subagent_id": sub_id,
                    "subagent_role": role_cfg.role,
                    "subagent_name": role_cfg.name,
                    "subagent_icon": role_cfg.icon,
                }
                if on_event:
                    on_event(enriched)
                elif verbose:
                    _print_event(enriched.get("type", ""), enriched)

            sub_result = run_agent(
                root_dir, subtask, model=model, max_iterations=role_cfg.max_iterations, verbose=False,
                on_event=_sub_event, allow_subagents=False,
                stop_event=stop_event, on_permission=on_permission,
                system_prompt=role_cfg.system_prompt,
                subagent_id=sub_id, subagent_role=role_cfg.role,
                custom_tools=(sub_schemas, sub_impls),
            )

            success = sub_result.get("finished", False)
            summary = sub_result.get("summary", "")

            # Emit finish event
            emit(
                "subagent_finish",
                id=sub_id,
                role=role_cfg.role,
                name=role_cfg.name,
                icon=role_cfg.icon,
                color=role_cfg.color,
                task=subtask,
                success=success,
                summary=summary,
            )

            if success:
                return f"[{role_cfg.name.upper()} SUBAGENT COMPLETED] {summary}"
            return (
                f"[{role_cfg.name.upper()} SUBAGENT INCOMPLETE] {summary}. "
                f"Verify touched files before proceeding."
            )

        schemas = schemas + [_SPAWN_SUBAGENT_SCHEMA]
        impls = {**impls, "spawn_subagent": spawn_subagent}

    if messages is None:
        memory_note = impls["memory_read"]() if "memory_read" in impls else "(memory is empty)"
        summary = impls.get("workspace_summary", lambda: "")()
        sys_content = system_prompt or SYSTEM_PROMPT
        messages = [{"role": "system", "content": sys_content}]
        if summary and summary != "Workspace initialized.":
            messages.append({"role": "system", "content": f"Workspace Context: {summary}"})
        if memory_note != "(memory is empty)":
            messages.append({"role": "system", "content": f"Project memory (persisted notes from previous runs):\n{memory_note}"})
        messages.append({"role": "user", "content": task})
    else:
        messages.append({"role": "user", "content": task})

    if not force_task and _is_chat(task, model):
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
        emit("answer", text=message.content, verified=True, chat=True)
        return _finish({"messages": messages, "finished": True, "summary": message.content, "chat": True})

    last_failure = None
    repeat_count = 0
    did_any_tool_call = False
    nudged_to_finish = False
    nudged_to_tool = False
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

        turn_count += 1
        p_tok = 0
        c_tok = 0
        usage = getattr(response, "usage", None)
        if usage is not None:
            try:
                p_val = getattr(usage, "prompt_tokens", 0)
                c_val = getattr(usage, "completion_tokens", 0)
                p_tok = int(p_val) if p_val is not None else 0
                c_tok = int(c_val) if c_val is not None else 0
                total_prompt_tokens += p_tok
                total_completion_tokens += c_tok
                active_prov = get_provider()
                active_model = model or get_model()
                from .cost import calculate_turn_cost
                turn_cost = calculate_turn_cost(active_prov, active_model, p_tok, c_tok)
                total_cost_usd += turn_cost
                emit(
                    "token_usage",
                    turn=turn_count,
                    prompt_tokens=p_tok,
                    completion_tokens=c_tok,
                    total_tokens=p_tok + c_tok,
                    cost_usd=turn_cost,
                    cumulative_cost=round(total_cost_usd, 6),
                )
            except (TypeError, ValueError):
                pass

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
            if force_task and not did_any_tool_call and not nudged_to_tool:
                nudged_to_tool = True
                messages.append({
                    "role": "user",
                    "content": "This task requires inspecting and modifying files in the repository. "
                               "Please call the appropriate tools (e.g. grep, find_files, read_file, edit_file) "
                               "to locate the issue and apply code changes.",
                })
                continue
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
            if func is None and "." in name:
                short_name = name.split(".")[-1]
                if short_name in impls:
                    name = short_name
                    func = impls[name]
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
                    if isinstance(raw, tuple):
                        if len(raw) == 2:
                            result, diff = raw
                        elif len(raw) == 3 and isinstance(raw[0], bool):
                            result, diff = raw[1], None
                        elif len(raw) >= 1:
                            result, diff = raw[0], (raw[1] if len(raw) > 1 else None)
                        else:
                            result, diff = None, None
                    else:
                        result, diff = raw, None
                except TypeError as exc:
                    expected = list(inspect.signature(func).parameters)
                    result = f"ERROR: invalid arguments for '{name}' ({exc}). Expected keys: {expected}"
                except Exception as exc:
                    result = f"ERROR: {exc}"

            emit("tool_result", name=name, result=result, diff=diff)

            if isinstance(result, str) and "[WARNING] SYNTAX/LINT ERROR DETECTED" in result:
                emit("diagnostic_warning", name=name, message=result)

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


def _safe_print(text):
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            print(text.encode(encoding, errors="replace").decode(encoding))
        except Exception:
            print(text.encode("ascii", errors="replace").decode("ascii"))


def _print_event(event_type, data):
    """Default plain-print rendering of agent events."""
    prefix = ""
    if data.get("subagent_name"):
        prefix = f"[{data.get('subagent_icon', '🤖')} {data.get('subagent_name')}] "
    elif data.get("subagent"):
        prefix = f"[{data['subagent']}] "

    if event_type == "subagent_start":
        _safe_print(f"--> [{data.get('icon', '🤖')} {data.get('name', 'Sub-Agent')}] Starting: {data.get('task')}")
    elif event_type == "subagent_finish":
        status = "Completed" if data.get("success") else "Incomplete"
        _safe_print(f"<-- [{data.get('icon', '🤖')} {data.get('name', 'Sub-Agent')}] {status}: {data.get('summary', '')}")
    elif event_type == "tool_call":
        _safe_print(f"{prefix}[tool] {data['name']}({data['args']})")
    elif event_type == "tool_result":
        _safe_print(f"{prefix}[result] {str(data['result'])[:500]}")
    elif event_type == "answer":
        _safe_print(f"{prefix}[agent] {data.get('text', '')}")
    elif event_type == "error":
        _safe_print(f"{prefix}{str(data.get('message', ''))}")
