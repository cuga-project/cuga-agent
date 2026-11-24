"""Ownership diagnostics, the managed handler's trace id, inner-tool callbacks and early root close."""

from __future__ import annotations

from types import MethodType, SimpleNamespace

import pytest
from langchain_core.callbacks import AsyncCallbackManager, BaseCallbackHandler
from langchain_core.runnables.config import var_child_runnable_config
from langfuse.langchain import CallbackHandler
from loguru import logger

from cuga import sdk
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import tool_callback_recording
from cuga.backend.observability import langfuse_sdk as lf

pytestmark = pytest.mark.unit


class StockHandler(CallbackHandler):
    def __init__(self):  # no Langfuse client: these tests never export
        pass


class ManagedHandler(lf._handler_class(StockHandler)):
    def __init__(self, trace_id=None):
        self._cuga_trace_id = trace_id


class OtherHandler(BaseCallbackHandler):
    pass


@pytest.fixture
def warnings(monkeypatch):
    monkeypatch.setattr(lf, "enabled", lambda: True)
    seen = []
    sink = logger.add(lambda message: seen.append(message.record["message"]), level="WARNING")
    yield seen
    logger.remove(sink)


def test_managed_handler_without_trace_id_warns_when_run_has_trace_id(warnings):
    lf.check_tool_ownership(
        {"callbacks": [ManagedHandler()], "configurable": {"langfuse_trace_id": "a" * 32}}
    )
    assert any("different traces" in message for message in warnings)


def test_managed_handler_with_same_trace_id_does_not_warn(warnings):
    tid = "b" * 32
    lf.check_tool_ownership({"callbacks": [ManagedHandler(tid)], "configurable": {"langfuse_trace_id": tid}})
    assert warnings == []


def test_managed_handler_without_any_trace_warns(warnings):
    lf.check_tool_ownership({"callbacks": [ManagedHandler()]})
    assert any("not recorded" in message for message in warnings)


def test_mixed_handlers_warn(warnings):
    tid = "c" * 32
    lf.check_tool_ownership(
        {"callbacks": [ManagedHandler(tid), StockHandler()], "configurable": {"langfuse_trace_id": tid}}
    )
    assert any("mixed" in message for message in warnings)


def test_no_warning_when_tracing_is_off(monkeypatch):
    monkeypatch.setattr(lf, "enabled", lambda: False)
    seen = []
    sink = logger.add(lambda message: seen.append(message), level="WARNING")
    try:
        lf.check_tool_ownership({"callbacks": [ManagedHandler()]})
    finally:
        logger.remove(sink)
    assert seen == []


@pytest.mark.asyncio
async def test_caller_owned_stream_warns_for_managed_handler_without_trace(warnings):
    async def source(message, **kwargs):
        yield {"final_answer": "done"}

    agent = SimpleNamespace(
        _callbacks=None,
        _trace_run_config=lambda config: dict(config or {}),
        _trace_thread_id=lambda message, thread_id, action_response, config: thread_id,
        _stream_untraced=source,
    )
    agent.stream = MethodType(sdk.CugaAgent.stream, agent)
    updates = [state async for state in agent.stream("hi", config={"callbacks": [ManagedHandler()]})]
    assert updates == [{"final_answer": "done"}]
    assert any("not recorded" in message for message in warnings)


def test_factory_trace_id_sets_trace_context(monkeypatch):
    seen = {}

    class Base(CallbackHandler):
        def __init__(self, *, public_key=None, trace_context=None):
            seen.update(public_key=public_key, trace_context=trace_context)

    monkeypatch.setattr("langfuse.langchain.CallbackHandler", Base)
    handler = lf.create_sdk_tool_handler(trace_id="d" * 32)
    assert seen["trace_context"] == {"trace_id": "d" * 32}
    assert handler._cuga_trace_id == "d" * 32
    assert handler._cuga_skips_tool_observations is True
    with pytest.raises(ValueError):
        lf.create_sdk_tool_handler(trace_id="d" * 32, trace_context={"trace_id": "e" * 32})


@pytest.mark.parametrize("as_manager", [False, True])
def test_inner_tool_drops_only_langfuse_callbacks_after_outer_callback(monkeypatch, as_manager):
    monkeypatch.setattr(lf, "enabled", lambda: True)
    stock, other = StockHandler(), OtherHandler()
    callbacks = AsyncCallbackManager(handlers=[stock, other], inheritable_handlers=[stock, other])
    token = var_child_runnable_config.set({"callbacks": callbacks if as_manager else [stock, other]})
    try:
        assert lf.inner_tool_config("lookup") is None  # no outer callback: keep the inherited callbacks
        with tool_callback_recording("lookup", covered=True):
            config = lf.inner_tool_config("lookup")
            assert lf.inner_tool_config("other_tool") is None
    finally:
        var_child_runnable_config.reset(token)
    kept = config["callbacks"]
    if as_manager:
        assert kept.handlers == [other] and kept.inheritable_handlers == [other]
        assert callbacks.handlers == [stock, other]  # the run's own manager is not changed
    else:
        assert kept == [other]


def test_inner_tool_config_is_none_when_tracing_off(monkeypatch):
    monkeypatch.setattr(lf, "enabled", lambda: False)
    with tool_callback_recording("lookup", covered=True):
        assert lf.inner_tool_config("lookup") is None


def test_run_trace_close_ends_span_once_and_ignores_later_end():
    calls = []
    span = SimpleNamespace(
        update=lambda **fields: calls.append(("update", fields)),
        set_trace_io=lambda **fields: calls.append(("io", fields)),
        end=lambda: calls.append(("end",)),
    )
    trace = lf.RunTrace(span)
    trace.end(error="cancelled")
    trace.close()
    trace.close()
    trace.end("late output")
    assert [call[0] for call in calls] == ["update", "end"]
