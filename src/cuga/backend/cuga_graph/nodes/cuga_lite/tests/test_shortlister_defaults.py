"""Defaults and the `threshold` contract.

The load-bearing guarantee of this feature: with the default settings, or at or
below `threshold` candidates, shortlisting behaves exactly as it did before the
strategy seam existed — same prompt, same LLM call, no embedding work.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
    ShortlisterRouter,
    clear_instance_cache,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.plan import (
    DEFAULT_MAX_RESULTS,
    DEFAULT_THRESHOLD,
    DEFAULT_TOP_K,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_instance_cache()
    yield
    clear_instance_cache()


def _tool(name: str) -> StructuredTool:
    def fn(**kwargs):
        return name

    fn.__name__ = name
    return StructuredTool.from_function(func=fn, name=name, description=f"{name} description")


def _tools(count: int):
    return [_tool(f"tool_{i}") for i in range(count)]


def _settings(**shortlister_overrides):
    """Minimal settings stand-in exposing only what the router reads."""
    return SimpleNamespace(shortlister=SimpleNamespace(**shortlister_overrides))


# --- defaults ---------------------------------------------------------------


def test_defaults_are_llm_and_128():
    """Shipping defaults: LLM strategy, K=128, threshold=128, max_results=10."""
    plan = ShortlisterRouter.resolve(SimpleNamespace())
    assert plan.strategy == "llm"
    assert plan.threshold == DEFAULT_THRESHOLD == 128
    assert plan.top_k == DEFAULT_TOP_K == 128
    assert plan.max_results == DEFAULT_MAX_RESULTS == 10
    assert plan.is_llm_only is True


def test_real_settings_defaults_match_plan_defaults():
    """settings.toml and plan.py must not drift apart."""
    from cuga.config import settings

    plan = ShortlisterRouter.resolve(settings)
    assert plan.strategy == "llm"
    assert plan.threshold == 128
    assert plan.top_k == 128
    assert plan.max_results == 10


def test_missing_shortlister_section_is_safe():
    """A settings object with no [shortlister] must not raise."""
    plan = ShortlisterRouter.resolve(SimpleNamespace())
    assert plan.strategy == "llm"


# --- the threshold contract -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_count,expect_cosine",
    [(128, False), (129, True)],
    ids=["at-threshold-unchanged", "above-threshold-engages"],
)
async def test_threshold_gates_the_cosine_stage(tool_count, expect_cosine):
    """At or below `threshold`, the cosine strategy must not run at all."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    tools = _tools(tool_count)
    llm_result = AsyncMock(return_value=_empty_result())
    embed_result = AsyncMock(return_value=_empty_result())

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm.LLMShortlister.shortlist",
            llm_result,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.embedding.EmbeddingShortlister.shortlist",
            embed_result,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils.settings",
            _settings(strategy="embedding", threshold=128),
        ),
    ):
        await PromptUtils.find_tools(query="q", all_tools=tools, all_apps=[])

    assert embed_result.await_count == (1 if expect_cosine else 0)
    assert llm_result.await_count == (0 if expect_cosine else 1)


@pytest.mark.asyncio
async def test_threshold_zero_always_engages():
    """`threshold = 0` opts out of the gate entirely."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    embed_result = AsyncMock(return_value=_empty_result())
    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.embedding.EmbeddingShortlister.shortlist",
            embed_result,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils.settings",
            _settings(strategy="embedding", threshold=0),
        ),
    ):
        await PromptUtils.find_tools(query="q", all_tools=_tools(2), all_apps=[])

    embed_result.assert_awaited_once()


# --- K handling -------------------------------------------------------------


def test_top_k_negative_clamps_to_zero():
    plan = ShortlisterRouter.resolve(_settings(top_k=-5))
    assert plan.top_k == 0


def test_max_results_non_positive_falls_back_to_default():
    plan = ShortlisterRouter.resolve(_settings(max_results=0))
    assert plan.max_results == DEFAULT_MAX_RESULTS


@pytest.mark.asyncio
async def test_bind_cap_k_never_exceeds_caller_cap():
    """A configured top_k may lower the bind cap, never raise it past the provider limit."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    tools = _tools(200)
    captured = {}

    async def _capture(self, request):
        captured["top_k"] = request.top_k
        return _empty_result()

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm.LLMShortlister.shortlist",
            _capture,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils.settings",
            _settings(top_k=9999, threshold=0),
        ),
    ):
        await PromptUtils.shortlist_tool_names(query="q", all_tools=tools, all_apps=[], top_k=16)

    assert captured["top_k"] == 16, "caller cap must win over a larger configured top_k"


@pytest.mark.asyncio
async def test_configured_top_k_can_lower_bind_cap():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    captured = {}

    async def _capture(self, request):
        captured["top_k"] = request.top_k
        return _empty_result()

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.embedding.EmbeddingShortlister.shortlist",
            _capture,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils.settings",
            _settings(strategy="embedding", top_k=4, threshold=0),
        ),
    ):
        await PromptUtils.shortlist_tool_names(query="q", all_tools=_tools(50), all_apps=[], top_k=32)

    assert captured["top_k"] == 4


# --- distinct from the older threshold --------------------------------------


def test_shortlister_threshold_is_not_shortlisting_tool_threshold():
    """Two different settings, two different meanings — easy to confuse.

    `advanced_features.shortlisting_tool_threshold` (35) decides when tools hide
    behind find_tools in the prompt. `shortlister.threshold` (128) decides when
    the cosine stage engages inside the shortlister.
    """
    from cuga.config import settings

    assert settings.advanced_features.shortlisting_tool_threshold == 35
    assert ShortlisterRouter.resolve(settings).threshold == 128


def _empty_result():
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import ShortlistResult

    return ShortlistResult()


@pytest.mark.asyncio
async def test_max_results_caps_every_strategy_not_just_embedding():
    """Regression: `max_results` used to be honoured only inside EmbeddingShortlister.

    `hybrid` ends with an LLM pick, so the count was whatever the model returned —
    which defeats the cap that exists to stop find_tools output overflowing
    `execution_output_max_length` and being silently truncated mid-render.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import ShortlistCandidate

    tools = _tools(200)

    async def _return_everything(self, request):
        from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import ShortlistResult

        return ShortlistResult(
            candidates=[ShortlistCandidate(name=t.name, score=1.0, reasoning="r") for t in request.tools]
        )

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.hybrid.HybridShortlister.shortlist",
            _return_everything,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils.settings",
            _settings(strategy="hybrid", threshold=0, max_results=4),
        ),
    ):
        out = await PromptUtils.find_tools(query="q", all_tools=tools, all_apps=[])

    assert out.startswith("# Found 4 Matching Tool(s)"), out.splitlines()[0]


@pytest.mark.asyncio
async def test_default_llm_path_keeps_no_fixed_result_count():
    """The cap must not leak into the default path — the LLM has always been
    free to return as many tools as it judges relevant."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import ShortlistCandidate

    tools = _tools(20)

    async def _return_everything(self, request):
        from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import ShortlistResult

        return ShortlistResult(
            candidates=[ShortlistCandidate(name=t.name, score=1.0, reasoning="r") for t in request.tools]
        )

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm.LLMShortlister.shortlist",
            _return_everything,
        ),
        patch("cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils.settings", _settings()),
    ):
        out = await PromptUtils.find_tools(query="q", all_tools=tools, all_apps=[])

    assert out.startswith("# Found 20 Matching Tool(s)"), out.splitlines()[0]
