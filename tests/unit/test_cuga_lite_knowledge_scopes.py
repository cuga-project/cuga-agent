from __future__ import annotations

from types import SimpleNamespace

from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
    _get_knowledge_tool_scope_context,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.e2b.e2b_executor import E2BExecutor


def test_knowledge_scope_context_requires_thread_for_session_scope():
    engine = SimpleNamespace(
        _config=SimpleNamespace(
            enabled=True,
            agent_level_enabled=False,
            session_level_enabled=True,
        )
    )

    scopes_without_thread, default_without_thread = _get_knowledge_tool_scope_context(engine, None)
    scopes_with_thread, default_with_thread = _get_knowledge_tool_scope_context(engine, "thread-123")

    assert scopes_without_thread == ()
    assert default_without_thread is None
    assert scopes_with_thread == ("session",)
    assert default_with_thread == "session"


def test_e2b_serializes_knowledge_wrapper_scope_and_thread_context():
    async def wrapped_knowledge_tool(*args, **kwargs):
        return {"args": args, "kwargs": kwargs}

    wrapped_knowledge_tool._knowledge_allowed_scopes = ("session",)
    wrapped_knowledge_tool._knowledge_default_scope = "session"
    wrapped_knowledge_tool._knowledge_thread_id = "thread-123"

    executor = E2BExecutor()
    stub = executor._serialize_knowledge_tool_stub(
        "knowledge_search_knowledge",
        wrapped_knowledge_tool,
    )

    assert 'kwargs["scope"] = "session"' in stub
    assert 'kwargs.setdefault("thread_id", "thread-123")' in stub
    assert "Allowed scopes" in stub
    assert 'return await call_api("knowledge", "knowledge_search_knowledge", kwargs)' in stub
