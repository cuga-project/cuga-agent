"""Unit tests for the static model price table."""

from cuga.backend.llm.pricing import estimate_cost, find_model_price, normalize_model_name


def test_normalize_strips_provider_prefixes():
    assert normalize_model_name("openai/gpt-4.1") == "gpt-4.1"
    assert normalize_model_name("rits/openai/gpt-oss-120b") == "gpt-oss-120b"
    assert normalize_model_name("GPT-4o") == "gpt-4o"


def test_specific_variant_wins_over_base_model():
    assert find_model_price("gpt-4o-mini") == (0.15, 0.60)
    assert find_model_price("gpt-4o") == (2.50, 10.00)
    assert find_model_price("openai/gpt-4.1-mini-2025") == (0.40, 1.60)


def test_prefixed_and_dated_names_match():
    assert find_model_price("azure/gpt-4o-2024-11-20") == (2.50, 10.00)
    assert find_model_price("MiniMax-M3") == (0.30, 1.20)


def test_unknown_model_returns_none():
    assert find_model_price("google/gemma-4-31B-it") is None
    assert estimate_cost("some-self-hosted-model", 1000, 1000) is None
    assert estimate_cost("", 1000, 1000) is None


def test_estimate_cost_math():
    # 1M input + 1M output of gpt-4.1 = $2.00 + $8.00
    assert estimate_cost("openai/gpt-4.1", 1_000_000, 1_000_000) == 10.00
    assert estimate_cost("gpt-4o-mini", 100_000, 10_000) == (100_000 * 0.15 + 10_000 * 0.60) / 1_000_000


def test_estimate_cost_never_raises_on_bad_input():
    assert estimate_cost(None, 10, 10) is None  # type: ignore[arg-type]
