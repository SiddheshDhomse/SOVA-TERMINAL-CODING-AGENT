"""Credential / API-key management, persisted in .sova/credentials.json."""
import json
import os
from typing import Optional


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _credentials_path(root_dir: str) -> str:
    """Return the absolute path to the credentials file."""
    return os.path.join(root_dir, ".sova", "credentials.json")


# ---------------------------------------------------------------------------
# Load / Save (atomic write, same pattern as sessions.py)
# ---------------------------------------------------------------------------

def load_credentials(root_dir: str) -> dict:
    """Read and return all saved credentials.  Returns ``{}`` if the file does not exist."""
    path = _credentials_path(root_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_credentials(root_dir: str, credentials: dict) -> None:
    """Atomic-write *credentials* dict to ``.sova/credentials.json``."""
    path = _credentials_path(root_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(credentials, f, indent=2)
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# Single-key helpers
# ---------------------------------------------------------------------------

def get_credential(root_dir: str, key: str) -> Optional[str]:
    """Fetch a single credential value by *key* name, or ``None`` if absent."""
    return load_credentials(root_dir).get(key)


def set_credential(root_dir: str, key: str, value: str) -> None:
    """Set a single credential, merge into the existing store, and persist."""
    creds = load_credentials(root_dir)
    creds[key] = value
    save_credentials(root_dir, creds)


def delete_credential(root_dir: str, key: str) -> bool:
    """Remove a credential by *key*.  Return ``True`` if the key existed."""
    creds = load_credentials(root_dir)
    if key not in creds:
        return False
    del creds[key]
    save_credentials(root_dir, creds)
    return True


# ---------------------------------------------------------------------------
# Environment injection
# ---------------------------------------------------------------------------

def apply_credentials(root_dir: str) -> None:
    """Load all credentials and inject them into ``os.environ``.

    Only sets env vars that are **not** already present so that explicit
    environment variables are never overridden.
    """
    for key, value in load_credentials(root_dir).items():
        if key not in os.environ:
            os.environ[key] = value


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def mask_value(value: str) -> str:
    """Return a masked version of *value* suitable for display.

    * Length ≤ 8  → ``'****'``
    * Otherwise   → ``'****'`` + last 4 characters
    """
    if len(value) <= 8:
        return "****"
    return "****" + value[-4:]


# ---------------------------------------------------------------------------
# Known / suggested keys
# ---------------------------------------------------------------------------

def get_known_keys() -> list[dict]:
    """Return a list of dicts describing known / suggested credential keys."""
    return [
        {"key": "GROQ_API_KEY", "label": "Groq API Key", "category": "llm", "hint": "Get from console.groq.com"},
        {"key": "OPENAI_API_KEY", "label": "OpenAI API Key", "category": "llm", "hint": "Get from platform.openai.com"},
        {"key": "NVIDIA_API_KEY", "label": "Nvidia API Key", "category": "llm", "hint": "Get from build.nvidia.com"},
        {"key": "SOVA_OLLAMA_HOST", "label": "Ollama Host URL", "category": "llm", "hint": "Default: http://localhost:11434"},
    ]


# ---------------------------------------------------------------------------
# Credential testing
# ---------------------------------------------------------------------------

def test_credential(root_dir: str, key: str) -> dict:
    """Test whether a stored credential is valid.

    Returns ``{"ok": bool, "message": str}``.

    * ``GROQ_API_KEY``    – creates an OpenAI client with the Groq base URL and lists models.
    * ``OPENAI_API_KEY``  – creates an OpenAI client and lists models.
    * ``NVIDIA_API_KEY``  – creates an OpenAI client with the Nvidia base URL and lists models.
    * ``SOVA_OLLAMA_HOST``– sends an HTTP GET to ``{host}/api/tags``.
    * Unknown keys        – returns a generic success message.
    """
    value = get_credential(root_dir, key)
    if value is None:
        return {"ok": False, "message": f"No credential stored for {key}"}

    if key == "GROQ_API_KEY":
        return _test_openai_compatible(value, base_url="https://api.groq.com/openai/v1", label="Groq")

    if key == "OPENAI_API_KEY":
        return _test_openai_compatible(value, base_url=None, label="OpenAI")

    if key == "NVIDIA_API_KEY":
        return _test_openai_compatible(value, base_url="https://integrate.api.nvidia.com/v1", label="Nvidia")

    if key == "SOVA_OLLAMA_HOST":
        return _test_ollama(value)

    return {"ok": True, "message": "Saved (no test available)"}


def _test_openai_compatible(api_key: str, base_url: Optional[str], label: str) -> dict:
    """Try to list models via the OpenAI-compatible API."""
    try:
        from openai import OpenAI  # type: ignore[import-untyped]

        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
        models = client.models.list()
        count = len(list(models))
        return {"ok": True, "message": f"{label} OK – {count} model(s) available"}
    except Exception as exc:
        return {"ok": False, "message": f"{label} error: {exc}"}


def _test_ollama(host: str) -> dict:
    """Try an HTTP GET to the Ollama ``/api/tags`` endpoint."""
    try:
        import urllib.request
        import urllib.error

        url = host.rstrip("/") + "/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        count = len(data.get("models", []))
        return {"ok": True, "message": f"Ollama OK – {count} model(s) available"}
    except Exception as exc:
        return {"ok": False, "message": f"Ollama error: {exc}"}
