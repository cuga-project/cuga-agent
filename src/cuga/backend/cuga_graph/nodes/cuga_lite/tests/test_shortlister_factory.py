"""Strategy resolution: built-ins, dotted paths, precedence, caching, fallback."""

from types import SimpleNamespace
from typing import Any, ClassVar, List

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
    Shortlister,
    ShortlistRequest,
    ShortlistCandidate,
    ShortlisterPlan,
    ShortlisterRouter,
    ShortlisterUnavailableError,
    clear_instance_cache,
    resolve_shortlister,
    run_shortlister,
    shortlister_to_configurable,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.embedding import EmbeddingShortlister
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.hybrid import HybridShortlister
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm import LLMShortlister

pytestmark = pytest.mark.unit


class CustomShortlister:
    """Stand-in for a user-supplied strategy loaded by dotted path."""

    name: ClassVar[str] = "custom"

    def __init__(self, plan: Any = None):
        self.plan = plan

    async def shortlist(self, request: ShortlistRequest) -> List[ShortlistCandidate]:
        return [ShortlistCandidate(name="custom_pick", score=1.0)]


class AlwaysUnavailable:
    name: ClassVar[str] = "unavailable"

    async def shortlist(self, request: ShortlistRequest) -> List[ShortlistCandidate]:
        raise ShortlisterUnavailableError("model missing")


@pytest.fixture(autouse=True)
def _clear():
    clear_instance_cache()
    yield
    clear_instance_cache()


def _settings(**kwargs):
    return SimpleNamespace(shortlister=SimpleNamespace(**kwargs))


# --- built-ins --------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [("llm", LLMShortlister), ("embedding", EmbeddingShortlister), ("hybrid", HybridShortlister)],
)
def test_builtin_strategies_resolve(name, expected):
    assert isinstance(resolve_shortlister(ShortlisterPlan(strategy=name)), expected)


def test_unknown_bare_name_is_rewritten_by_the_router():
    """A typo must not take the agent down — it degrades with a note."""
    plan = ShortlisterRouter.resolve(_settings(strategy="cosinee"))
    assert plan.strategy == "llm"
    assert any("cosinee" in n for n in plan.notes)


def test_unknown_name_on_a_hand_built_plan_raises():
    """Bypassing the router is a programming error, so it is loud."""
    with pytest.raises(ValueError, match="Unknown shortlister strategy"):
        resolve_shortlister(ShortlisterPlan(strategy="nonsense"))


# --- dotted paths -----------------------------------------------------------


def test_dotted_path_loads_a_custom_strategy():
    path = f"{__name__}.CustomShortlister"
    strategy = resolve_shortlister(ShortlisterPlan(strategy=path))
    assert isinstance(strategy, CustomShortlister)


def test_dotted_path_receives_the_plan():
    plan = ShortlisterPlan(strategy=f"{__name__}.CustomShortlister", top_k=7)
    assert resolve_shortlister(plan).plan.top_k == 7


def test_broken_dotted_path_falls_back_rather_than_crashing():
    plan = ShortlisterPlan(strategy="does.not.Exist", fallback_strategy="llm")
    assert isinstance(resolve_shortlister(plan), LLMShortlister)


# --- precedence -------------------------------------------------------------


def test_configurable_beats_settings():
    plan = ShortlisterRouter.resolve(
        _settings(strategy="llm", top_k=99),
        configurable={"shortlister_strategy": "embedding", "shortlister_top_k": 12},
    )
    assert plan.strategy == "embedding"
    assert plan.top_k == 12


def test_per_seam_section_beats_global():
    settings = SimpleNamespace(
        shortlister=SimpleNamespace(
            strategy="llm",
            bind_cap=SimpleNamespace(strategy="embedding"),
            discovery=SimpleNamespace(strategy="hybrid"),
        )
    )
    assert ShortlisterRouter.resolve(settings, seam="bind_cap").strategy == "embedding"
    assert ShortlisterRouter.resolve(settings, seam="discovery").strategy == "hybrid"


def test_override_beats_configurable():
    plan = ShortlisterRouter.resolve(
        _settings(strategy="llm"),
        configurable={"shortlister_strategy": "embedding"},
        override=Shortlister(strategy="hybrid"),
    )
    assert plan.strategy == "hybrid"


def test_injected_instance_wins_over_named_strategy():
    custom = CustomShortlister()
    plan = ShortlisterRouter.resolve(_settings(strategy="embedding"), override=Shortlister(instance=custom))
    assert resolve_shortlister(plan) is custom


def test_env_style_string_values_are_coerced():
    """Env vars arrive as strings; ints/floats/bools must still land correctly."""
    plan = ShortlisterRouter.resolve(_settings(top_k="64", min_score="0.42", threshold="256"))
    assert plan.top_k == 64
    assert plan.min_score == pytest.approx(0.42)
    assert plan.threshold == 256


def test_garbage_value_falls_through_to_the_default():
    plan = ShortlisterRouter.resolve(_settings(top_k="not-a-number"))
    assert plan.top_k == 128


