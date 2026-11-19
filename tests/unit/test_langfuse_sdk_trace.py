"""SDK run traces (observability/langfuse_sdk.py): off by default, no-op without keys, no double tool records.

These tests do not require a Langfuse server.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from cuga.backend.observability import langfuse_sdk as lf

pytestmark = pytest.mark.unit


def test_tracing_is_off_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert lf.enabled() is False


@pytest.mark.asyncio
async def test_run_trace_off_leaves_the_config_unchanged(monkeypatch):
    monkeypatch.setattr(lf, "enabled", lambda: False)
    config = {"configurable": {"thread_id": "t1"}}
    async with lf.run_trace("CugaAgent.invoke", "hi", "t1", config) as trace:
        assert trace.span is None
    assert config == {"configurable": {"thread_id": "t1"}}


def test_record_tool_call_off_never_opens_a_client(monkeypatch):
    monkeypatch.setattr(lf, "enabled", lambda: False)
    monkeypatch.setattr(lf, "_client", lambda: pytest.fail("client opened while tracing is off"))
    lf.record_tool_call("read", {"key": "k"}, "value", app_name="kv", duration_ms=3.0)


def test_a_caller_trace_id_is_kept():
    assert lf.caller_has_trace({"configurable": {"langfuse_trace_id": "abc"}}) is True
    assert lf.caller_has_trace({"configurable": {}}) is False
    assert lf.caller_has_trace(None) is False


def test_handler_drops_tool_runs_so_each_tool_call_is_recorded_once():
    seen = []

    class Base:
        def on_tool_start(self, *a, **k):
            seen.append("start")

        def on_tool_end(self, *a, **k):
            seen.append("end")

        def on_llm_start(self, *a, **k):
            seen.append("llm")

    handler = lf._handler_class(Base)()
    handler.on_tool_start({}, "x")
    handler.on_tool_end("y")
    handler.on_llm_start({}, ["p"])
    assert seen == ["llm"]


def test_long_values_are_cut():
    out = lf._cut({"text": "x" * (lf.MAX_FIELD_CHARS + 10), "n": 1, "items": list(range(500))})
    assert out["text"].endswith(f"[{lf.MAX_FIELD_CHARS + 10} chars]")
    assert out["n"] == 1 and len(out["items"]) == 200


def test_final_answer_is_found_in_a_streamed_state():
    assert lf.final_answer(("ns", {"node": {"final_answer": "done"}})) == "done"
    assert lf.final_answer({"node": {"final_answer": ""}}) == ""
    assert lf.final_answer({"other": 1}) == {"other": 1}


@pytest.fixture
def tracing(monkeypatch):
    import langfuse
    from langfuse import langchain

    events = []
    loop_thread = threading.get_ident()

    class CallbackHandler:
        pass

    from cuga.backend.cuga_graph.utils import langfuse_tracing

    monkeypatch.setattr(langfuse_tracing, "_langfuse_handler_classes", lambda: (CallbackHandler,))
    monkeypatch.setattr(langchain, "CallbackHandler", CallbackHandler)
    monkeypatch.setattr(
        langfuse, "propagate_attributes", lambda **attrs: events.append(attrs) or nullcontext()
    )
    monkeypatch.setattr(lf, "enabled", lambda: True)

    class Client:
        @staticmethod
        def get_current_trace_id():
            return None

        @staticmethod
        @contextmanager
        def start_as_current_observation(**options):
            events.append(("open", options["name"]))
            try:
                yield SimpleNamespace(update=lambda **kw: None, set_trace_io=lambda **kw: None)
            finally:
                events.append(("close", options["name"]))

        @staticmethod
        def flush():
            events.append(("flush_off_loop", threading.get_ident() != loop_thread))

    monkeypatch.setattr(lf, "_client", Client)
    return events, CallbackHandler


@pytest.mark.asyncio
async def test_flush_is_explicit_after_owned_root_closes(tracing):
    events, _ = tracing
    async with lf.run_trace("root", "hello", "thread-1", {}):
        async with lf.run_trace("child", "hello", "thread-1", {}):
            pass
    assert not any(isinstance(event, tuple) and event[0] == "flush_off_loop" for event in events)
    await asyncio.to_thread(lf.flush)
    assert events[0]["session_id"] == "thread-1"
    assert events[1:] == [
        ("open", "root"),
        ("open", "child"),
        ("close", "child"),
        ("close", "root"),
        ("flush_off_loop", True),
    ]


@pytest.mark.asyncio
async def test_invoke_preserves_one_handler_through_reentry_and_groups_first_turn(tracing, monkeypatch):
    from cuga.backend.cuga_graph.utils.langfuse_tracing import get_langfuse_invoke_config
    from cuga.sdk import CugaAgent, InvokeResult

    events, _ = tracing
    agent = CugaAgent.__new__(CugaAgent)
    agent._callbacks = []
    seen = []

    async def invoke_untraced(message, *, thread_id, config, **kwargs):
        seen.append(thread_id)
        if message == "first":
            return await agent.invoke("inner", thread_id=thread_id, config=config)
        # Use the same callback config route as the SDK graph.
        agent._apply_callbacks(config)
        assert len(get_langfuse_invoke_config()["callbacks"]) == 1
        return InvokeResult(answer="done", thread_id=thread_id)

    monkeypatch.setattr(agent, "_invoke_untraced", invoke_untraced)
    supplied = {"configurable": {"langfuse": {"user_id": "user"}}}
    result = await agent.invoke("first", config=supplied)
    assert result.thread_id and seen == [result.thread_id] * 2
    assert events[0]["session_id"] == result.thread_id
    assert [x for x in events if isinstance(x, tuple) and x[0] == "open"] == [("open", "cuga.invoke")]
    assert "callbacks" not in supplied


@pytest.mark.asyncio
async def test_constructor_handler_is_kept_and_shared_without_sdk_root(tracing, monkeypatch):
    from cuga.backend.cuga_graph.utils.langfuse_tracing import get_langfuse_invoke_config
    from cuga.sdk import CugaAgent, InvokeResult

    events, callback_class = tracing
    agent = CugaAgent.__new__(CugaAgent)
    supplied = callback_class()
    agent._callbacks = [supplied]

    async def invoke_untraced(message, *, thread_id, config, **kwargs):
        agent._apply_callbacks(config)
        assert get_langfuse_invoke_config()["callbacks"] == [supplied]
        assert config["callbacks"].count(supplied) == 1
        return InvokeResult(answer="done", thread_id=thread_id)

    monkeypatch.setattr(agent, "_invoke_untraced", invoke_untraced)
    await agent.invoke("test", thread_id="thread-1")
    assert events == []


@pytest.mark.asyncio
async def test_cancelled_run_is_error_and_retains_partial_output(tracing, monkeypatch):
    updates = []
    io = []
    client = lf._client()
    monkeypatch.setattr(lf, "_client", lambda: client)

    @contextmanager
    def observation(**options):
        yield SimpleNamespace(update=lambda **kw: updates.append(kw), set_trace_io=lambda **kw: io.append(kw))

    monkeypatch.setattr(client, "start_as_current_observation", observation)
    with pytest.raises(asyncio.CancelledError):
        async with lf.run_trace("stream", "hello", "t", {}) as trace:
            trace.end("partial")
            raise asyncio.CancelledError()
    assert updates[-1] == {"level": "ERROR", "status_message": "cancelled"}
    assert io[-1] == {"output": "partial"}


@pytest.mark.asyncio
async def test_ordinary_error_retains_partial_output(tracing, monkeypatch):
    updates = []
    client = lf._client()
    monkeypatch.setattr(lf, "_client", lambda: client)

    @contextmanager
    def observation(**options):
        yield SimpleNamespace(update=lambda **kw: updates.append(kw), set_trace_io=lambda **kw: None)

    monkeypatch.setattr(client, "start_as_current_observation", observation)
    with pytest.raises(ValueError, match="failed"):
        async with lf.run_trace("stream", "hello", "t", {}) as trace:
            trace.end("partial")
            raise ValueError("failed")
    assert updates == [{"output": "partial"}, {"level": "ERROR", "status_message": "ValueError: failed"}]


@pytest.mark.asyncio
async def test_exporter_is_never_called_on_root_hot_path(tracing, monkeypatch):
    monkeypatch.setattr(lf, "flush", lambda: pytest.fail("per-run exporter invoked"))
    async with lf.run_trace("invoke", "synthetic", "t", {}):
        pass
    await asyncio.sleep(0)  # Also detect a scheduled fire-and-forget flush.


def test_has_current_span_without_langfuse_probe():
    from opentelemetry import trace

    assert lf._has_current_span() is False
    context = trace.SpanContext(1, 2, is_remote=False)
    with trace.use_span(trace.NonRecordingSpan(context)):
        assert lf._has_current_span() is True


@pytest.mark.parametrize("module", ["example.callbacks", "my_app.handlers"])
def test_unrelated_callback_handler_name_is_not_langfuse(module):
    from cuga.backend.cuga_graph.utils.langfuse_tracing import is_langfuse_callback_handler

    handler = type("CallbackHandler", (), {"__module__": module})()
    # A name alone must not opt an unrelated callback into tracing ownership.
    assert not is_langfuse_callback_handler(handler)


def test_run_error_status_is_cut_without_changing_output():
    updates = []
    span = SimpleNamespace(update=lambda **fields: updates.append(fields))
    text = "synthetic diagnostic " + "x" * lf.MAX_FIELD_CHARS
    lf.RunTrace(span).end(error=text)
    assert updates == [{"level": "ERROR", "status_message": lf._cut(text)}]
