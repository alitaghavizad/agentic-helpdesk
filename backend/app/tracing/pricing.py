from __future__ import annotations

import json
from decimal import Decimal

from app.config import get_settings

# USD per million tokens (spec 17). Only claude-opus-5 is seeded here --
# its rate is stated exactly in the spec. No Gemini entry is hardcoded:
# inventing an unverified rate would violate spec 17's own principle that
# an unpriced model must render as "unpriced," not a plausible-looking
# wrong number. Add a real rate via MODEL_PRICING_OVERRIDES when needed.
DEFAULT_RATES: dict[str, dict[str, Decimal]] = {
    "claude-opus-5": {"input": Decimal("5.00"), "output": Decimal("25.00")},
}

CACHE_WRITE_MULTIPLIER = Decimal("1.25")
CACHE_READ_MULTIPLIER = Decimal("0.1")
_PER_MILLION = Decimal(1_000_000)


def _rate_table() -> dict[str, dict[str, Decimal]]:
    table = {model: dict(rates) for model, rates in DEFAULT_RATES.items()}
    raw_overrides = get_settings().model_pricing_overrides
    if raw_overrides:
        overrides = json.loads(raw_overrides)
        for model, rates in overrides.items():
            table[model] = {
                "input": Decimal(str(rates["input"])),
                "output": Decimal(str(rates["output"])),
            }
    return table


def cost_for(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Decimal | None:
    """Returns the USD cost for one call's usage, or None if `model` has no
    known rate -- never raises, and never fabricates a number (spec 17)."""
    rates = _rate_table().get(model)
    if rates is None:
        return None
    input_rate = rates["input"]
    output_rate = rates["output"]
    total = (
        Decimal(input_tokens) * input_rate
        + Decimal(output_tokens) * output_rate
        + Decimal(cache_write_tokens) * input_rate * CACHE_WRITE_MULTIPLIER
        + Decimal(cache_read_tokens) * input_rate * CACHE_READ_MULTIPLIER
    ) / _PER_MILLION
    return total.quantize(Decimal("0.000001"))
