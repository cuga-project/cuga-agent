"""ToolGuard records actual policy verdicts and delegates tool observations exactly once."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables.config import var_child_runnable_config
from langchain_core.tools import tool

from cuga.backend.cuga_graph.nodes.cuga_lite.providers import combined, registry
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import (
    ToolCallTracker,
    make_recording_awaitable,
    tracked_tool,
)
from cuga.backend.observability import langfuse_sdk

pytestmark = pytest.mark.unit


@pytest.fixture
def manual_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(langfuse_sdk, "enabled", lambda: True)

    def record(*args, **kwargs):
        if not kwargs["langchain_tool"]:
            calls.append((args, kwargs))

    monkeypatch.setattr(langfuse_sdk, "record_tool_call", record)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["direct", "tracked_direct", "registry", "mcp"])
@pytest.mark.parametrize("outcome", ["allowed", "denied", "cancelled", "error"])
@pytest.mark.parametrize("caller_handler", [False, True, "ignore_agent", "sdk"])
async def test_guard_tracks_one_tool(manual_calls, monkeypatch, route, outcome, caller_handler):
    from langfuse.langchain import CallbackHandler

    denied = outcome == "denied"
    cancelled = outcome == "cancelled"
    failed = outcome == "error"
    side_effect = asyncio.CancelledError if cancelled else ValueError("synthetic failure") if failed else None
    delegate = AsyncMock(return_value={"ok": True}, side_effect=side_effect)

    async def lookup(value: int):
        """Look up a synthetic value."""
        return await delegate(value)

    if route == "direct":
        raw = tool(lookup)
    elif route == "tracked_direct":
        raw = tool(tracked_tool(app_name="shop")(lookup))
    else:
        tool_def = {
            "description": "Look up a synthetic value.",
            "parameters": {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
        }
        if route == "registry":

            async def post(_session, _host, payload, _timeout):
                await delegate(payload["args"]["value"])
                return 200, '{"ok": true}'

            monkeypatch.setattr(registry, "post_registry_call", post)
            raw = registry.create_tool_from_api_dict("lookup", tool_def, "shop")
        else:

            async def invoke(_app, _name, args):
                return await delegate(args["value"])

            monkeypatch.setattr(combined.tracker, "invoke_tool", invoke)
            raw = combined.create_tool_from_tracker("lookup", tool_def, "shop")

    provider = ToolGuardingToolProvider(None)
    verdict = AsyncMock(return_value="synthetic test verdict" if denied else None)
    monkeypatch.setattr(
        provider,
        "_get_or_create_toolguard_runtime",
        AsyncMock(return_value=SimpleNamespace(guard_tool_call=verdict)),
    )
    guarded = provider._wrap_tool(raw, "shop").coroutine
    if "direct" in route:
        guarded = make_recording_awaitable(guarded, "lookup", app_name="shop", param_names=["value"])

    callback_calls = []

    class CountingHandler(CallbackHandler):
        def __init__(self):
            # LangChain exercises real callback registration/dispatch; avoid configuring a remote client.
            pass

        @property
        def ignore_agent(self):
            return caller_handler == "ignore_agent"

        def on_tool_start(self, *args, **kwargs):
            callback_calls.append("start")

        def on_tool_end(self, *args, **kwargs):
            callback_calls.append("end")

        def on_tool_error(self, *args, **kwargs):
            callback_calls.append("error")

    handler = langfuse_sdk._handler_class(CountingHandler)() if caller_handler == "sdk" else CountingHandler()
    ToolCallTracker.start_tracking()
    config_token = var_child_runnable_config.set({"callbacks": [handler] if caller_handler else []})
    try:
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await guarded(value=3)
            result = None
        elif failed and "direct" in route:
            with pytest.raises(ValueError, match="synthetic failure"):
                await guarded(value=3)
            result = None
        else:
            result = await guarded(value=3)
    finally:
        var_child_runnable_config.reset(config_token)
        local = ToolCallTracker.stop_tracking()

    verdict.assert_awaited_once()
    if denied:
        delegate.assert_not_awaited()
        assert result["blocked_by_policy"] is True
        assert result["policy_violation"] is True
        assert len(manual_calls) == 1
        assert manual_calls[0][0][2] == result
        assert manual_calls[0][1]["error"] == result["error"]
        assert callback_calls == []
    elif cancelled:
        delegate.assert_awaited_once_with(3)
        # BaseTool does not dispatch on_tool_error for CancelledError. Keep
        # the incomplete caller-owned span untouched, without a duplicate.
        assert callback_calls == (["start"] if caller_handler is True else [])
        if caller_handler is True:
            assert manual_calls == []
        else:
            assert len(manual_calls) == 1
            assert manual_calls[0][1]["error"] == "cancelled"
    elif failed:
        delegate.assert_awaited_once_with(3)
        terminal_event = "error" if "direct" in route else "end"
        if terminal_event == "end":
            assert "synthetic failure" in result["error"]
        assert callback_calls == (["start", terminal_event] if caller_handler is True else [])
        assert len(manual_calls) == (0 if caller_handler is True else 1)
        if manual_calls:
            assert "synthetic failure" in manual_calls[0][1]["error"]
        assert "synthetic failure" in local[0]["error"]
    else:
        delegate.assert_awaited_once_with(3)
        assert result == {"ok": True}
        assert callback_calls == (["start", "end"] if caller_handler is True else [])
        assert len(manual_calls) == (0 if caller_handler is True else 1)
    assert len(local) == 1
    assert local[0]["succeeded"] is (outcome == "allowed")
    assert local[0]["result"] == (None if failed else result)


@pytest.mark.asyncio
async def test_untracked_outer_tool_keeps_its_observation_when_same_named_inner_tool_records(manual_calls):
    @tracked_tool(app_name="inner_app")
    async def lookup(value):
        return value

    async def outer(value):
        return (await lookup(value)) * 2

    wrapped = make_recording_awaitable(outer, "lookup", app_name="outer_app", param_names=["value"])
    ToolCallTracker.start_tracking()
    try:
        assert await wrapped(3) == 6
    finally:
        local = ToolCallTracker.stop_tracking()
    assert [(record["app_name"], record["result"]) for record in local] == [
        ("inner_app", 3),
        ("outer_app", 6),
    ]
    assert [(kwargs["app_name"], args[2]) for args, kwargs in manual_calls] == [
        ("inner_app", 3),
        ("outer_app", 6),
    ]
