"""Validate shortlister/find_tools names against real candidates with retry (#546)."""

from __future__ import annotations

from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cuga.backend.cuga_graph.nodes.cuga_agent_core.schemas.shortlister import (
    APIDetails,
    ShortListerOutputLite,
)


def _detail(name: str, reasoning: str = "relevant") -> APIDetails:
    return APIDetails(name=name, relevance_score=1.0, reasoning=reasoning)


def _tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = f"{name} description"
    tool.args_schema = None
    tool.func = MagicMock()
    return tool


def _chain_with_responses(responses: List[Any]) -> MagicMock:
    chain = MagicMock()
    chain.ainvoke = AsyncMock(side_effect=list(responses))
    return chain


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shortlist_valid_names_no_retry():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    chain = _chain_with_responses([ShortListerOutputLite(result=[_detail("tool_a")])])
    with (
        patch(
            "cuga.backend.cuga_graph.nodes.shared.base_agent.BaseAgent.get_chain",
            return_value=chain,
        ),
        patch("cuga.backend.llm.models.LLMManager"),
    ):
        ranked = await PromptUtils.shortlist_tool_names(
            query="list users",
            all_tools=[_tool("tool_a"), _tool("tool_b")],
            all_apps=[],
            top_k=2,
        )

    assert ranked == ["tool_a"]
    assert chain.ainvoke.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shortlist_retries_then_accepts_valid_names():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    chain = _chain_with_responses(
        [
            ShortListerOutputLite(result=[_detail("hallucinated_tool")]),
            ShortListerOutputLite(result=[_detail("tool_a")]),
        ]
    )
    with (
        patch(
            "cuga.backend.cuga_graph.nodes.shared.base_agent.BaseAgent.get_chain",
            return_value=chain,
        ),
        patch("cuga.backend.llm.models.LLMManager"),
    ):
        ranked = await PromptUtils.shortlist_tool_names(
            query="list users",
            all_tools=[_tool("tool_a"), _tool("tool_b")],
            all_apps=[],
            top_k=2,
        )

    assert ranked == ["tool_a"]
    assert chain.ainvoke.await_count == 2
    retry_instructions = chain.ainvoke.await_args_list[1].args[0]["instructions"]
    assert "hallucinated_tool" in retry_instructions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shortlist_does_not_retry_when_any_valid_name_present():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    chain = _chain_with_responses(
        [
            ShortListerOutputLite(result=[_detail("fake_one"), _detail("tool_a")]),
        ]
    )
    with (
        patch(
            "cuga.backend.cuga_graph.nodes.shared.base_agent.BaseAgent.get_chain",
            return_value=chain,
        ),
        patch("cuga.backend.llm.models.LLMManager"),
    ):
        ranked = await PromptUtils.shortlist_tool_names(
            query="list users",
            all_tools=[_tool("tool_a"), _tool("tool_b")],
            all_apps=[],
            top_k=2,
        )

    assert ranked == ["tool_a"]
    assert chain.ainvoke.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shortlist_keeps_valid_names_when_later_retry_is_worse():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    chain = _chain_with_responses(
        [
            ShortListerOutputLite(result=[_detail("tool_a"), _detail("fake_one")]),
            ShortListerOutputLite(result=[_detail("fake_two")]),
            ShortListerOutputLite(result=[_detail("fake_three")]),
        ]
    )
    with (
        patch(
            "cuga.backend.cuga_graph.nodes.shared.base_agent.BaseAgent.get_chain",
            return_value=chain,
        ),
        patch("cuga.backend.llm.models.LLMManager"),
    ):
        ranked = await PromptUtils.shortlist_tool_names(
            query="list users",
            all_tools=[_tool("tool_a"), _tool("tool_b")],
            all_apps=[],
            top_k=2,
        )

    # First attempt already has a usable name — do not burn retries and lose it.
    assert ranked == ["tool_a"]
    assert chain.ainvoke.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shortlist_filters_all_invalid_after_retries():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    chain = _chain_with_responses(
        [
            ShortListerOutputLite(result=[_detail("fake_one")]),
            ShortListerOutputLite(result=[_detail("fake_two")]),
            ShortListerOutputLite(result=[_detail("fake_three")]),
        ]
    )
    with (
        patch(
            "cuga.backend.cuga_graph.nodes.shared.base_agent.BaseAgent.get_chain",
            return_value=chain,
        ),
        patch("cuga.backend.llm.models.LLMManager"),
    ):
        ranked = await PromptUtils.shortlist_tool_names(
            query="list users",
            all_tools=[_tool("tool_a"), _tool("tool_b")],
            all_apps=[],
            top_k=2,
        )

    assert ranked == []
    assert chain.ainvoke.await_count == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_tools_mentions_filtered_invalid_names_after_retries():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    chain = _chain_with_responses(
        [
            ShortListerOutputLite(result=[_detail("ghost_a"), _detail("ghost_b")]),
            ShortListerOutputLite(result=[_detail("ghost_a"), _detail("tool_a")]),
        ]
    )
    with (
        patch(
            "cuga.backend.cuga_graph.nodes.shared.base_agent.BaseAgent.get_chain",
            return_value=chain,
        ),
        patch("cuga.backend.llm.models.LLMManager"),
    ):
        result = await PromptUtils.find_tools(
            query="find contacts",
            all_tools=[_tool("tool_a")],
            all_apps=[],
        )

    assert "`tool_a`" in result
    assert "ghost_a" in result
    assert "ghost_b" in result
    assert "filtered" in result.lower()
    assert chain.ainvoke.await_count == 2
    retry_instructions = chain.ainvoke.await_args_list[1].args[0]["instructions"]
    assert "ghost_a" in retry_instructions
    assert "ghost_b" in retry_instructions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_tools_all_invalid_includes_filtered_note():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    chain = _chain_with_responses(
        [
            ShortListerOutputLite(result=[_detail("ghost_a")]),
            ShortListerOutputLite(result=[_detail("ghost_b")]),
            ShortListerOutputLite(result=[_detail("ghost_c")]),
        ]
    )
    with (
        patch(
            "cuga.backend.cuga_graph.nodes.shared.base_agent.BaseAgent.get_chain",
            return_value=chain,
        ),
        patch("cuga.backend.llm.models.LLMManager"),
    ):
        result = await PromptUtils.find_tools(
            query="find contacts",
            all_tools=[_tool("tool_a")],
            all_apps=[],
        )

    assert result.startswith("No matching tools found for your query.")
    assert "filtered" in result.lower()
    assert "ghost_a" in result
    assert "ghost_b" in result
    assert "ghost_c" in result
    assert chain.ainvoke.await_count == 3
