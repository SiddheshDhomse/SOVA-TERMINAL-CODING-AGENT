"""OpenAI-compatible chat wrapper supporting Groq, Ollama, Nvidia, and OpenAI providers."""
import json
import os
import re
import urllib.request
from typing import Any, Dict, List, Optional

from openai import OpenAI

from . import credentials as _creds

DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-120b",
    "ollama": "llama3.1:8b",
    "nvidia": "nvidia/nemotron-3.5-lightning-30b-a3b",
    "openai": "gpt-4o",
}

PROVIDERS_CONFIG = {
    "groq": {
        "name": "Groq (Fast Cloud Inference)",
        "default": "openai/gpt-oss-120b",
        "recommended": [
            "openai/gpt-oss-120b",
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
            "qwen-2.5-32b",
        ],
        "default_budget": 5500,  # 8000 TPM limit safety margin on free tier
    },
    "ollama": {
        "name": "Ollama (Local Offline)",
        "default": "llama3.1:8b",
        "recommended": [
            "llama3.1:8b",
            "qwen2.5-coder:7b",
            "qwen2.5-coder:14b",
            "deepseek-coder-v2:16b",
            "codellama",
        ],
        "default_budget": 16000,
    },
    "nvidia": {
        "name": "Nvidia NIM (Cloud Inference)",
        "default": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "recommended": [
            "nvidia/nemotron-3.5-lightning-30b-a3b",
            "meta/llama-3.3-70b-instruct",
            "mistralai/mistral-large-2-instruct",
        ],
        "default_budget": 32000,
    },
    "openai": {
        "name": "OpenAI (Official API)",
        "default": "gpt-4o",
        "recommended": [
            "gpt-4o",
            "gpt-4o-mini",
            "o3-mini",
            "o1-preview",
        ],
        "default_budget": 64000,
    },
}

MODEL_ALIASES = {
    "groq": {
        "gpt oss 120b": "openai/gpt-oss-120b",
        "gpt-oss 120b": "openai/gpt-oss-120b",
        "gpt oss-120b": "openai/gpt-oss-120b",
        "gpt-oss-120b": "openai/gpt-oss-120b",
        "openai gpt oss 120b": "openai/gpt-oss-120b",
        "openai/gpt-oss-120b": "openai/gpt-oss-120b",
        "gpt oss 20b": "openai/gpt-oss-20b",
        "gpt-oss 20b": "openai/gpt-oss-20b",
        "gpt-oss-20b": "openai/gpt-oss-20b",
        "openai/gpt-oss-20b": "openai/gpt-oss-20b",
        "llama 3.3 70b versatile": "openai/gpt-oss-120b",
        "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
        "llama 3.1 8b instant": "llama-3.1-8b-instant",
        "llama-3.1-8b-instant": "llama-3.1-8b-instant",
    },
}

_client = None
_client_provider = None


def invalidate_client():
    """Reset the cached LLM client so next call picks up new credentials."""
    global _client, _client_provider
    _client = None
    _client_provider = None


def get_available_providers() -> List[str]:
    return list(PROVIDERS_CONFIG.keys())


def get_models_for_provider(provider: str) -> List[str]:
    provider = provider.lower()
    config = PROVIDERS_CONFIG.get(provider, {})
    models = list(config.get("recommended", []))

    # For Ollama, dynamically probe installed models if server is reachable
    if provider == "ollama":
        host = os.environ.get("SOVA_OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        try:
            req = urllib.request.Request(f"{host}/api/tags", headers={"User-Agent": "sova"})
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                installed = [m["name"] for m in data.get("models", []) if "name" in m]
                for m in installed:
                    if m not in models:
                        models.insert(0, m)
        except Exception:
            pass  # Fallback to recommended list if unreachable

    return models


def get_provider() -> str:
    return os.environ.get("SOVA_PROVIDER", "groq").lower()


def normalize_model(model: str | None, provider: str | None = None) -> str:
    if not model:
        return ""

    provider = (provider or get_provider()).lower()
    normalized = re.sub(r"[\s_]+", " ", model.strip().lower())
    normalized = normalized.replace("/", " / ").replace("-", " - ")
    normalized = re.sub(r"\s+", " ", normalized).replace(" / ", "/").replace(" - ", "-")
    return MODEL_ALIASES.get(provider, {}).get(normalized, model.strip())


def get_model() -> str:
    configured = os.environ.get("SOVA_MODEL")
    if configured:
        return normalize_model(configured)
    return DEFAULT_MODELS.get(get_provider(), "")


def estimate_tokens(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> int:
    """Fast, reliable token estimator (~4 chars per token plus envelope overhead)."""
    total_chars = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            total_chars += len(json.dumps(content))
        tool_calls = m.get("tool_calls")
        if tool_calls:
            total_chars += len(json.dumps(tool_calls))
    if tools:
        total_chars += len(json.dumps(tools))
    # 4 chars per token heuristic + 40 tokens per message overhead
    return (total_chars // 4) + (len(messages) * 40)


def get_token_budget(provider: Optional[str] = None, model: Optional[str] = None) -> int:
    """Return max message token budget to prevent HTTP 413 TPM rate limits."""
    prov = (provider or get_provider()).lower()
    mod = (model or get_model()).lower()

    if prov == "groq":
        # Groq free tier limit on gpt-oss-120b is strictly 8,000 TPM
        if "120b" in mod:
            return 5500
        elif "70b" in mod:
            return 9000
        elif "8b" in mod:
            return 18000
        return 6000

    cfg = PROVIDERS_CONFIG.get(prov)
    return cfg.get("default_budget", 16000) if cfg else 16000


def get_client() -> OpenAI:
    global _client, _client_provider
    provider = get_provider()
    if _client is not None and _client_provider == provider:
        return _client

    if provider == "groq":
        api_key = _creds.get_active_credential("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set or invalid. Please add your Groq API key in Settings or .env file."
            )
        base_url = "https://api.groq.com/openai/v1"
    elif provider == "ollama":
        api_key = "ollama"  # unused by Ollama but required by the OpenAI client
        host = (_creds.get_active_credential("SOVA_OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        base_url = f"{host}/v1"
    elif provider == "nvidia":
        api_key = _creds.get_active_credential("NVIDIA_API_KEY")
        if not api_key:
            raise RuntimeError(
                "NVIDIA_API_KEY is not set or invalid. Please add your Nvidia API key in Settings or .env file."
            )
        base_url = "https://integrate.api.nvidia.com/v1"
    elif provider == "openai":
        api_key = _creds.get_active_credential("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set or invalid. Please add your OpenAI API key in Settings or .env file.")
        base_url = None
    else:
        raise RuntimeError(f"Unknown SOVA_PROVIDER '{provider}'. Use 'groq', 'ollama', 'nvidia', or 'openai'.")

    _client = OpenAI(api_key=api_key, base_url=base_url)
    _client_provider = provider
    return _client


def chat(messages, tools, model=None):
    client = get_client()
    kwargs = {"tools": tools, "tool_choice": "auto"} if tools else {}
    return client.chat.completions.create(
        model=normalize_model(model) or get_model(),
        messages=messages,
        temperature=0,
        **kwargs,
    )


class _StreamedMessage:
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
    def __init__(self, message, usage=None):
        self.choices = [type("Choice", (), {"message": message})()]
        self.usage = usage


def chat_stream(messages, tools, model=None, on_delta=None):
    """Stream a completion, calling on_delta(text_fragment) as content tokens arrive."""
    client = get_client()
    kwargs = {"tools": tools, "tool_choice": "auto"} if tools else {}
    stream = client.chat.completions.create(
        model=normalize_model(model) or get_model(),
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
