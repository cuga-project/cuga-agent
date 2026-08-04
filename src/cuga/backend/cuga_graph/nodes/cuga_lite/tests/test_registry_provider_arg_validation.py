import pytest
from unittest.mock import AsyncMock, patch

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_arguments_return_structured_error_and_are_tracked():
    tool = create_tool_from_api_dict(
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
def test_structured_tool_has_handle_validation_error_enabled():
    tool = create_tool_from_api_dict(
        tool_name="place_order",
        tool_def={
            "description": "place an order",
            "parameters": {
                "properties": {
                    "quantity": {"type": "integer"},
                },
                "required": ["quantity"],
            },
        },
        app_name="shop",
    )
    assert tool.handle_validation_error is True