# --- caching ----------------------------------------------------------------


def test_instances_are_cached_so_the_model_loads_once():
    a = resolve_shortlister(ShortlisterPlan(strategy="embedding"))
    b = resolve_shortlister(ShortlisterPlan(strategy="embedding"))
    assert a is b


def test_different_embedding_models_get_different_instances():
    a = resolve_shortlister(ShortlisterPlan(strategy="embedding", embedding_model="model-a"))
    b = resolve_shortlister(ShortlisterPlan(strategy="embedding", embedding_model="model-b"))
    assert a is not b


def test_per_call_knobs_do_not_fragment_the_cache():
    """top_k / max_results travel per request, so they must not split the cache."""
    a = resolve_shortlister(ShortlisterPlan(strategy="embedding", top_k=10))
    b = resolve_shortlister(ShortlisterPlan(strategy="embedding", top_k=99))
    assert a is b


def test_constructor_fields_do_fragment_the_cache():
    """Regression: `min_score` is a constructor argument of EmbeddingShortlister.

    It was missing from `cache_key`, so two plans differing only in `min_score`
    shared one instance and the second silently kept the first one's floor —
    a per-invoke `shortlister_min_score` override did nothing after the first
    resolution. Every constructor field must be part of the key.
    """
    strict = resolve_shortlister(ShortlisterPlan(strategy="embedding", min_score=0.9))
    loose = resolve_shortlister(ShortlisterPlan(strategy="embedding", min_score=0.1))
    assert strict is not loose
    assert strict._min_score == pytest.approx(0.9)
    assert loose._min_score == pytest.approx(0.1)


def test_query_weight_also_fragments_the_cache():
    a = resolve_shortlister(ShortlisterPlan(strategy="embedding", query_weight=0.9))
    b = resolve_shortlister(ShortlisterPlan(strategy="embedding", query_weight=0.2))
    assert a is not b


# --- fallback ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_unavailable_strategy_degrades_to_fallback():
    plan = ShortlisterPlan(strategy="embedding", fallback_strategy="llm", instance=AlwaysUnavailable())
    called = {}

    async def _fake(self, request):
        called["yes"] = True
        return [ShortlistCandidate(name="from_llm")]

    from unittest.mock import patch

    with patch.object(LLMShortlister, "shortlist", _fake):
        result = await run_shortlister(plan, ShortlistRequest(query="q", tools=[], apps=[]))

    assert called
    assert result[0].name == "from_llm"


@pytest.mark.asyncio
async def test_unavailable_with_no_distinct_fallback_reraises():
    plan = ShortlisterPlan(strategy="llm", fallback_strategy="llm", instance=AlwaysUnavailable())
    with pytest.raises(ShortlisterUnavailableError):
        await run_shortlister(plan, ShortlistRequest(query="q", tools=[], apps=[]))


@pytest.mark.asyncio
async def test_ordinary_errors_are_not_swallowed():
    """Only unavailability triggers the fallback; real failures keep each call
    site's own error contract."""

    class Boom:
        name = "boom"

        async def shortlist(self, request):
            raise RuntimeError("ranking blew up")

    plan = ShortlisterPlan(strategy="embedding", instance=Boom())
    with pytest.raises(RuntimeError, match="ranking blew up"):
        await run_shortlister(plan, ShortlistRequest(query="q", tools=[], apps=[]))


# --- SDK serialization ------------------------------------------------------


def test_shortlister_to_configurable_is_empty_when_unset():
    assert shortlister_to_configurable(None) == {}
    assert shortlister_to_configurable(Shortlister()) == {}


def test_shortlister_to_configurable_emits_only_set_fields():
    cfg = shortlister_to_configurable(Shortlister(strategy="hybrid", top_k=32))
    assert cfg == {"shortlister_strategy": "hybrid", "shortlister_top_k": 32}


def test_shortlister_to_configurable_passes_an_instance_through():
    custom = CustomShortlister()
    cfg = shortlister_to_configurable(Shortlister(instance=custom))
    assert cfg["shortlister_instance"] is custom


class ExplodingShortlister:
    """Accepts `plan=` but fails during its own initialization."""

    name = "exploding"

    def __init__(self, plan=None):
        raise TypeError("something broke inside __init__")

    async def shortlist(self, request):  # pragma: no cover
        return []


def test_constructor_failure_is_not_mistaken_for_a_signature_mismatch():
    """Regression: `except TypeError` around `cls(plan=plan)` could not tell
    "does not accept plan" from "raised TypeError while initializing", so a
    broken strategy was silently retried as `cls()` — running unconfigured or
    masking the real error. The signature is inspected instead."""
    plan = ShortlisterPlan(strategy=f"{__name__}.ExplodingShortlister", fallback_strategy="llm")
    # The failure is reported (here: swallowed into the documented fallback),
    # never turned into a no-arg construction of the same class.
    strategy = resolve_shortlister(plan)
    assert not isinstance(strategy, ExplodingShortlister)
    assert isinstance(strategy, LLMShortlister)
