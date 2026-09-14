"""Token spend and cost observability engine for SOVA."""
from typing import Any, Dict, Optional, Tuple

# Pricing in USD per 1,000,000 tokens (Prompt, Completion)
# Based on official provider pricing schedules
MODEL_PRICING: Dict[str, Dict[str, Tuple[float, float]]] = {
    "groq": {
        "default": (0.59, 0.79),
        "openai/gpt-oss-120b": (0.15, 0.60),
        "llama-3.3-70b-versatile": (0.59, 0.79),
        "llama-3.1-8b-instant": (0.05, 0.08),
        "mixtral-8x7b-32768": (0.24, 0.24),
    },
    "gemini": {
        "default": (0.15, 0.60),
        "gemini-2.5-flash": (0.15, 0.60),
        "gemini-2.5-pro": (1.25, 5.00),
        "gemini-2.0-flash": (0.10, 0.40),
    },
    "openrouter": {
        "default": (1.50, 6.00),
        "anthropic/claude-3.7-sonnet": (3.00, 15.00),
        "anthropic/claude-3.5-sonnet": (3.00, 15.00),
        "deepseek/deepseek-r1": (0.55, 2.19),
        "deepseek/deepseek-chat": (0.14, 0.28),
        "openai/gpt-4o": (2.50, 10.00),
        "openai/o3-mini": (1.10, 4.40),
    },
    "openai": {
        "default": (2.50, 10.00),
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
        "o3-mini": (1.10, 4.40),
        "o1-preview": (15.00, 60.00),
    },
    "ollama": {
        "default": (0.0, 0.0),  # Local offline execution
    },
    "nvidia": {
        "default": (0.0, 0.0),  # Free NIM developer tier
    },
}


def calculate_turn_cost(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Calculate estimated USD cost for an LLM turn given provider, model, and token counts."""
    prov = (provider or "groq").lower()
    mod = (model or "").lower()

    if ":free" in mod or mod.endswith(":free"):
        return 0.0

    prov_table = MODEL_PRICING.get(prov, MODEL_PRICING["groq"])
    prompt_rate, completion_rate = prov_table.get(mod, prov_table["default"])

    cost = ((prompt_tokens / 1_000_000.0) * prompt_rate) + ((completion_tokens / 1_000_000.0) * completion_rate)
    return round(cost, 6)


def format_cost(cost_usd: float) -> str:
    """Format USD cost into human-readable representation."""
    if cost_usd <= 0.0:
        return "$0.00 (Free/Local)"
    if cost_usd < 0.01:
        return f"${cost_usd:.4f}"
    return f"${cost_usd:.2f}"


def format_token_cost_summary(metrics: Dict[str, Any]) -> str:
    """Format metrics dictionary into clean one-line status string."""
    total_tokens = metrics.get("total_tokens", 0)
    prompt_tokens = metrics.get("prompt_tokens", 0)
    completion_tokens = metrics.get("completion_tokens", 0)
    cost = metrics.get("cost_usd", 0.0)
    turns = metrics.get("turns", 0)
    elapsed = metrics.get("elapsed_seconds", 0.0)

    cost_str = format_cost(cost)
    return (
        f"{turns} turn(s) in {elapsed:.1f}s | "
        f"Tokens: {total_tokens:,} (prompt: {prompt_tokens:,}, completion: {completion_tokens:,}) | "
        f"Cost: {cost_str}"
    )
