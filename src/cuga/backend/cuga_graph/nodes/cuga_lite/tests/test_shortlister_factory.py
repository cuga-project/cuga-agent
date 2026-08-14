"""Strategy resolution: built-ins, dotted paths, precedence, caching, fallback."""

from types import SimpleNamespace
from typing import Any, ClassVar, List

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
    ShortlistRequest,
    ShortlistCandidate,
    ShortlisterPlan,
    ShortlisterRouter,
    ShortlisterUnavailableError,
    clear_instance_cache,
    resolve_shortlister,
    run_shortlister,
)
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


def test_builtin_llm_strategy_resolves():
    assert isinstance(resolve_shortlister(ShortlisterPlan(strategy="llm")), LLMShortlister)


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


def test_broken_dotted_path_falls_back_rather_than_crashing():
    plan = ShortlisterPlan(strategy="does.not.Exist", fallback_strategy="llm")
    assert isinstance(resolve_shortlister(plan), LLMShortlister)


# --- precedence -------------------------------------------------------------


# --- caching ----------------------------------------------------------------


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


def test_configurable_beats_settings():
    plan = ShortlisterRouter.resolve(
        _settings(strategy="llm"), configurable={"shortlister_strategy": f"{__name__}.CustomShortlister"}
    )
    assert plan.strategy == f"{__name__}.CustomShortlister"


def test_per_seam_section_beats_global():
    dotted = f"{__name__}.CustomShortlister"
    settings = SimpleNamespace(
        shortlister=SimpleNamespace(strategy="llm", bind_cap=SimpleNamespace(strategy=dotted))
    )
    assert ShortlisterRouter.resolve(settings, seam="bind_cap").strategy == dotted
    assert ShortlisterRouter.resolve(settings, seam="discovery").strategy == "llm"


def test_injected_instance_wins_over_named_strategy():
    custom = CustomShortlister()
    plan = ShortlisterRouter.resolve(_settings(strategy="llm"), override=custom)
    assert resolve_shortlister(plan) is custom


def test_instances_are_cached():
    assert resolve_shortlister(ShortlisterPlan()) is resolve_shortlister(ShortlisterPlan())
