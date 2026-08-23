"""OpenAI-compatible chat wrapper supporting the Groq and Ollama providers."""
import os

from openai import OpenAI

DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "ollama": "llama3.1:8b",
    "nvidia": "nvidia/nemotron-3.5-lightning-30b-a3b",
}

_client = None
_client_provider = None


def get_provider() -> str:
    return os.environ.get("SOVA_PROVIDER", "groq").lower()


def get_model() -> str:
    return os.environ.get("SOVA_MODEL") or DEFAULT_MODELS.get(get_provider(), "")


def get_client() -> OpenAI:
    global _client, _client_provider
    provider = get_provider()
    if _client is not None and _client_provider == provider:
        return _client

    if provider == "groq":
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Copy .env.example to .env and add your free Groq API key."
            )
        base_url = "https://api.groq.com/openai/v1"
    elif provider == "ollama":
        api_key = "ollama"  # unused by Ollama but required by the OpenAI client
        host = os.environ.get("SOVA_OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        base_url = f"{host}/v1"
    elif provider == "nvidia":
        api_key = os.environ.get("NVIDIA_API_KEY")
        if not api_key:
            raise RuntimeError(
                "NVIDIA_API_KEY is not set. Add your Nvidia API key from https://build.nvidia.com."
            )
        base_url = "https://integrate.api.nvidia.com/v1"
    else:
        raise RuntimeError(f"Unknown SOVA_PROVIDER '{provider}'. Use 'groq', 'ollama', or 'nvidia'.")

    _client = OpenAI(api_key=api_key, base_url=base_url)
    _client_provider = provider
    return _client


def chat(messages, tools, model=None):
    client = get_client()
    kwargs = {"tools": tools, "tool_choice": "auto"} if tools else {}
    return client.chat.completions.create(
        model=model or get_model(),
        messages=messages,
        temperature=0,
        **kwargs,
    )


class _StreamedMessage:
    """Minimal stand-in for the non-streaming SDK's `message` object, built by
    accumulating deltas so callers can treat streamed and non-streamed responses the same way."""

    def __init__(self, content, tool_calls):
        self.content = content
        self.tool_calls = tool_calls
        self.role = "assistant"

    def model_dump(self, exclude_none=False):
        dump = {"role": self.role, "content": self.content}
        if self.tool_calls:
            dump["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in self.tool_calls
            ]
        elif exclude_none:
            dump.pop("tool_calls", None)
        return dump


class _ToolCallFn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, id, function):
        self.id = id
        self.function = function


class _StreamedResponse:
    def __init__(self, message):
        self.choices = [type("Choice", (), {"message": message})()]


def chat_stream(messages, tools, model=None, on_delta=None):
    """Stream a completion, calling on_delta(text_fragment) as content tokens arrive.
    Returns a response object shaped like the non-streaming chat() response so callers
    (the agent loop) can handle both uniformly."""
    client = get_client()
    kwargs = {"tools": tools, "tool_choice": "auto"} if tools else {}
    stream = client.chat.completions.create(
        model=model or get_model(),
        messages=messages,
        temperature=0,
        stream=True,
        **kwargs,
    )

    content_parts = []
    tool_calls = {}  # index -> {"id":..., "name":..., "arguments": [parts]}

    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            content_parts.append(delta.content)
            if on_delta:
                on_delta(delta.content)
        for tc_delta in delta.tool_calls or []:
            entry = tool_calls.setdefault(tc_delta.index, {"id": None, "name": None, "arguments": []})
            if tc_delta.id:
                entry["id"] = tc_delta.id
            if tc_delta.function:
                if tc_delta.function.name:
                    entry["name"] = tc_delta.function.name
                if tc_delta.function.arguments:
                    entry["arguments"].append(tc_delta.function.arguments)

    content = "".join(content_parts) or None
    ordered = [tool_calls[i] for i in sorted(tool_calls)]
    calls = [
        _ToolCall(id=e["id"], function=_ToolCallFn(name=e["name"], arguments="".join(e["arguments"])))
        for e in ordered
    ]
    return _StreamedResponse(_StreamedMessage(content, calls))
