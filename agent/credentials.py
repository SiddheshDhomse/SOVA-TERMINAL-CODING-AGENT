"""Credential / API-key management, persisted in .sova/credentials.json."""
import json
import os
import re
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _credentials_path(root_dir: str) -> str:
    """Return the absolute path to the credentials file."""
    return os.path.join(root_dir, ".sova", "credentials.json")


# ---------------------------------------------------------------------------
# Placeholder detection
# ---------------------------------------------------------------------------

def is_placeholder(value: Optional[str]) -> bool:
    """Return True if *value* is empty, None, or a default placeholder string."""
    if not value or not isinstance(value, str):
        return True
    val = value.strip().lower()
    if not val:
        return True
    if val.startswith("your_") or val.endswith("_here") or "your_groq_api_key" in val or "your_openai" in val:
        return True
    if val in ("your_api_key", "your_groq_api_key_here", "your_nvidia_api_key_here", "your_openai_api_key_here"):
        return True
    return False


# ---------------------------------------------------------------------------
# Load / Save (atomic write, same pattern as sessions.py)
# ---------------------------------------------------------------------------

def load_credentials(root_dir: Optional[str] = None) -> Dict[str, str]:
    """Read and return all saved credentials. Returns {} if file missing or corrupt."""
    root = root_dir or os.getcwd()
    path = _credentials_path(root)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {k: str(v) for k, v in data.items() if isinstance(v, str)}
    except (json.JSONDecodeError, OSError):
        return {}


