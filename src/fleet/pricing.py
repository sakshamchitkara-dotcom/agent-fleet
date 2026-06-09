"""Estimated USD cost of a model call from its usage.

Prices are Anthropic first-party API rates in USD per million tokens, taken
from the claude-api skill's model table (cached 2026-06-24). Cache writes use
the 5-minute TTL rate (1.25x input), which is what the Claude backend's
top-level `cache_control` requests; cache reads are 0.1x input unless the
model publishes its own rate. Bedrock and Vertex model IDs are mapped to their
first-party ID; the rates are first-party list prices unless a price file
($FLEET_PRICES, `fleet --prices`) supplies the provider's own.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

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


# Bedrock: "anthropic.claude-...", cross-region profiles "us.anthropic.claude-...", legacy
# "-20251101-v1:0" suffixes. Vertex: "claude-opus-4-5@20251101".
_PROFILE = re.compile(r"^(?:[a-z]{2,6}\.)?anthropic\.")
_SUFFIX = re.compile(r"(?:@\d{8}|-\d{8})?(?:-v\d+(?::\d+)?)?$")


def canonical(model: str | None) -> str:
    """First-party model ID for a Bedrock or Vertex ID, so it finds its row in PRICES."""
    return _SUFFIX.sub("", _PROFILE.sub("", model or "", count=1), count=1)


def load_prices(path: str | Path) -> dict[str, tuple[float, float, float, float]]:
    """Custom rates (Bedrock/Vertex, regional or negotiated), $/MTok:
    {"model-id": {"input": 4.4, "output": 22, "cache_read": 0.44, "cache_write": 5.5}}.
    cache_read defaults to 0.1x input, cache_write to 1.25x input."""
    try:
        data = json.loads(Path(path).read_text())
        out = {}
        for model, r in data.items():
            inp = float(r["input"])
            out[model] = (inp, float(r["output"]), float(r.get("cache_read", inp * 0.1)),
                          float(r.get("cache_write", inp * CACHE_WRITE)))
        return out
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise ValueError(f"bad price file {path}: {type(e).__name__}: {e}") from e


@lru_cache(maxsize=4)
def _overrides(path: str, mtime: float) -> dict:
    return load_prices(path)


def overrides() -> dict:
    """Rates from the JSON file named by $FLEET_PRICES (set by `fleet --prices`), if any."""
    path = os.environ.get("FLEET_PRICES")
    return _overrides(path, os.stat(path).st_mtime) if path else {}


def price(model: str | None) -> tuple[float, float, float, float]:
    """(input, output, cache read, cache write) $/MTok. Custom rates win (exact ID, then the
    first-party ID); unknown models (and the scripted backend) are priced as the default model."""
    custom = overrides()
    if (hit := custom.get(model or "") or custom.get(canonical(model))):
        return hit
    inp, out, read = PRICES.get(canonical(model), PRICES[DEFAULT_MODEL])
    return inp, out, read, inp * CACHE_WRITE


def cost(model: str | None, usage: dict) -> float:
    """USD for one request. `input_tokens` is the full prompt size (cached parts included)."""
    inp, out, read, write = price(model)
    cached = usage.get("cache_read_input_tokens", 0)
    written = usage.get("cache_creation_input_tokens", 0)
    fresh = max(0, usage.get("input_tokens", 0) - cached - written)
    return (fresh * inp + written * write + cached * read + usage.get("output_tokens", 0) * out) / 1_000_000


def usd(amount: float) -> str:
    return f"${amount:.4f}" if amount < 1 else f"${amount:,.2f}"

