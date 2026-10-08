import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from cuga.backend.evolve import saved_memory_tracking
from cuga.backend.evolve.integration import EvolveIntegration
from cuga.backend.storage.facade import get_storage

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    import cuga.backend.storage.facade as facade

    monkeypatch.setattr(facade, "_local_db_path", lambda: str(tmp_path / "saved_memory_tracking.db"))
    monkeypatch.setattr(saved_memory_tracking, "_scope", lambda: ("tenant", "service"))
    get_storage().invalidate_relational_stores()
    yield
    get_storage().invalidate_relational_stores()


def answer(turn="turn"):
    return [
        {
            "event_name": "Answer",
            "event_data": json.dumps(
                {
                    "data": "A reply",
                    "memory_turn_id": turn,
                    "memory_usage": {"count": 1, "entity_ids": ["used"]},
                }
            ),
        }
    ]


@pytest.mark.asyncio
async def test_reopen_confirms_late_save_without_waiting_for_write(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def write(*args):
        entered.set()
        await release.wait()
        return {
            "updates": [
                {"event": "ADD", "id": "one"},
                {"event": "UPDATE", "id": "one"},
                {"event": "UPDATE", "id": "two"},
                {"event": "DELETE", "id": "deleted"},
                {"event": "NONE", "id": "unchanged"},
            ]
        }

    monkeypatch.setattr(EvolveIntegration, "get_compliance_status", AsyncMock(return_value={}))
    monkeypatch.setattr(EvolveIntegration, "_call_tool", write)
    task = asyncio.create_task(
        EvolveIntegration.store_user_facts(
            "alice",
            "likes pizza",
            metadata={"agent_id": "agent"},
            namespace_id="service",
            turn_id="turn",
        )
    )
    events = answer()
    scope = dict(agent_id="agent", user_id="alice")
    try:
        await asyncio.wait_for(entered.wait(), 2)
        pending = await asyncio.wait_for(saved_memory_tracking.enrich_saved_memories(events, **scope), 2)
        assert "memory_saved" not in json.loads(pending[0]["event_data"])
        assert not task.done()
        release.set()
        await task
        get_storage().invalidate_relational_stores()
        reopened = await saved_memory_tracking.enrich_saved_memories(events, **scope)
        payload = json.loads(reopened[0]["event_data"])
        assert payload["memory_saved"] == {"count": 2, "entity_ids": ["one", "two"]}
        assert payload["memory_usage"]["entity_ids"] == ["used"]
        assert "memory_saved" not in json.loads(events[0]["event_data"])
        for other in [{"user_id": "bob"}, {"agent_id": "other"}]:
            assert await saved_memory_tracking.enrich_saved_memories(events, **(scope | other)) == events
        assert await saved_memory_tracking.enrich_saved_memories(answer("other-turn"), **scope) == answer(
            "other-turn"
        )
        for other_scope in [("tenant", "other-service"), ("other-tenant", "service")]:
            monkeypatch.setattr(saved_memory_tracking, "_scope", lambda: other_scope)
            assert await saved_memory_tracking.enrich_saved_memories(events, **scope) == events
    finally:
        release.set()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, {"error": "failed"}, {"updates": []}])
async def test_empty_or_failed_save_never_reports_success(result):
    await saved_memory_tracking.record_saved_memories(
        result, turn_id="turn", agent_id="agent", user_id="alice"
    )
    assert (
        await saved_memory_tracking.enrich_saved_memories(answer(), agent_id="agent", user_id="alice")
        == answer()
    )


@pytest.mark.asyncio
async def test_legacy_history_and_repeated_confirmations():
    result = {"updates": [{"event": "ADD", "id": "one"}]}
    for _ in range(2):
        await saved_memory_tracking.record_saved_memories(
            result, turn_id="turn", agent_id="agent", user_id="alice"
        )
    events = [
        {"event_name": "Answer", "event_data": "plain old reply"},
        {
            "event_name": "Answer",
            "event_data": json.dumps({"data": "older reply", "memory_usage": {"turn_id": "turn"}}),
        },
    ]
    data = await saved_memory_tracking.enrich_saved_memories(events, agent_id="agent", user_id="alice")
    assert data[0] == events[0]
    assert json.loads(data[1]["event_data"])["memory_saved"]["count"] == 1


@pytest.mark.asyncio
async def test_history_endpoint_enriches_only_authenticated_users_history(monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from cuga.backend.server import main

    await saved_memory_tracking.record_saved_memories(
        {"updates": [{"event": "ADD", "id": "one"}]},
        turn_id="turn",
        agent_id="agent",
        user_id="alice",
    )
    history = AsyncMock(return_value=SimpleNamespace(events=answer()))
    monkeypatch.setattr(main, "get_conversation_db", lambda: SimpleNamespace(get_stream_events=history))
    app = FastAPI()
    app.add_api_route("/history/{thread_id}", main.get_conversation_stream_events)
    app.dependency_overrides[main.require_chat_access] = lambda: SimpleNamespace(sub="alice")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/history/thread?agent_id=agent&user_id=bob")
    assert response.status_code == 200
    history.assert_awaited_once_with("agent", "thread", "alice")
    assert json.loads(response.json()["events"][0]["event_data"])["memory_saved"]["entity_ids"] == ["one"]


@pytest.mark.asyncio
async def test_write_failure_does_not_record_saved_memory(monkeypatch):
    monkeypatch.setattr(EvolveIntegration, "get_compliance_status", AsyncMock(return_value={}))
    monkeypatch.setattr(EvolveIntegration, "_call_tool", AsyncMock(side_effect=RuntimeError("write failed")))
    await EvolveIntegration.store_user_facts(
        "alice",
        "likes pizza",
        metadata={"agent_id": "agent"},
        turn_id="turn",
    )
    assert (
        await saved_memory_tracking.enrich_saved_memories(answer(), agent_id="agent", user_id="alice")
        == answer()
    )
