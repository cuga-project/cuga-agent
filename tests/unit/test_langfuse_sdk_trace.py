"""SDK run traces (observability/langfuse_sdk.py): off by default, no-op without keys, no double tool records.

These tests do not require a Langfuse server.
"""

from __future__ import annotations

import pytest

from cuga.backend.observability import langfuse_sdk as lf

pytestmark = pytest.mark.unit


def test_tracing_is_off_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert lf.enabled() is False


def test_run_trace_off_leaves_the_config_unchanged(monkeypatch):
    monkeypatch.setattr(lf, "enabled", lambda: False)
    config = {"configurable": {"thread_id": "t1"}}
    with lf.run_trace("CugaAgent.invoke", "hi", "t1", config) as trace:
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
    assert lf.final_answer({"other": 1}) == {"other": 1}
