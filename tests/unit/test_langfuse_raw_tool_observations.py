"""Raw direct tools need manual observations even when only a caller trace id exists."""

import asyncio

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker
from cuga.backend.cuga_graph.utils import langfuse_tracing
from cuga.backend.observability import langfuse_sdk as lf

pytestmark = pytest.mark.unit


class _Observation:
    def __init__(self):
        self.ended = 0

    def end(self):
        self.ended += 1


class _Client:
    def __init__(self, current_trace_id):
        self.current_trace_id = current_trace_id
        self.observations = []

    def get_current_trace_id(self):
        return self.current_trace_id

    def start_observation(self, **kwargs):
        observation = _Observation()
        self.observations.append((kwargs, observation))
        return observation


@pytest.fixture(params=[None, "active-trace"])
def traced_tools(request, monkeypatch):
    client = _Client(request.param)
    monkeypatch.setattr(lf, "enabled", lambda: True)
    monkeypatch.setattr(lf, "_client", lambda: client)
    trace_token = langfuse_tracing._langfuse_trace_id.set("caller-trace")
    tracking_token = tracker._tracking_enabled_context.set(False)
    try:
        yield client
    finally:
        tracker._tracking_enabled_context.reset(tracking_token)
        langfuse_tracing._langfuse_trace_id.reset(trace_token)


def _assert_one_observation(client, *, error=None, output=None):
    assert len(client.observations) == 1
    payload, observation = client.observations[0]
    assert observation.ended == 1
    assert payload["as_type"] == "tool"
    assert payload["input"] == {"value": 3}
    assert payload["output"] == output
    assert payload["level"] == ("ERROR" if error is not None else None)
    assert payload["status_message"] == error
    assert payload["trace_context"] == (None if client.current_trace_id else {"trace_id": "caller-trace"})


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "error", "cancelled"])
async def test_raw_direct_tool_records_once_without_local_tracking(traced_tools, outcome):
    # prepare_node extracts .func/.coroutine and invokes it directly, without
    # BaseTool.ainvoke or any LangChain on_tool_* callbacks.
    async def raw(value):
        if outcome == "error":
            raise ValueError("failed")
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        return value * 2

    wrapped = tracker.make_recording_awaitable(raw, "raw", param_names=["value"])
    if outcome == "success":
        assert await wrapped(3) == 6
        _assert_one_observation(traced_tools, output=6)
    else:
        exception = ValueError if outcome == "error" else asyncio.CancelledError
        with pytest.raises(exception):
            await wrapped(3)
        _assert_one_observation(traced_tools, error="failed" if outcome == "error" else "cancelled")


@pytest.mark.asyncio
async def test_tracked_async_tool_cancellation_is_an_error(traced_tools):
    @tracker.tracked_tool
    async def cancelled(value):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await cancelled(value=3)
    _assert_one_observation(traced_tools, error="cancelled")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload, expected_error",
    [
        (
            {"blocked_by_policy": True, "policy_violation": True, "error": "Tool call blocked by policy"},
            "Tool call blocked by policy",
        ),
        ({"error": "A business result, not an exception"}, None),
        ({"blocked_by_policy": True, "error": "missing second sentinel"}, None),
        ({"blocked_by_policy": 1, "policy_violation": True, "error": "not a boolean"}, None),
        ({"blocked_by_policy": True, "policy_violation": True, "error": ""}, None),
        ({"blocked_by_policy": True, "policy_violation": True, "error": {"detail": "not text"}}, None),
    ],
)
async def test_direct_tool_only_marks_explicit_policy_denials(traced_tools, payload, expected_error):
    async def raw(value):
        return payload

    wrapped = tracker.make_recording_awaitable(raw, "raw", param_names=["value"])
    tracker.ToolCallTracker.start_tracking()
    try:
        assert await wrapped(3) is payload
    finally:
        calls = tracker.ToolCallTracker.stop_tracking()
    assert len(calls) == 1
    assert calls[0]["error"] == expected_error
    assert calls[0]["succeeded"] is (expected_error is None)
    assert calls[0]["result"] == payload
    _assert_one_observation(traced_tools, error=expected_error, output=payload)
