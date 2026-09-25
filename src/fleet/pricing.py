"""Estimated USD cost of a model call from its usage.

Prices are Anthropic first-party API rates in USD per million tokens, taken
from the claude-api skill's model table (cached 2026-06-24). Cache writes use
the 5-minute TTL rate (1.25x input), which is what the Claude backend's
top-level `cache_control` requests; cache reads are 0.1x input unless the
model publishes its own rate. Bedrock/Vertex pricing differs.
"""

from __future__ import annotations

from .models import DEFAULT_MODEL

# model: (input, output, cache read) $/MTok
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-fable-5-1": (10.00, 50.00, 0.25),
    "claude-fable-5": (10.00, 50.00, 1.00),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-opus-4-7": (5.00, 25.00, 0.50),
    "claude-opus-4-6": (5.00, 25.00, 0.50),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-sonnet-4-6": (3.00, 15.00, 0.30),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}
CACHE_WRITE = 1.25


def price(model: str | None) -> tuple[float, float, float]:
    """Unknown models (and the scripted backend) are priced as the default model."""
    return PRICES.get(model or "", PRICES[DEFAULT_MODEL])


def cost(model: str | None, usage: dict) -> float:
    """USD for one request. `input_tokens` is the full prompt size (cached parts included)."""
    inp, out, read = price(model)
    cached = usage.get("cache_read_input_tokens", 0)
    written = usage.get("cache_creation_input_tokens", 0)
    fresh = max(0, usage.get("input_tokens", 0) - cached - written)
    return (fresh * inp + written * inp * CACHE_WRITE + cached * read
            + usage.get("output_tokens", 0) * out) / 1_000_000


def usd(amount: float) -> str:
    return f"${amount:.4f}" if amount < 1 else f"${amount:,.2f}"

