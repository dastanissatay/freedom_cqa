"""Цены моделей ($ за 1M токенов) для расчёта стоимости одной оценки.

Значения для Claude — прайс Anthropic API на момент хакатона (input / output / cache read).
Для своей модели на GPU задайте PRICE_INPUT_PER_M / PRICE_OUTPUT_PER_M в .env (или 0).
"""
from __future__ import annotations

from ..config import get_settings

PRICES: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01},
    "claude-fable-5-1": {"input": 10.0, "output": 50.0, "cache_read": 0.25},
    "mock": {"input": 0.0, "output": 0.0, "cache_read": 0.0},
}


def price_for(model: str) -> dict[str, float] | None:
    s = get_settings()
    if s.price_input_per_m is not None and s.price_output_per_m is not None:
        return {"input": s.price_input_per_m, "output": s.price_output_per_m, "cache_read": s.price_input_per_m * 0.1}
    return PRICES.get(model)


def cost_usd(model: str, usage: dict[str, int]) -> float | None:
    p = price_for(model)
    if p is None:
        return None
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    total = (
        inp * p["input"]
        + out * p["output"]
        + cache_read * p["cache_read"]
        + cache_write * p["input"] * 1.25
    ) / 1_000_000
    return round(total, 6)