def save_credentials(root_dir: str, credentials: Dict[str, str]) -> None:
    """Atomic-write *credentials* dict to ``.sova/credentials.json``."""
    path = _credentials_path(root_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(credentials, f, indent=2)
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# Key Resolution & Mutation
# ---------------------------------------------------------------------------

CREDENTIAL_KEY_ALIASES = {
    "OPENROUTER_API_KEY": [
        "OPENROUTER_API_KEY",
        "OPEN_ROUTE_API_KEY",
        "OPEN_ROUTER_API_KEY",
        "OPENROUTE_API_KEY",
    ],
    "OPEN_ROUTE_API_KEY": [
        "OPEN_ROUTE_API_KEY",
        "OPENROUTER_API_KEY",
        "OPEN_ROUTER_API_KEY",
        "OPENROUTE_API_KEY",
    ],
}


def get_active_credential(key: str, root_dir: Optional[str] = None) -> Optional[str]:
    """Return the active credential for *key*, enforcing precedence:
    1. Saved UI credential in .sova/credentials.json (if valid & not placeholder)
    2. Environment variable in os.environ (if valid & not placeholder)
    """
    root = root_dir or os.getcwd()
    keys_to_check = CREDENTIAL_KEY_ALIASES.get(key, [key])

    saved_dict = load_credentials(root)
    for k in keys_to_check:
        saved = saved_dict.get(k)
        if saved and not is_placeholder(saved):
            return saved.strip()

    for k in keys_to_check:
        env_val = os.environ.get(k)
        if env_val and not is_placeholder(env_val):
            return env_val.strip()

    return None


def get_credential(root_dir: str, key: str) -> Optional[str]:
    """Fetch a single stored credential value by *key* name."""
    return get_active_credential(key, root_dir)


def set_credential(root_dir: str, key: str, value: str) -> None:
    """Set a single credential, update .sova/credentials.json, and sync os.environ."""
    creds = load_credentials(root_dir)
    clean_val = value.strip()
    creds[key] = clean_val
    save_credentials(root_dir, creds)
    os.environ[key] = clean_val


def delete_credential(root_dir: str, key: str) -> bool:
    """Remove a credential by *key*. Syncs both file storage and os.environ."""
    creds = load_credentials(root_dir)
    existed = key in creds
    if key in creds:
        del creds[key]
        save_credentials(root_dir, creds)
    os.environ.pop(key, None)
    return existed or (key in os.environ)


def apply_credentials(root_dir: str) -> None:
    """Load stored credentials and inject non-placeholder values into os.environ."""
    creds = load_credentials(root_dir)
    for key, value in creds.items():
        if value and not is_placeholder(value):
            os.environ[key] = value.strip()


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def mask_value(value: str) -> str:
    """Return a masked version of *value* suitable for display."""
    if not value or is_placeholder(value):
        return ""
    if len(value) <= 8:
        return "••••••••"
    return "••••••••" + value[-4:]


# ---------------------------------------------------------------------------
# Known / suggested keys catalog
# ---------------------------------------------------------------------------

def get_known_keys() -> List[Dict[str, str]]:
    """Return a list of dicts describing known / suggested credential keys."""
    return [
        {"key": "GROQ_API_KEY", "label": "Groq API Key", "category": "llm", "hint": "console.groq.com"},
        {"key": "OPENAI_API_KEY", "label": "OpenAI API Key", "category": "llm", "hint": "platform.openai.com"},
        {"key": "GEMINI_API_KEY", "label": "Google Gemini API Key", "category": "llm", "hint": "aistudio.google.com"},
        {"key": "OPENROUTER_API_KEY", "label": "OpenRouter API Key", "category": "llm", "hint": "openrouter.ai"},
        {"key": "NVIDIA_API_KEY", "label": "Nvidia NIM API Key", "category": "llm", "hint": "build.nvidia.com"},
        {"key": "SOVA_OLLAMA_HOST", "label": "Ollama Host URL", "category": "llm", "hint": "http://localhost:11434"},
    ]


# ---------------------------------------------------------------------------
# Credential testing
# ---------------------------------------------------------------------------

def test_credential(root_dir: str, key: str, value: Optional[str] = None) -> dict:
    """Test whether a credential value (or stored active key) is valid."""
    target_val = value.strip() if value and value.strip() else get_active_credential(key, root_dir)

    if not target_val or is_placeholder(target_val):
        return {"ok": False, "message": f"No valid API key specified for {key}"}

    if key == "GROQ_API_KEY":
        return _test_openai_compatible(target_val, base_url="https://api.groq.com/openai/v1", label="Groq")

    if key == "OPENAI_API_KEY":
        return _test_openai_compatible(target_val, base_url=None, label="OpenAI")

    if key == "GEMINI_API_KEY":
        return _test_openai_compatible(target_val, base_url="https://generativelanguage.googleapis.com/v1beta/openai/", label="Google Gemini")

    if key in ("OPENROUTER_API_KEY", "OPEN_ROUTE_API_KEY", "OPEN_ROUTER_API_KEY", "OPENROUTE_API_KEY"):
        return _test_openai_compatible(target_val, base_url="https://openrouter.ai/api/v1", label="OpenRouter")

    if key == "NVIDIA_API_KEY":
        return _test_openai_compatible(target_val, base_url="https://integrate.api.nvidia.com/v1", label="Nvidia")

    if key == "SOVA_OLLAMA_HOST":
        return _test_ollama(target_val)

    return {"ok": True, "message": "Saved key configured"}


def _test_openai_compatible(api_key: str, base_url: Optional[str], label: str) -> dict:
    """Try to list models via the OpenAI-compatible API."""
    try:
        from openai import OpenAI
        kwargs: dict = {"api_key": api_key, "timeout": 8.0}
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
        models = client.models.list()
        count = len(list(models))
        return {"ok": True, "message": f"{label} connected ({count} models available)"}
    except Exception as exc:
        msg = str(exc)
        if "401" in msg or "Authentication" in msg or "invalid_api_key" in msg:
            return {"ok": False, "message": f"{label} authentication failed (401 Invalid Key)"}
        return {"ok": False, "message": f"{label} test error: {msg[:120]}"}


def _test_ollama(host: str) -> dict:
    """Try an HTTP GET to the Ollama /api/tags endpoint."""
    try:
        import urllib.request
        url = host.rstrip("/") + "/api/tags"
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "sova"})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        count = len(data.get("models", []))
        return {"ok": True, "message": f"Ollama reachable ({count} models installed)"}
    except Exception as exc:
        return {"ok": False, "message": f"Ollama offline or unreachable: {exc}"}
