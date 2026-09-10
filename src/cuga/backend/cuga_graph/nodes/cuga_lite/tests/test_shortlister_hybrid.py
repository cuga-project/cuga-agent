"""HybridShortlister: cosine prefilters, the LLM decides."""

from typing import Any, List
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
    ShortlistCandidate,
    ShortlistRequest,
    ShortlistResult,
    ShortlisterUnavailableError,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.embedding import EmbeddingShortlister
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.hybrid import HybridShortlister
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm import LLMShortlister

pytestmark = pytest.mark.unit


def _tool(name: str) -> StructuredTool:
    def fn(**kwargs):
        return name

    fn.__name__ = name
    return StructuredTool.from_function(func=fn, name=name, description=name)


def _tools(n: int) -> List[StructuredTool]:
    return [_tool(f"tool_{i}") for i in range(n)]


def _request(tools, **kwargs) -> ShortlistRequest:
    return ShortlistRequest(query="find contacts", tools=tools, apps=[], **kwargs)


class _RecordingLLM(LLMShortlister):
    def __init__(self):
        super().__init__()
        self.seen: List[Any] = []

    async def shortlist(self, request: ShortlistRequest) -> ShortlistResult:
        self.seen.append([t.name for t in request.tools])
        return ShortlistResult(candidates=[ShortlistCandidate(name=request.tools[0].name)])


def _hybrid(embedding=None, llm=None):
    return HybridShortlister(embedding=embedding or EmbeddingShortlister("model"), llm=llm or _RecordingLLM())


@pytest.mark.asyncio
async def test_cosine_narrows_the_pool_before_the_llm_sees_it():
    llm = _RecordingLLM()
    prefiltered = ShortlistResult(candidates=[ShortlistCandidate(name=f"tool_{i}") for i in range(5)])

    with patch.object(EmbeddingShortlister, "shortlist", AsyncMock(return_value=prefiltered)):
        await _hybrid(llm=llm).shortlist(_request(_tools(100), top_k=5))

    assert llm.seen == [[f"tool_{i}" for i in range(5)]]


@pytest.mark.asyncio
async def test_prefilter_is_skipped_when_the_pool_already_fits():
    """Below the cut width the LLM would see the same set either way."""
    llm = _RecordingLLM()
    embed = AsyncMock()

    with patch.object(EmbeddingShortlister, "shortlist", embed):
        await _hybrid(llm=llm).shortlist(_request(_tools(4), top_k=10))

    embed.assert_not_awaited()
    assert llm.seen == [[f"tool_{i}" for i in range(4)]]


@pytest.mark.asyncio
async def test_llm_ordering_wins():
    llm_result = ShortlistResult(
        candidates=[ShortlistCandidate(name="tool_9"), ShortlistCandidate(name="tool_1")]
    )
    prefiltered = ShortlistResult(candidates=[ShortlistCandidate(name=f"tool_{i}") for i in range(10)])

    with (
        patch.object(EmbeddingShortlister, "shortlist", AsyncMock(return_value=prefiltered)),
        patch.object(LLMShortlister, "shortlist", AsyncMock(return_value=llm_result)),
    ):
        # A plain LLMShortlister, so the patch above actually applies.
        result = await _hybrid(llm=LLMShortlister()).shortlist(_request(_tools(50), top_k=10))

    assert [c.name for c in result.candidates] == ["tool_9", "tool_1"]


@pytest.mark.asyncio
async def test_degrades_to_plain_llm_when_embeddings_are_unavailable():
    """Cold start must not break discovery — this path is exactly today's behavior."""
    llm = _RecordingLLM()
    unavailable = AsyncMock(side_effect=ShortlisterUnavailableError("still downloading"))

    with patch.object(EmbeddingShortlister, "shortlist", unavailable):
        result = await _hybrid(llm=llm).shortlist(_request(_tools(50), top_k=10))

    assert llm.seen == [[f"tool_{i}" for i in range(50)]], "LLM must see the full pool"
    assert result


@pytest.mark.asyncio
async def test_empty_tools_short_circuits():
    assert (await _hybrid().shortlist(_request([]))).candidates == []
