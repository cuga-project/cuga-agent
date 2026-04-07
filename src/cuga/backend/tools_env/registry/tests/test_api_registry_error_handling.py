import pytest
from unittest.mock import AsyncMock

from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
from cuga.backend.tools_env.registry.registry.api_registry import ApiRegistry


@pytest.mark.asyncio
async def test_call_function_returns_error_dict_when_tool_missing():
    manager = MCPManager(config={})
    registry = ApiRegistry(client=manager)

    manager.call_tool = AsyncMock(
        side_effect=Exception("[Tool evolve_save_trajectory not found in any server]")
    )

    result = await registry.call_function(
        app_name="evolve",
        function_name="evolve_save_trajectory",
        arguments={
            "trajectory_data": "[]",
            "task_id": "demo",
        },
    )

    assert isinstance(result, dict)
    assert result["status"] == "exception"
    assert result["status_code"] == 500
    assert result["function_name"] == "evolve_save_trajectory"
    assert "not found in any server" in result["message"]
