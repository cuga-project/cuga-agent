"""Stub-generation correctness for the E2B knowledge tool wrappers.

These tests pin the behavior that prevented the live "Amit's ID" trace
from happening:
  - thread_id auto-injection that ACTUALLY replaces empty strings (not
    just missing keys), so an LLM emitting ``thread_id=""`` literally
    still gets the chat's real thread_id pushed in.
  - whitespace-only thread_id is treated as empty.
  - tools that DON'T accept ``scope``/``thread_id`` get a stub with no
    validation block — avoids a TypeError inside the sandbox.
  - generated stubs are syntactically valid Python.
"""

from __future__ import annotations

import ast

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.e2b.e2b_executor import E2BExecutor


class _FakeKnowledgeTool:
    """Mirrors the attributes the wrapper attaches to a real tool."""

    _knowledge_allowed_scopes = ("agent", "session")
    _knowledge_default_scope = "agent"
    _knowledge_thread_id = "thread-abc-123"


@pytest.fixture
def executor() -> E2BExecutor:
    # Bypass __init__ — we only exercise the static-style stub generator.
    return E2BExecutor.__new__(E2BExecutor)


def _stub(executor: E2BExecutor, tool_name: str) -> str:
    return executor._serialize_knowledge_tool_stub(tool_name, _FakeKnowledgeTool())


def _exec_stub(stub_src: str, tool_name: str, args: tuple, kwargs: dict) -> dict:
    """Compile + execute one stub call against a fake ``call_api`` recorder.

    Returns the kwargs that were forwarded to ``call_api`` so we can
    assert the wrapper's transformation.
    """
    captured: dict = {}

    async def call_api(app: str, name: str, kw: dict):
        captured.update(kw)
        captured["__app__"] = app
        captured["__tool__"] = name
        return {}

    ns: dict = {"call_api": call_api}
    exec(compile(stub_src, "<stub>", "exec"), ns)
    import asyncio

    asyncio.run(ns[tool_name](*args, **kwargs))
    return captured


def test_all_generated_stubs_are_syntactically_valid(executor):
    """Catches any indentation regression in the f-string template."""
    for tool_name in E2BExecutor._KNOWLEDGE_POSITIONAL_ARGS:
        stub = _stub(executor, tool_name)
        ast.parse(stub)  # raises SyntaxError on failure


def test_stub_replaces_explicit_empty_thread_id(executor):
    """The fix for the live trace: LLM passes ``thread_id=""`` literally
    and we must overwrite it, not setdefault it."""
    stub = _stub(executor, "knowledge_search_knowledge")
    forwarded = _exec_stub(
        stub,
        "knowledge_search_knowledge",
        args=(),
        kwargs={"query": "test", "scope": "session", "thread_id": ""},
    )
    assert forwarded["thread_id"] == "thread-abc-123"


def test_stub_replaces_whitespace_thread_id(executor):
    """``"   "`` is not a valid thread_id — sanitizing it would resolve
    a phantom collection. Treat it as empty."""
    stub = _stub(executor, "knowledge_search_knowledge")
    forwarded = _exec_stub(
        stub,
        "knowledge_search_knowledge",
        args=(),
        kwargs={"query": "test", "scope": "session", "thread_id": "   "},
    )
    assert forwarded["thread_id"] == "thread-abc-123"


def test_stub_preserves_explicit_non_empty_thread_id(executor):
    """If the caller (or another wrapper) supplied a real thread_id,
    don't second-guess it."""
    stub = _stub(executor, "knowledge_search_knowledge")
    forwarded = _exec_stub(
        stub,
        "knowledge_search_knowledge",
        args=(),
        kwargs={"query": "test", "scope": "session", "thread_id": "caller-supplied"},
    )
    assert forwarded["thread_id"] == "caller-supplied"


def test_stub_injects_when_thread_id_missing_entirely(executor):
    """``setdefault`` worked for this case before — make sure we still do."""
    stub = _stub(executor, "knowledge_search_knowledge")
    forwarded = _exec_stub(
        stub,
        "knowledge_search_knowledge",
        args=(),
        kwargs={"query": "test", "scope": "session"},
    )
    assert forwarded["thread_id"] == "thread-abc-123"


def test_stub_handles_positional_call(executor):
    """LLM-emitted positional calls used to bypass validation and could
    later TypeError on duplicate kwargs."""
    stub = _stub(executor, "knowledge_search_knowledge")
    forwarded = _exec_stub(
        stub,
        "knowledge_search_knowledge",
        args=("hebrew query", "session"),
        kwargs={},
    )
    assert forwarded["query"] == "hebrew query"
    assert forwarded["scope"] == "session"
    assert forwarded["thread_id"] == "thread-abc-123"


def test_stub_for_tool_without_thread_id_does_not_inject(executor):
    """``knowledge_get_ingestion_status`` has no thread_id parameter.
    Injecting one would silently confuse the backend / future migrations."""
    stub = _stub(executor, "knowledge_get_ingestion_status")
    forwarded = _exec_stub(
        stub,
        "knowledge_get_ingestion_status",
        args=(),
        kwargs={"task_id": "task-1"},
    )
    assert "thread_id" not in forwarded
    assert "scope" not in forwarded


def test_stub_for_tool_without_scope_does_not_emit_scope_block(executor):
    """No scope validation block for status tools — saves a runtime
    TypeError from injecting a scope kwarg the function doesn't accept."""
    stub = _stub(executor, "knowledge_get_knowledge_status")
    assert "scope" not in stub
    assert "thread_id" not in stub


def test_stub_invalid_scope_returns_error(executor):
    """Sanity: agent-only tools refuse session scope and vice versa."""

    class _AgentOnlyTool:
        _knowledge_allowed_scopes = ("agent",)
        _knowledge_default_scope = "agent"
        _knowledge_thread_id = "thread-xyz"

    stub = executor._serialize_knowledge_tool_stub("knowledge_search_knowledge", _AgentOnlyTool())
    captured: dict = {}

    async def call_api(app: str, name: str, kw: dict):
        captured["__called__"] = True
        return {}

    ns: dict = {"call_api": call_api}
    exec(compile(stub, "<stub>", "exec"), ns)
    import asyncio

    result = asyncio.run(ns["knowledge_search_knowledge"](query="t", scope="session", thread_id="t1"))
    assert "error" in result
    assert "session" in result["error"]
    assert "__called__" not in captured  # short-circuited before HTTP


def test_stub_thread_id_block_skipped_when_no_session_scope(executor):
    """No need to inject thread_id when the engine doesn't even allow
    session scope — keeps the generated stub minimal."""

    class _AgentOnlyTool:
        _knowledge_allowed_scopes = ("agent",)
        _knowledge_default_scope = "agent"
        _knowledge_thread_id = "thread-xyz"

    stub = executor._serialize_knowledge_tool_stub("knowledge_search_knowledge", _AgentOnlyTool())
    assert "_existing_tid" not in stub  # no thread_id block at all
