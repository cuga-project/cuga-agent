"""Model price lookup for run receipts.

Primary source is litellm's bundled model-cost map (litellm is already a core
dependency; its map is maintained upstream and includes cache-read pricing).
The small static table below is a fallback for models litellm only knows under
provider-prefixed keys (e.g. bare ``gpt-oss-120b``) or not at all. litellm is
tried with the raw model name first, then the normalized one, since bare
open-weight names are not litellm keys but prefixed ones may be.

Self-hosted RITS deployments (``rits/...`` model names) have no meaningful
public price and always resolve to ``None`` — they are never billed at a
notional market rate.

All lookups are fail-safe: unknown models and any lookup error yield ``None``.
"""

from typing import Any, Dict, Optional, Tuple

# Fallback table: (substring pattern, USD per 1M input tokens, USD per 1M output tokens).
# Patterns match as substrings against a normalized model name, longest pattern
# first. Values mirror litellm's map at the time of writing (gpt-oss per
# groq/azure list price).
MODEL_PRICES: list[Tuple[str, float, float]] = [
    ("gpt-4.1-mini", 0.40, 1.60),
    ("gpt-4.1", 2.00, 8.00),
    ("gpt-4o-mini", 0.15, 0.60),
    ("gpt-4o", 2.50, 10.00),
    ("gpt-oss-120b", 0.15, 0.60),
    ("gpt-oss-20b", 0.07, 0.30),
    ("minimax-m3", 0.30, 1.20),
]

_LITELLM_UNAVAILABLE = False


def normalize_model_name(model_name: str) -> str:
    """Lowercase and strip provider prefixes: ``azure/openai/gpt-4.1`` -> ``gpt-4.1``."""
    return model_name.strip().lower().rsplit("/", 1)[-1]


def _litellm_price(model_name: str) -> Optional[Dict[str, Optional[float]]]:
    """Look up litellm's bundled cost map; raw name first, then normalized."""
    global _LITELLM_UNAVAILABLE
    if _LITELLM_UNAVAILABLE:
        return None
    try:
        import litellm

        cost_map = getattr(litellm, "model_cost", None) or {}
        for key in (model_name, model_name.strip().lower(), normalize_model_name(model_name)):
            info: Dict[str, Any] = cost_map.get(key) or {}
            input_cost = info.get("input_cost_per_token")
            output_cost = info.get("output_cost_per_token")
            if input_cost is not None and output_cost is not None:
                cache_read = info.get("cache_read_input_token_cost")
                return {
                    "input": float(input_cost) * 1_000_000,
                    "output": float(output_cost) * 1_000_000,
                    "cache_read": float(cache_read) * 1_000_000 if cache_read is not None else None,
                }
        return None
    except Exception:
        _LITELLM_UNAVAILABLE = True
        return None


def _static_price(model_name: str) -> Optional[Dict[str, Optional[float]]]:
    normalized = normalize_model_name(model_name)
    if not normalized:
        return None
    for pattern, input_price, output_price in sorted(
        MODEL_PRICES, key=lambda entry: len(entry[0]), reverse=True
    ):
        if pattern in normalized:
            return {"input": input_price, "output": output_price, "cache_read": None}
    return None


def find_model_price(model_name: str) -> Optional[Dict[str, Optional[float]]]:
    """USD-per-1M-token prices for a model: ``{"input", "output", "cache_read"}`` or None.

    ``cache_read`` is None when the source has no cache-read price (cached
    input tokens are then billed at the full input rate).
    """
    try:
        if not model_name:
            return None
        if model_name.strip().lower().startswith("rits/"):
            return None  # self-hosted; no meaningful public price
        return _litellm_price(model_name) or _static_price(model_name)
    except Exception:
        return None


def estimate_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> Optional[float]:
    """Estimated USD cost for a call, or None when the model has no known price.

    ``cache_read_tokens`` must be a subset of ``input_tokens``; they are billed
    at the model's cache-read rate when known, else at the full input rate.
    """
    try:
        prices = find_model_price(model_name)
        if prices is None:
            return None
        cache_read_tokens = min(max(int(cache_read_tokens or 0), 0), int(input_tokens))
        cache_price = prices["cache_read"] if prices["cache_read"] is not None else prices["input"]
        cost = (
            (input_tokens - cache_read_tokens) * prices["input"]
            + cache_read_tokens * cache_price
            + output_tokens * prices["output"]
        )
        return cost / 1_000_000
    except Exception:
        return None
