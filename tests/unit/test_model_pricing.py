"""Unit tests for model price lookup (litellm primary, static fallback)."""

from unittest.mock import patch

from cuga.backend.llm import pricing
from cuga.backend.llm.pricing import estimate_cost, find_model_price, normalize_model_name


def test_normalize_strips_provider_prefixes():
    assert normalize_model_name("openai/gpt-4.1") == "gpt-4.1"
    assert normalize_model_name("azure/openai/gpt-oss-120b") == "gpt-oss-120b"
    assert normalize_model_name("GPT-4o") == "gpt-4o"


def test_specific_variant_wins_over_base_model():
    mini = find_model_price("gpt-4o-mini")
    base = find_model_price("gpt-4o")
    assert (mini["input"], mini["output"]) == (0.15, 0.60)
    assert (base["input"], base["output"]) == (2.50, 10.00)


def test_static_fallback_for_bare_open_weight_names():
    # bare gpt-oss names are not litellm keys (they need a provider prefix),
    # so the static fallback answers — with litellm-aligned prices
    oss_120b = find_model_price("openai/gpt-oss-120b")
    assert (oss_120b["input"], oss_120b["output"]) == (0.15, 0.60)
    assert find_model_price("gpt-oss-20b")["input"] == 0.07
    assert find_model_price("MiniMax-M3")["output"] == 1.20


def test_rits_models_always_resolve_to_none():
    # self-hosted RITS serving is never billed at notional market rate
    assert find_model_price("rits/openai/gpt-oss-120b") is None
    assert estimate_cost("rits/openai/gpt-4.1", 1000, 1000) is None


def test_unknown_model_returns_none():
    assert find_model_price("google/gemma-4-31B-it") is None
    assert estimate_cost("some-self-hosted-model", 1000, 1000) is None
    assert estimate_cost("", 1000, 1000) is None


def test_estimate_cost_math():
    # 1M input + 1M output of gpt-4.1 = $2.00 + $8.00
    assert estimate_cost("gpt-4.1", 1_000_000, 1_000_000) == 10.00
    assert estimate_cost("gpt-4o-mini", 100_000, 10_000) == (100_000 * 0.15 + 10_000 * 0.60) / 1_000_000


def test_litellm_is_primary_source_when_it_knows_the_model():
    fake_map = {
        "my-model": {
            "input_cost_per_token": 1e-6,
            "output_cost_per_token": 2e-6,
            "cache_read_input_token_cost": 0.25e-6,
        }
    }
    with patch.object(pricing, "_LITELLM_UNAVAILABLE", False), patch("litellm.model_cost", fake_map):
        prices = find_model_price("my-model")
        assert prices == {"input": 1.0, "output": 2.0, "cache_read": 0.25}
        # cache-read tokens billed at the cache-read rate
        cost = estimate_cost("my-model", 1_000_000, 0, cache_read_tokens=400_000)
        assert cost == (600_000 * 1.0 + 400_000 * 0.25) / 1_000_000


def test_cache_read_tokens_clamped_and_neutral_without_cache_price():
    # static-fallback models have no cache-read price: cached tokens bill at
    # the input rate, so cost is unchanged by the cache split (and the count
    # is clamped to input_tokens)
    base = estimate_cost("gpt-oss-20b", 100_000, 1_000)
    cached = estimate_cost("gpt-oss-20b", 100_000, 1_000, cache_read_tokens=90_000)
    over = estimate_cost("gpt-oss-20b", 100_000, 1_000, cache_read_tokens=999_999)
    assert base == cached == over


def test_estimate_cost_never_raises_on_bad_input():
    assert estimate_cost(None, 10, 10) is None  # type: ignore[arg-type]
