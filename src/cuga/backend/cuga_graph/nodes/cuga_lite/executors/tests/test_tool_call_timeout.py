"""
Tests for tool call timeout functionality.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager
from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import call_api


@pytest.fixture
def mock_state():
    """Create a mock AgentState with VariablesManager."""
    state = MagicMock(spec=AgentState)
    state.variables_manager = VariablesManager()
    return state


@pytest.mark.asyncio
async def test_code_execution_timeout(mock_state, monkeypatch):
    """Test that code execution itself times out correctly.

    Uses a small `advanced_features.sandbox_execution_timeout` (2s) via the
    configurable setting so the test runs in ~3s rather than waiting on the
    production default. Sleeps slightly longer than that.
    """
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "sandbox_execution_timeout", 2)

    code = """
import asyncio
await asyncio.sleep(3)  # Longer than the test's 2-second sandbox timeout
print("This should not print")
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    # Should contain timeout error message
    assert "timeout" in result.lower() or "timed out" in result.lower()


@pytest.mark.asyncio
async def test_tool_call_timeout_with_default_timeout(mock_state):
    """Test that tool calls work correctly within the default timeout."""

    # Create a fast async tool
    async def fast_tool(value: int) -> int:
        """A tool that completes quickly."""
        await asyncio.sleep(0.1)
        return value * 2

    code = """
result = await fast_tool(5)
print(result)
"""
    _locals = {
        "fast_tool": fast_tool,
    }

    # Execute code - should complete successfully
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=_locals,
        state=mock_state,
        mode='local',
    )

    # Should contain the result
    assert "10" in result
    assert 'result' in new_vars
    assert new_vars['result'] == 10


@pytest.mark.asyncio
async def test_call_api_timeout():
    """Test that call_api function respects timeout configuration."""
    from cuga.config import settings

    # Mock the HTTP call to simulate a timeout
    with patch(
        'cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry.aiohttp.ClientSession'
    ) as mock_session_class:
        # Create a mock post context manager that raises TimeoutError
        async def timeout_post_context(*args, **kwargs):
            # Simulate timeout by raising asyncio.TimeoutError
            raise asyncio.TimeoutError("Request timed out")

        # Create a mock post method that returns an async context manager
        mock_post_context = AsyncMock()
        mock_post_context.__aenter__ = AsyncMock(side_effect=timeout_post_context)
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        # Create a mock session that is an async context manager
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_post_context)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        # Make ClientSession() return the mock session
        mock_session_class.return_value = mock_session

        # Set a short timeout
        original_timeout = getattr(settings.advanced_features, 'tool_call_timeout', 30)
        settings.advanced_features.tool_call_timeout = 1

        try:
            # This should timeout and raise TimeoutError
            with pytest.raises(TimeoutError) as exc_info:
                await call_api("test_app", "test_api", {"arg": "value"})

            assert "timed out" in str(exc_info.value).lower()
        finally:
            # Restore original timeout
            settings.advanced_features.tool_call_timeout = original_timeout
