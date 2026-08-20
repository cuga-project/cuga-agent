"""End-to-end seam tests with a REAL EmbeddingShortlister.

Every other shortlister test mocks ``shortlist``, which is exactly why two bugs
reached review: a strategy returning the wrong type, and the default LLM path
being capped above the threshold. These tests put a real strategy through
``PromptUtils.find_tools`` / ``shortlist_tool_names`` and mock only the
*embedding backend* — the one piece that would otherwise need a model download.
"""

from unittest.mock import patch

import numpy as np
import pytest
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import clear_instance_cache
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import embedding as embedding_module

pytestmark = pytest.mark.unit

VOCAB = ["contact", "account", "lead", "weather", "flight", "invoice"]
MODEL = "seam-test-model"


class FakeBackend:
    """Bag-of-words embedder standing in for fastembed. Deterministic, offline."""

    async def aembed(self, texts, *, as_query):
        return np.array(
            [np.array([float((t or "").lower().count(w)) for w in VOCAB], dtype=np.float32) for t in texts],
            dtype=np.float32,
        )


@pytest.fixture(autouse=True)
def _clean():
    clear_instance_cache()
    embedding_module.reset_caches()
    embedding_module._MODELS[embedding_module.backend_key("local", MODEL)] = FakeBackend()
    yield
    embedding_module.reset_caches()
    clear_instance_cache()


def _tool(name: str, description: str = "") -> StructuredTool:
    def fn(**kwargs):
        return name

    fn.__name__ = name
    return StructuredTool.from_function(func=fn, name=name, description=description or name)


CRM = [
    _tool("crm_get_contacts", "contact contact"),
    _tool("crm_get_accounts", "account account"),
    _tool("crm_delete_lead", "lead lead"),
]


def _cfg(**kw):
    cfg = {f"shortlister_{k}": v for k, v in kw.items()}
    cfg.setdefault("shortlister_embedding_model", MODEL)
    cfg.setdefault("shortlister_embedding_provider", "local")
    return {"configurable": cfg}


# --- the seams actually consume what the strategy returns -------------------


@pytest.mark.asyncio
async def test_find_tools_consumes_a_real_embedding_strategy():
    """Regression: EmbeddingShortlister returned a bare list while find_tools read
    ``result.candidates``, so a warm model raised AttributeError — swallowed by
    find_tools into an error string, and raised at the bind cap."""
    out = await PromptUtils.find_tools(
        query="find the contact",
        all_tools=CRM,
        all_apps=[],
        run_config=_cfg(strategy="embedding", threshold=0),
    )
    assert out.startswith("# Found "), out.splitlines()[0]
    assert "crm_get_contacts" in out
    assert "Cosine similarity" in out, "the cosine strategy did not produce the ranking"
    assert "Tool shortlisting failed" not in out


@pytest.mark.asyncio
async def test_bind_cap_consumes_a_real_embedding_strategy():
    names = await PromptUtils.shortlist_tool_names(
        query="delete the lead",
        all_tools=CRM,
        all_apps=[],
        top_k=2,
        run_config=_cfg(strategy="embedding", threshold=0),
    )
    assert names, "bind cap returned nothing from a working cosine strategy"
    assert names[0] == "crm_delete_lead"
    assert set(names) <= {t.name for t in CRM}


@pytest.mark.asyncio
async def test_hybrid_prefilter_consumes_the_embedding_result():
    """Regression: hybrid iterated the prefilter directly; once embedding returned
    a ShortlistResult that became `TypeError: not iterable`."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
        ShortlistCandidate,
        ShortlistResult,
        ShortlisterPlan,
        resolve_shortlister,
    )

    seen = {}

    async def _fake_llm(self, request):
        seen["pool"] = [t.name for t in request.tools]
        return ShortlistResult(candidates=[ShortlistCandidate(name=request.tools[0].name)])

    plan = ShortlisterPlan(strategy="hybrid", embedding_model=MODEL, embedding_provider="local")
    hybrid = resolve_shortlister(plan)

    with patch("cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm.LLMShortlister.shortlist", _fake_llm):
        from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import ShortlistRequest

        result = await hybrid.shortlist(ShortlistRequest(query="contact", tools=CRM, apps=[], top_k=2))

    # The property under test is that hybrid *consumes* the prefilter result
    # without raising, and hands the LLM a narrowed pool. The exact count is a
    # function of min_score, which is not what this test pins.
    assert "pool" in seen, "the LLM leg never ran — the prefilter raised"
    assert 0 < len(seen["pool"]) < len(CRM), f"pool not narrowed: {seen['pool']}"
    assert result.candidates


# --- the default LLM path is untouched at every catalogue size --------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_count", [10, 129, 400], ids=["small", "just-over", "large"])
async def test_default_llm_path_is_never_capped(tool_count):
    """Regression: the gate keyed only on catalogue size, so with shipped defaults
    a 129-tool catalogue still used the LLM but injected top_k=128 and truncated
    the render to 10. #624 requires the default path unchanged at every N."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
        ShortlistCandidate,
        ShortlistResult,
    )

    tools = [_tool(f"tool_{i}") for i in range(tool_count)]
    seen = {}

    async def _capture(self, request):
        seen["top_k"] = request.top_k
        seen["max_results"] = request.max_results
        seen["instructions"] = request.instructions
        return ShortlistResult(
            candidates=[ShortlistCandidate(name=t.name, score=1.0, reasoning="r") for t in tools]
        )

    with patch("cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm.LLMShortlister.shortlist", _capture):
        out = await PromptUtils.find_tools(query="q", all_tools=tools, all_apps=[])

    assert seen["top_k"] is None, "default LLM path was given a top_k"
    assert seen["max_results"] is None, "default LLM path was given a render cap"
    assert out.startswith(f"# Found {tool_count} Matching Tool(s)"), out.splitlines()[0]


@pytest.mark.asyncio
async def test_default_bind_cap_keeps_the_callers_top_k():
    """The provider cap is the caller's; config may lower it only when a
    non-default ranker is engaged."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import ShortlistResult

    tools = [_tool(f"tool_{i}") for i in range(300)]
    seen = {}

    async def _capture(self, request):
        seen["top_k"] = request.top_k
        return ShortlistResult()

    with patch("cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm.LLMShortlister.shortlist", _capture):
        await PromptUtils.shortlist_tool_names(query="q", all_tools=tools, all_apps=[], top_k=64)

    assert seen["top_k"] == 64, "default LLM path had its cap rewritten by config"
