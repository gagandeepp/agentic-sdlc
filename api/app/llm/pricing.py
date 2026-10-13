"""Per-model token prices and the cost of one API response."""

import logging
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from app.paths import CONFIG_DIR

log = logging.getLogger(__name__)

# Output tokens assumed when reserving budget before a request (the CLI asks for 128K).
RESERVE_OUTPUT_TOKENS = 8_000


class Rates(BaseModel):
    input: float
    output: float
    cache_read: float


class LongContextRates(Rates):
    threshold_tokens: int


class ModelPricing(Rates):
    context_window: int
    long_context: LongContextRates | None = None


class PricingTable(BaseModel):
    models: dict[str, ModelPricing]
    cache_write_multiplier: dict[Literal["ephemeral_5m", "ephemeral_1h"], float]

    def pricing_for(self, model: str) -> ModelPricing:
        found = self.models.get(model)
        if found is not None:
            return found
        log.warning("no pricing for model %r; charging the most expensive known rates", model)
        return max(self.models.values(), key=lambda pricing: pricing.output)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_5m_tokens: int | None = None
    cache_write_1h_tokens: int | None = None

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> "Usage":
        def count(value: Any) -> int:
            return int(value) if isinstance(value, int | float) else 0

        breakdown = raw.get("cache_creation") or {}
        return cls(
            input_tokens=count(raw.get("input_tokens")),
            output_tokens=count(raw.get("output_tokens")),
            cache_creation_input_tokens=count(raw.get("cache_creation_input_tokens")),
            cache_read_input_tokens=count(raw.get("cache_read_input_tokens")),
            cache_write_5m_tokens=(
                count(breakdown["ephemeral_5m_input_tokens"])
                if "ephemeral_5m_input_tokens" in breakdown
                else None
            ),
            cache_write_1h_tokens=(
                count(breakdown["ephemeral_1h_input_tokens"])
                if "ephemeral_1h_input_tokens" in breakdown
                else None
            ),
        )

    @property
    def prompt_tokens(self) -> int:
        """Everything the model read: fresh input plus cache writes and reads (context size)."""
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens


@lru_cache
def load_pricing(path: Path = CONFIG_DIR / "pricing.yaml") -> PricingTable:
    return PricingTable.model_validate(yaml.safe_load(path.read_text()))


def cost_usd(table: PricingTable, model: str, usage: Usage) -> float:
    pricing = table.pricing_for(model)
    rates: Rates = pricing
    long_context = pricing.long_context
    if long_context is not None and usage.prompt_tokens > long_context.threshold_tokens:
        rates = long_context
    if usage.cache_write_5m_tokens is None and usage.cache_write_1h_tokens is None:
        write_5m, write_1h = usage.cache_creation_input_tokens, 0
    else:
        write_5m, write_1h = usage.cache_write_5m_tokens or 0, usage.cache_write_1h_tokens or 0
    multiplier = table.cache_write_multiplier
    per_mtok = (
        usage.input_tokens * rates.input
        + usage.output_tokens * rates.output
        + usage.cache_read_input_tokens * rates.cache_read
        + write_5m * rates.input * multiplier["ephemeral_5m"]
        + write_1h * rates.input * multiplier["ephemeral_1h"]
    )
    return per_mtok / 1_000_000


def context_window(table: PricingTable, model: str) -> int:
    return table.pricing_for(model).context_window


def estimate_reservation(
    table: PricingTable, model: str, body_bytes: int, output_tokens: int = RESERVE_OUTPUT_TOKENS
) -> float:
    """Upper-ish bound for one request, reserved before forwarding (about 4 bytes per token)."""
    pricing = table.pricing_for(model)
    return (body_bytes / 4 * pricing.input + output_tokens * pricing.output) / 1_000_000
