"""Unit tests for /stream SSE prelude events — W09.6 (thread_created)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _parse_sse_events(raw: str) -> list[dict]:
    """Parse a raw SSE response body into a list of {event, data} dicts."""
    events = []
    current: dict = {}
    for line in raw.split("\n"):
        if line.startswith("event:"):
            current["event"] = line[6:].strip()
        elif line.startswith("data:"):
            current["data"] = line[5:].strip()
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


def _stream_app_with_issued_id(tmp_path, monkeypatch):
    """Build a minimal FastAPI test app that exposes /stream with our real logic.

    Returns (app, db, seen_kwargs) where seen_kwargs captures the kwargs
    passed to the fake event_stream.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI

    from cuga.backend.server import main
    from cuga.backend.storage.relational.local import LocalRelationalStore
    from cuga.backend.server.conversation_threads import ConversationThreadsDB

    store = LocalRelationalStore(str(tmp_path / "threads.db"))
    db = ConversationThreadsDB()
    db._get_store = lambda: store

    monkeypatch.setattr(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db", lambda: db
    )

    app_state = SimpleNamespace(
        knowledge_provider=None,
        knowledge_engine=None,
        current_llm=None,
        agent_id="cuga-default",
        output_format=None,
        agent=SimpleNamespace(graph=MagicMock()),
        stop_events={},
    )
    seen: list = []

    async def fake_event_stream(*args, **kwargs):
        seen.append(kwargs)
        yield "data: ok\n\n"

    monkeypatch.setattr(main, "app_state", app_state)
    monkeypatch.setattr(main, "event_stream", fake_event_stream)
    monkeypatch.setattr(main, "get_query", AsyncMock(return_value="hello"))
    monkeypatch.setattr(main, "get_attachment_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(main.events_bridge, "forwards_to_events", lambda *a: False)

    app = FastAPI()
    app.post("/stream")(main.stream)
    app.dependency_overrides[main.require_chat_access] = lambda: SimpleNamespace(sub="user-tc")
    return app, db, seen


# ---------------------------------------------------------------------------
# W09.6: thread_created SSE event
# ---------------------------------------------------------------------------


def test_thread_created_event_emitted_when_no_x_thread_id(tmp_path, monkeypatch):
    """No X-Thread-ID → /stream passes issued_thread_id to event_stream (W09.6)."""
    from fastapi.testclient import TestClient


    app, db, seen = _stream_app_with_issued_id(tmp_path, monkeypatch)

    with TestClient(app) as client:
        response = client.post("/stream")

    assert response.status_code == 200, response.text
    # event_stream was called
    assert len(seen) == 1
    # issued_thread_id was passed and matches thread_id
    assert seen[0]["issued_thread_id"] is not None
    assert seen[0]["issued_thread_id"] == seen[0]["thread_id"]


def test_no_thread_created_event_when_x_thread_id_provided(tmp_path, monkeypatch):
    """Client supplies X-Thread-ID with a known record → issued_thread_id is None."""
    import asyncio
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock

    from cuga.backend.server.conversation_threads import Scope
    from cuga.backend.server import main

    monkeypatch.setattr("cuga.config.get_tenant_id", lambda: "tenant-1")
    monkeypatch.setattr("cuga.config.get_service_instance_id", lambda: "inst-1")

    app, db, seen = _stream_app_with_issued_id(tmp_path, monkeypatch)

    # Pre-create the record so resolve_owned_thread succeeds
    scope = Scope(
        tenant_id="tenant-1",
        instance_id="inst-1",
        agent_id="cuga-default",
        user_id="user-tc",
    )
    asyncio.run(db.create(scope, "existing-thread-id"))

    # Patch resolve_owned_thread to succeed
    monkeypatch.setattr(
        "cuga.backend.server.thread_guard.resolve_owned_thread", AsyncMock(return_value=None)
    )
    # Patch knowledge access checks to pass
    monkeypatch.setattr(main, "_knowledge_enabled_for_app_state", lambda s: False)

    with TestClient(app) as client:
        response = client.post("/stream", headers={"X-Thread-ID": "existing-thread-id"})

    assert response.status_code == 200, response.text
    assert len(seen) == 1
    # issued_thread_id must be None — client supplied the ID
    assert seen[0]["issued_thread_id"] is None


# ---------------------------------------------------------------------------
# W09.6: actual thread_created SSE event in the stream output
# ---------------------------------------------------------------------------


def test_thread_created_sse_event_is_first_in_stream(tmp_path, monkeypatch):
    """When issued_thread_id is set, the thread_created SSE event is first before graph events."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock


    from cuga.backend.cuga_graph.utils.agent_loop import AgentLoopAnswer
    from cuga.backend.server import main
    from cuga.backend.storage.relational.local import LocalRelationalStore
    from cuga.backend.server.conversation_threads import ConversationThreadsDB

    store = LocalRelationalStore(str(tmp_path / "threads2.db"))
    db = ConversationThreadsDB()
    db._get_store = lambda: store

    monkeypatch.setattr(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db", lambda: db
    )

    # Build app_state with graph that emits a real answer
    graph = MagicMock()

    async def _aget_state(cfg):
        return SimpleNamespace(
            values={},
            next=(),
            config={"configurable": {"checkpoint_id": "c1"}},
        )

    graph.aget_state = AsyncMock(side_effect=_aget_state)

    agent = SimpleNamespace(
        graph=graph,
        policy_system=None,
        enable_todos=None,
        reflection_enabled=None,
        shortlisting_tool_threshold=None,
        cuga_lite_max_steps=None,
        enable_filesystem_tools=None,
        special_instructions=None,
        supervisor_enabled=False,
        _checkpoint_config_version=None,
    )

    from types import SimpleNamespace

    app_state_real = SimpleNamespace(
        agent=agent,
        agent_id="cuga-default",
        output_format=None,
        current_llm=None,
        knowledge_provider=None,
        knowledge_engine=None,
        config_version=None,
        stop_events={},
    )
    monkeypatch.setattr(main, "app_state", app_state_real)
    monkeypatch.setattr(main, "_assert_run_thread_access", AsyncMock())
    monkeypatch.setattr(main, "_canonical_thread_id", lambda x: x)
    monkeypatch.setattr(main, "_checkpoint_thread_id", lambda tid, uid, aid: tid)
    monkeypatch.setattr(main, "_rehydrate_citation_ledger", AsyncMock())
    monkeypatch.setattr(main, "format_upload_context", MagicMock(return_value=None))
    monkeypatch.setattr(main, "_save_conversation_and_events_async", AsyncMock())
    monkeypatch.setattr(main, "log_error_ref", MagicMock(return_value="ref"))
    monkeypatch.setattr("cuga.config.get_tenant_id", lambda: "tenant-1")
    monkeypatch.setattr("cuga.config.get_service_instance_id", lambda: "inst-1")

    # Stub lifecycle DB so finally block doesn't error
    class FakeThreadsDB:
        async def mark_turn_committed(self, scope, tid, **kwargs):
            pass

        async def mark_projected(self, scope, tid, checkpoint_id):
            pass

    monkeypatch.setattr(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db",
        lambda: FakeThreadsDB(),
    )

    answer = AgentLoopAnswer(answer="world", end=True, interrupt=False, has_tools=False, tools=[])

    async def _fake_run_stream(state=None, resume=None):
        yield answer

    fake_loop = MagicMock()
    fake_loop.run_stream = _fake_run_stream
    fake_loop.get_langfuse_trace_id = MagicMock(return_value=None)
    monkeypatch.setattr("cuga.backend.cuga_graph.utils.agent_loop.AgentLoop", MagicMock(return_value=fake_loop))

    # Drive event_stream directly with issued_thread_id set
    issued_id = "server-issued-tid"

    async def _collect():
        events = []
        async for ev in main.event_stream(
            query="hello",
            api_mode=True,
            thread_id=issued_id,
            user_id="user-tc",
            agent_id="cuga-default",
            issued_thread_id=issued_id,
        ):
            events.append(ev)
        return events

    events = asyncio.run(_collect())

    assert events, "expected at least one SSE event"
    first = events[0]
    # First event should be thread_created
    assert "event: thread_created" in first
    assert issued_id in first


def test_no_thread_created_event_when_issued_thread_id_is_none(tmp_path, monkeypatch):
    """When issued_thread_id is None, no thread_created event is emitted."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from cuga.backend.cuga_graph.utils.agent_loop import AgentLoopAnswer
    from cuga.backend.server import main
    from cuga.backend.storage.relational.local import LocalRelationalStore
    from cuga.backend.server.conversation_threads import ConversationThreadsDB

    store = LocalRelationalStore(str(tmp_path / "threads3.db"))
    db = ConversationThreadsDB()
    db._get_store = lambda: store

    monkeypatch.setattr(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db", lambda: db
    )

    graph = MagicMock()

    async def _aget_state(cfg):
        return SimpleNamespace(
            values={},
            next=(),
            config={"configurable": {"checkpoint_id": "c2"}},
        )

    graph.aget_state = AsyncMock(side_effect=_aget_state)

    agent = SimpleNamespace(
        graph=graph,
        policy_system=None,
        enable_todos=None,
        reflection_enabled=None,
        shortlisting_tool_threshold=None,
        cuga_lite_max_steps=None,
        enable_filesystem_tools=None,
        special_instructions=None,
        supervisor_enabled=False,
        _checkpoint_config_version=None,
    )

    app_state_real = SimpleNamespace(
        agent=agent,
        agent_id="cuga-default",
        output_format=None,
        current_llm=None,
        knowledge_provider=None,
        knowledge_engine=None,
        config_version=None,
        stop_events={},
    )
    monkeypatch.setattr(main, "app_state", app_state_real)
    monkeypatch.setattr(main, "_assert_run_thread_access", AsyncMock())
    monkeypatch.setattr(main, "_canonical_thread_id", lambda x: x)
    monkeypatch.setattr(main, "_checkpoint_thread_id", lambda tid, uid, aid: tid)
    monkeypatch.setattr(main, "_rehydrate_citation_ledger", AsyncMock())
    monkeypatch.setattr(main, "format_upload_context", MagicMock(return_value=None))
    monkeypatch.setattr(main, "_save_conversation_and_events_async", AsyncMock())
    monkeypatch.setattr(main, "log_error_ref", MagicMock(return_value="ref"))
    monkeypatch.setattr("cuga.config.get_tenant_id", lambda: "tenant-1")
    monkeypatch.setattr("cuga.config.get_service_instance_id", lambda: "inst-1")

    class FakeThreadsDB:
        async def mark_turn_committed(self, scope, tid, **kwargs):
            pass

        async def mark_projected(self, scope, tid, checkpoint_id):
            pass

    monkeypatch.setattr(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db",
        lambda: FakeThreadsDB(),
    )

    answer = AgentLoopAnswer(answer="hi", end=True, interrupt=False, has_tools=False, tools=[])

    async def _fake_run_stream(state=None, resume=None):
        yield answer

    fake_loop = MagicMock()
    fake_loop.run_stream = _fake_run_stream
    fake_loop.get_langfuse_trace_id = MagicMock(return_value=None)
    monkeypatch.setattr("cuga.backend.cuga_graph.utils.agent_loop.AgentLoop", MagicMock(return_value=fake_loop))

    async def _collect():
        events = []
        async for ev in main.event_stream(
            query="hello",
            api_mode=True,
            thread_id="existing-tid",
            user_id="user-tc",
            agent_id="cuga-default",
            issued_thread_id=None,
        ):
            events.append(ev)
        return events

    events = asyncio.run(_collect())

    for ev in events:
        assert "thread_created" not in ev, f"unexpected thread_created in {ev!r}"
