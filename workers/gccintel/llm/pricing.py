"""Per-model token prices (USD per million tokens) for cost accounting.

Claude prices are Anthropic first-party list prices as documented on 2026-11-03.
Other providers' prices change frequently and are NOT hard-coded: set
LLM_PRICE_INPUT_PER_MTOK / LLM_PRICE_OUTPUT_PER_MTOK for them, otherwise cost is recorded
as 0 with `price_known = False` so dashboards can flag it.
"""

from __future__ import annotations

import os

CLAUDE_PRICES: dict[str, tuple[float, float, float]] = {
    # model: (input, output, cache_read)
    "claude-fable-5-2": (8.0, 40.0, 0.20),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


def price_for(model: str) -> tuple[float, float, float, bool]:
    if model in CLAUDE_PRICES:
        i, o, c = CLAUDE_PRICES[model]
        return i, o, c, True
    env_in, env_out = os.environ.get("LLM_PRICE_INPUT_PER_MTOK"), os.environ.get("LLM_PRICE_OUTPUT_PER_MTOK")
    if env_in and env_out:
        i, o = float(env_in), float(env_out)
        return i, o, i * 0.1, True
    return 0.0, 0.0, 0.0, False


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> tuple[float, bool]:
    i, o, c, known = price_for(model)
    cost = (input_tokens * i + output_tokens * o + cache_read * c + cache_write * i * 1.25) / 1_000_000
    return round(cost, 6), known
