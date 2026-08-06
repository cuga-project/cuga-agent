"""
Tests for recording direct-LangChain tool calls via make_recording_awaitable.

Direct tools (DirectLangChainToolsProvider) have no built-in recorder, unlike
registry/combined provider tools. prepare_node wraps them with
make_recording_awaitable so track_tool_calls=True captures
{tool_name, arguments, result} without hand-decorating every tool.
"""

import asyncio

import pytest

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import make_tool_awaitable
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.arg_warning import make_arg_warning_callable
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import (
    ToolCallTracker,
    make_recording_awaitable,
)


def _wrap_like_prepare_node(tool_func, tool_name):
    """The exact chain prepare_node applies to a direct tool."""
    wrapped = make_arg_warning_callable(tool_func, None, enable=True)
    return make_recording_awaitable(make_tool_awaitable(wrapped), tool_name, app_name="runtime_tools")


async def _get_data(x: int) -> dict:
    return {"value": x * 2}


def _get_data_sync(x: int) -> dict:
    return {"value": x * 3}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_tool_calls_recorded_in_order_with_args_and_results():
    async_tool = _wrap_like_prepare_node(_get_data, "get_data")
    sync_tool = _wrap_like_prepare_node(_get_data_sync, "get_data_sync")

    ToolCallTracker.start_tracking(enabled=True)
    assert await async_tool(x=3) == {"value": 6}
    assert await sync_tool(x=4) == {"value": 12}
    calls = ToolCallTracker.stop_tracking()

    assert [c["name"] for c in calls] == ["get_data", "get_data_sync"]
    assert calls[0]["arguments"] == {"x": 3}
    assert calls[0]["result"] == {"value": 6}
    assert calls[0]["app_name"] == "runtime_tools"
    assert calls[0]["error"] is None
    assert calls[1]["arguments"] == {"x": 4}
    assert calls[1]["result"] == {"value": 12}

    # Positional args are preserved too. Without a schema (no param_names) they
    # fall back to positional keys; prepare_node passes the tool's real parameter
    # names — see test_prepare_node_direct_tool_recording.py.
    ToolCallTracker.start_tracking(enabled=True)
    assert await async_tool(7) == {"value": 14}
    calls = ToolCallTracker.stop_tracking()
    assert calls[0]["arguments"] == {"arg0": 7}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_tool_errors_recorded_and_reraised():
    async def boom(x: int) -> dict:
        raise ValueError("boom")

    tool = _wrap_like_prepare_node(boom, "boom")

    ToolCallTracker.start_tracking(enabled=True)
    with pytest.raises(ValueError, match="boom"):
        await tool(x=1)
    calls = ToolCallTracker.stop_tracking()

    assert len(calls) == 1
    assert calls[0]["name"] == "boom"
    assert calls[0]["error"] == "boom"
    assert calls[0]["result"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_tool_counts_toward_block_tool_call_evidence():
    """Direct tools bypass the registry/combined call paths, so they must
    increment the per-block counter the executor reports as timeout evidence."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import BlockToolCallCounter

    tool = _wrap_like_prepare_node(_get_data, "get_data")
    BlockToolCallCounter.reset()
    assert BlockToolCallCounter.current_count() == 0

    await tool(x=1)
    await tool(x=2)

    assert BlockToolCallCounter.current_count() == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_call_is_not_recorded_as_success():
    """CancelledError is a BaseException, so it skips `except Exception`; without
    an explicit handler the finally block records a cancelled call as a success."""

    async def hangs(x: int) -> dict:
        await asyncio.sleep(60)
        return {"value": x}

    tool = _wrap_like_prepare_node(hangs, "hangs")

    ToolCallTracker.start_tracking(enabled=True)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(tool(x=1), timeout=0.05)
    calls = ToolCallTracker.stop_tracking()

    assert len(calls) == 1
    assert calls[0]["error"], "a cancelled call must carry an error, not look successful"
    assert calls[0]["result"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_tracking_session_leaves_behavior_unchanged():
    tool = _wrap_like_prepare_node(_get_data, "get_data")

    ToolCallTracker.stop_tracking()  # ensure no active session
    assert await tool(x=5) == {"value": 10}
    assert ToolCallTracker.get_current_calls() == []
