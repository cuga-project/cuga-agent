"""Static price estimates for the models shipped in configurations/models/*.toml.

Prices are public list prices in USD per 1M tokens and are estimates by design —
self-hosted deployments (e.g. RITS) have no meaningful public price and simply
resolve to ``None``. To support a new model, add a ``(pattern, input, output)``
entry; patterns are matched as substrings against a normalized model name
(lowercased, provider prefixes such as ``openai/`` or ``rits/openai/`` stripped),
longest pattern first, so put the more specific variant (``gpt-4o-mini``) rather
than relying on ordering.
"""

from typing import Optional, Tuple

# (substring pattern, USD per 1M input tokens, USD per 1M output tokens)
MODEL_PRICES: list[Tuple[str, float, float]] = [
    ("gpt-4.1-mini", 0.40, 1.60),
    ("gpt-4.1", 2.00, 8.00),
    ("gpt-4o-mini", 0.15, 0.60),
    ("gpt-4o", 2.50, 10.00),
    ("gpt-oss-120b", 0.15, 0.75),
    ("gpt-oss-20b", 0.10, 0.50),
    ("minimax-m3", 0.30, 1.20),
]


def normalize_model_name(model_name: str) -> str:
    """Lowercase and strip provider prefixes: ``rits/openai/gpt-4.1`` -> ``gpt-4.1``."""
    return model_name.strip().lower().rsplit("/", 1)[-1]


def find_model_price(model_name: str) -> Optional[Tuple[float, float]]:
    """Return (input, output) USD per 1M tokens for a model, or None if unknown."""
    try:
        normalized = normalize_model_name(model_name)
        if not normalized:
            return None
        for pattern, input_price, output_price in sorted(
            MODEL_PRICES, key=lambda entry: len(entry[0]), reverse=True
        ):
            if pattern in normalized:
                return input_price, output_price
        return None
    except Exception:
        return None


def estimate_cost(model_name: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    """Estimated USD cost for a call, or None when the model has no known price."""
    try:
        prices = find_model_price(model_name)
        if prices is None:
            return None
        input_price, output_price = prices
        return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
    except Exception:
        return None
