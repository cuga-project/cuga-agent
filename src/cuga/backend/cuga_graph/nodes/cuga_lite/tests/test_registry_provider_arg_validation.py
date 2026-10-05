import pytest
from unittest.mock import AsyncMock, patch

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker


def _place_order_tool():
    return create_tool_from_api_dict(
        tool_name="place_order",
        tool_def={
            "description": "place an order",
            "parameters": {
                "properties": {
                    "product_id": {"type": "integer"},
                    "quantity": {"type": "integer"},
                },
                "required": ["product_id", "quantity"],
            },
        },
        app_name="shop",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_arguments_return_structured_error_and_are_tracked():
    tool = _place_order_tool()

    ToolCallTracker.start_tracking(enabled=True)
    try:
        with patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry.call_api",
            new_callable=AsyncMock,
        ) as mock_call:
            result = await tool.coroutine({"product_id": 1, "quantity": 2, "currency": "USD"})

        mock_call.assert_not_awaited()
        assert result == {"error": "Unexpected argument(s) for place_order: currency"}
        calls = ToolCallTracker.get_current_calls()
        assert len(calls) == 1
        assert calls[0]["name"] == "place_order"
        assert calls[0]["error"] == "Unexpected argument(s) for place_order: currency"
        assert calls[0]["arguments"]["currency"] == "USD"
    finally:
        ToolCallTracker.stop_tracking()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_types_return_structured_error_on_sandbox_path():
    tool = _place_order_tool()

    ToolCallTracker.start_tracking(enabled=True)
    try:
        with patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry.call_api",
            new_callable=AsyncMock,
        ) as mock_call:
            result = await tool.coroutine({"product_id": 1, "quantity": "two"})

        mock_call.assert_not_awaited()
        assert isinstance(result, dict)
        assert "error" in result
        assert "quantity" in result["error"]
        assert "Invalid arguments for place_order:" in result["error"]
        assert 'quantity expected integer, received string "two"' in result["error"]
        calls = ToolCallTracker.get_current_calls()
        assert len(calls) == 1
        assert calls[0]["name"] == "place_order"
        assert "quantity" in calls[0]["error"]
    finally:
        ToolCallTracker.stop_tracking()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_required_fields_return_structured_error_on_sandbox_path():
    tool = _place_order_tool()

    ToolCallTracker.start_tracking(enabled=True)
    try:
        with patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry.call_api",
            new_callable=AsyncMock,
        ) as mock_call:
            result = await tool.coroutine({"product_id": 1})

        mock_call.assert_not_awaited()
        assert isinstance(result, dict)
        assert "error" in result
        assert "quantity" in result["error"]
        calls = ToolCallTracker.get_current_calls()
        assert len(calls) == 1
        assert "quantity" in calls[0]["error"]
    finally:
        ToolCallTracker.stop_tracking()


@pytest.mark.unit
def test_structured_tool_has_diagnostic_handle_validation_error():
    tool = _place_order_tool()
    assert callable(tool.handle_validation_error)
    message = tool.handle_validation_error(ValueError("quantity: Input should be a valid integer"))
    assert message.startswith("Tool input validation error for place_order:")
    assert "quantity" in message


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("param_schema", "value"),
    [
        ({"anyOf": [{"type": "string"}, {"type": "integer"}]}, 123),
        ({"type": ["string", "integer"]}, 7),
        ({"$ref": "#/components/schemas/Payload"}, {"a": 1}),
        ({}, [1, 2]),
    ],
)
async def test_ambiguous_schema_params_do_not_false_reject(param_schema, value):
    tool = create_tool_from_api_dict(
        tool_name="flex_tool",
        tool_def={
            "description": "accepts loosely typed params",
            "parameters": {
                "properties": {"payload": param_schema},
                "required": ["payload"],
            },
        },
        app_name="shop",
    )

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry.call_api",
        new_callable=AsyncMock,
        return_value={"ok": 1},
    ) as mock_call:
        result = await tool.coroutine({"payload": value})

    mock_call.assert_awaited_once()
    assert result == {"ok": 1}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_toolguard_rejects_unknown_args_before_ainvoke():
    from unittest.mock import MagicMock

    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider

    schema = _place_order_tool().args_schema
    inner = MagicMock()
    inner.name = "place_order"
    inner.description = "place an order"
    inner.args_schema = schema
    inner.ainvoke = AsyncMock(return_value={"ok": 1})
    inner.func = None
    inner.coroutine = None
    inner._operation_id = None

    provider = ToolGuardingToolProvider.__new__(ToolGuardingToolProvider)
    provider._guarded_tools_cache = {}
    provider._runtime = None
    provider._runtime_initialized = True
    provider._get_or_create_toolguard_runtime = AsyncMock(return_value=None)

    wrapped = provider._wrap_tool(inner, "shop")
    ToolCallTracker.start_tracking(enabled=True)
    try:
        result = await wrapped.coroutine({"product_id": 1, "quantity": 2, "currency": "USD"})

        inner.ainvoke.assert_not_awaited()
        assert result == {"error": "Unexpected argument(s) for place_order: currency"}
        calls = ToolCallTracker.get_current_calls()
        assert len(calls) == 1
        assert calls[0]["error"] == "Unexpected argument(s) for place_order: currency"
        assert calls[0]["arguments"]["currency"] == "USD"
    finally:
        ToolCallTracker.stop_tracking()


def _guarded(tool):
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider

    provider = ToolGuardingToolProvider.__new__(ToolGuardingToolProvider)
    provider._guarded_tools_cache = {}
    provider._runtime = None
    provider._runtime_initialized = True
    provider._get_or_create_toolguard_runtime = AsyncMock(return_value=None)
    return provider._wrap_tool(tool, "knowledge")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_toolguard_forwards_runtime_kwargs_to_a_function_that_takes_them():
    """Knowledge tools take ``**_`` so prepare_node can inject ``thread_id``; rejecting it
    as unknown made every SDK knowledge search return an error."""
    from langchain_core.tools import StructuredTool

    seen = {}

    async def search(query: str, scope: str = "agent", **_) -> dict:  # as KnowledgeClient's tools
        seen.update(query=query, scope=scope, **_)
        return {"results": [query]}

    wrapped = _guarded(
        StructuredTool.from_function(
            coroutine=search, name="knowledge_search_knowledge", description="search"
        )
    )
    result = await wrapped.coroutine(query="q", scope="agent", thread_id="t1")
    assert result == {"results": ["q"]}
    assert seen == {"query": "q", "scope": "agent", "thread_id": "t1"}

    # Without extras the normal validated path still runs.
    seen.clear()
    assert await wrapped.coroutine(query="q2") == {"results": ["q2"]}
    assert seen == {"query": "q2", "scope": "agent"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_toolguard_still_rejects_unknown_args_for_a_function_without_kwargs():
    from langchain_core.tools import StructuredTool

    async def search(query: str) -> dict:
        return {"results": [query]}

    wrapped = _guarded(
        StructuredTool.from_function(coroutine=search, name="strict_search", description="search")
    )
    assert await wrapped.coroutine(query="q", thread_id="t1") == {
        "error": "Unexpected argument(s) for strict_search: thread_id"
    }
