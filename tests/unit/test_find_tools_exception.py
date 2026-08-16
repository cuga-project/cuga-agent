"""
Tests that find_tools_func handles exceptions from PromptUtils.find_tools
gracefully by returning an error string instead of crashing.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.exceptions import OutputParserException


@pytest.fixture
def mock_tools():
    tool = MagicMock()
    tool.name = "test_tool"
    tool.description = "A test tool"
    return [tool]


@pytest.fixture
def mock_apps():
    app = MagicMock()
    app.name = "test_app"
    return [app]


async def _get_find_tools_func(mock_tools, mock_apps):
    """Create find_tools_tool and extract the inner async function for direct testing."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools import (
        create_find_tools_tool,
    )

    app_to_tools_map = {"test_app": mock_tools}
    tool = await create_find_tools_tool(
        all_tools=mock_tools,
        all_apps=mock_apps,
        app_to_tools_map=app_to_tools_map,
    )
    # The inner coroutine is stored on the tool
    return tool.coroutine or tool.func


@pytest.mark.asyncio
async def test_find_tools_func_returns_error_on_output_parser_exception(mock_tools, mock_apps):
    """When PromptUtils.find_tools raises OutputParserException, return an error string."""
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools.PromptUtils.find_tools",
        new_callable=AsyncMock,
        side_effect=OutputParserException("Invalid json output: "),
    ):
        func = await _get_find_tools_func(mock_tools, mock_apps)
        result = await func(query="find contacts", app_name="test_app")

    assert "malformed response" in result
    assert "Invalid json output" in result
    assert "retry" in result.lower()


@pytest.mark.asyncio
async def test_find_tools_func_returns_error_on_generic_exception(mock_tools, mock_apps):
    """Any exception type should be caught and return a generic internal error string with source error."""
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools.PromptUtils.find_tools",
        new_callable=AsyncMock,
        side_effect=RuntimeError("unexpected LLM failure"),
    ):
        func = await _get_find_tools_func(mock_tools, mock_apps)
        result = await func(query="find contacts", app_name="test_app")

    assert "internal error" in result
    assert "unexpected LLM failure" in result
    assert "retry" in result.lower()


@pytest.mark.asyncio
async def test_find_tools_func_success_passes_through(mock_tools, mock_apps):
    """On success, the result from PromptUtils.find_tools is returned as-is."""
    expected = "## 1. `test_tool`\nSome tool details"

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools.PromptUtils.find_tools",
        new_callable=AsyncMock,
        return_value=expected,
    ):
        func = await _get_find_tools_func(mock_tools, mock_apps)
        result = await func(query="find contacts", app_name="test_app")

    assert result == expected


@pytest.mark.asyncio
async def test_find_tools_forwards_query_and_task_context_separately(mock_tools, mock_apps):
    """The step query and the initial user message reach the shortlister as two values.

    They used to be pre-joined into one string here. They now travel separately so a
    non-LLM strategy can weight them independently (a long task context would otherwise
    dominate a short step query). ``LLMShortlister`` re-joins them — see the companion
    test below, which pins that the composed text is unchanged.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools import create_find_tools_tool

    app_to_tools_map = {"test_app": mock_tools}
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools.PromptUtils.find_tools",
        new_callable=AsyncMock,
        return_value="ok",
    ) as mock_find:
        tool = await create_find_tools_tool(
            all_tools=mock_tools,
            all_apps=mock_apps,
            app_to_tools_map=app_to_tools_map,
            initial_user_message="Book a flight to NYC",
        )
        func = tool.coroutine or tool.func
        await func(query="list calendar tools", app_name="test_app")

    mock_find.assert_awaited_once()
    call_kw = mock_find.await_args.kwargs
    assert call_kw["query"] == "list calendar tools"
    assert call_kw["task_context"] == "Book a flight to NYC"


@pytest.mark.unit
def test_compose_query_matches_legacy_find_tools_format():
    """The text the LLM sees is byte-identical to the pre-split composition.

    Guards the refactor: ``LLMShortlister`` must reproduce exactly what
    ``_compose_find_tools_shortlister_query`` produced, or every shortlister prompt
    silently changes.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools import (
        _compose_find_tools_shortlister_query,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm import compose_query

    cases = [
        ("list calendar tools", "Book a flight to NYC"),
        ("  padded  ", "  context  "),
        ("only a query", None),
        ("only a query", ""),
    ]
    for query, context in cases:
        assert compose_query(query, context) == _compose_find_tools_shortlister_query(query, context)
