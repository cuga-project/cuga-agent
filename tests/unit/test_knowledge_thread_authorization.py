"""Run entry points must authorize threads before reading state or minting credentials."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from cuga.backend.knowledge.session_provider import SessionProvider
from cuga.backend.server import main
from cuga.backend.server.conversation_history import ConversationHistoryDB

pytestmark = pytest.mark.unit


@pytest.fixture
def runtime(monkeypatch):
    provider = SessionProvider()
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(values={}, next=()))
    state = SimpleNamespace(
        stop_events={},
        knowledge_provider=provider,
        knowledge_engine=SimpleNamespace(_config=SimpleNamespace(enabled=True)),
        current_llm=None,
        agent_id="cuga-default",
        output_format=None,
        agent=SimpleNamespace(graph=graph),
    )
    database = ConversationHistoryDB()
    monkeypatch.setattr(main, "app_state", state)
    monkeypatch.setattr(main, "get_conversation_db", lambda: database)
    return provider, database, graph


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_source", ["knowledge", "conversation"])
@pytest.mark.parametrize("user", ["alice", "bob"])
async def test_stream_authorizes_thread_before_dispatch(runtime, monkeypatch, owner_source, user):
    provider, database, _ = runtime
    if owner_source == "knowledge":
        provider.claim_session("victim-thread", "alice")
        await database.create_conversation("victim-thread", "alice", "cuga-default")
    else:
        # History only: a conversation from before session continuity, which nobody can continue.
        await database.save_conversation("cuga-default", "victim-thread", 1, "alice", [])

    async def empty_stream(*args, **kwargs):
        if False:
            yield ""

    dispatch = MagicMock(side_effect=empty_stream)
    monkeypatch.setattr(main, "event_stream", dispatch)
    monkeypatch.setattr(main, "get_query", AsyncMock(return_value="list my documents"))
    monkeypatch.setattr(main, "get_attachment_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(main.events_bridge, "forwards_to_events", lambda *args: False)
    app = FastAPI()
    app.post("/stream")(main.stream)
    app.dependency_overrides[main.require_chat_access] = lambda: SimpleNamespace(sub=user)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/stream", headers={"X-Thread-ID": " victim-thread "})
    if user == "alice" and owner_source == "knowledge":
        assert response.status_code == 200, response.text
        assert dispatch.call_args.kwargs["thread_id"] == "victim-thread"
        assert provider.get_session("victim-thread").user_id == "alice"
    else:
        assert response.status_code == 404, response.text
        dispatch.assert_not_called()
        if owner_source == "conversation":
            assert provider.get_session("victim-thread") is None


@pytest.mark.asyncio
async def test_event_stream_also_checks_owner_before_reading_checkpoint(runtime):
    provider, database, graph = runtime
    provider.claim_session("victim-thread", "alice")
    stream = main.event_stream("list documents", thread_id="victim-thread", user_id="bob")
    frame = await anext(stream)
    assert "event: Error" in frame
    assert "Access denied" in frame
    graph.get_state.assert_not_called()
    graph.aget_state.assert_not_called()
    assert await database.claim_thread("victim-thread", "alice", create=False) is None


@pytest.mark.asyncio
async def test_run_authorization_stays_off_without_authenticated_user(runtime):
    provider, database, _ = runtime
    provider.claim_session("victim-thread", "alice")
    database.claim_thread = AsyncMock()
    await main._assert_run_thread_access("victim-thread", main.DEFAULT_USER_ID)
    database.claim_thread.assert_not_awaited()
    assert provider.get_session("victim-thread").user_id == "alice"


@pytest.mark.asyncio
@pytest.mark.parametrize("thread", ["victim-thread", "victim_thread"])
@pytest.mark.parametrize("knowledge_enabled", [True, False])
async def test_conversation_delete_cannot_remove_another_users_ownership(
    runtime, monkeypatch, thread, knowledge_enabled
):
    provider, database, _ = runtime
    main.app_state.knowledge_engine._config.enabled = knowledge_enabled
    provider.claim_session("victim-thread", "alice")
    # The DB layer returns True even for a user-filtered delete matching no rows.
    database.delete_thread = AsyncMock(return_value=True)
    main.app_state.knowledge_engine.drop_collection = AsyncMock()
    uploads = AsyncMock()
    monkeypatch.setattr(main, "delete_thread_uploads", uploads)
    app = FastAPI()
    app.state.app_state = main.app_state
    app.delete("/api/conversations/{conversation_id}")(main.delete_conversation)
    app.dependency_overrides[main.require_chat_access] = lambda: SimpleNamespace(sub="bob")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete(f"/api/conversations/{thread}")
    assert response.status_code == 404, response.text
    database.delete_thread.assert_not_awaited()
    main.app_state.knowledge_engine.drop_collection.assert_not_awaited()
    uploads.assert_not_awaited()
    assert provider.get_session("victim-thread").user_id == "alice"


@pytest.mark.asyncio
async def test_conversation_owner_can_still_delete_knowledge_and_uploads(runtime, monkeypatch):
    provider, database, _ = runtime
    provider.claim_session("victim-thread", "alice")
    await database.create_conversation("victim-thread", "alice", "cuga-default")
    database.delete_thread = AsyncMock(return_value=True)
    main.app_state.knowledge_engine.drop_collection = AsyncMock()
    uploads = AsyncMock()
    monkeypatch.setattr(main, "delete_thread_uploads", uploads)
    app = FastAPI()
    app.state.app_state = main.app_state
    app.delete("/api/conversations/{conversation_id}")(main.delete_conversation)
    app.dependency_overrides[main.require_chat_access] = lambda: SimpleNamespace(sub="alice")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/conversations/victim-thread")
    assert response.status_code == 200, response.text
    database.delete_thread.assert_awaited_once_with("cuga-default", "victim-thread", "alice")
    main.app_state.knowledge_engine.drop_collection.assert_awaited_once_with("kb_sess_victim_thread")
    uploads.assert_awaited_once_with("victim-thread")
    # Generated files and checkpoints may survive cleanup, so keep the owner.
    assert provider.get_session("victim-thread").user_id == "alice"
    with pytest.raises(HTTPException) as exc:
        await main._assert_run_thread_access("victim-thread", "bob")
    assert exc.value.status_code == 403
