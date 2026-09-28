from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.server.agents_routes import router
from cuga.backend.server.auth import require_chat_access, require_manage_access
from cuga.backend.server.config_store import reset_config_db

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _registry_on(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: True,
    )


def _client() -> TestClient:
    reset_config_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_manage_access] = lambda: None
    app.dependency_overrides[require_chat_access] = lambda: None
    return TestClient(app)


def test_list_agents_always_includes_default_when_store_empty():
    client = _client()

    response = client.get("/api/agents")

    assert response.status_code == 200
    agents = response.json()["agents"]
    assert [a["id"] for a in agents] == ["cuga-default"]


def test_create_agent_appears_in_list():
    client = _client()

    create_response = client.post(
        "/api/agents", json={"name": "Flight Booker", "description": "Books flights"}
    )
    assert create_response.status_code == 200
    assert create_response.json()["id"] == "flight-booker"

    agents = client.get("/api/agents").json()["agents"]
    ids = [a["id"] for a in agents]
    assert "flight-booker" in ids
    created = next(a for a in agents if a["id"] == "flight-booker")
    assert created["name"] == "Flight Booker"
    assert created["description"] == "Books flights"
    assert created["kind"] == "single"


def test_create_supervisor_agent_persists_kind():
    client = _client()

    create_response = client.post("/api/agents", json={"name": "Trip Supervisor", "kind": "supervisor"})
    assert create_response.status_code == 200

    agents = client.get("/api/agents").json()["agents"]
    created = next(a for a in agents if a["id"] == "trip-supervisor")
    assert created["kind"] == "supervisor"


def test_create_agent_name_collision_is_rejected():
    client = _client()
    client.post("/api/agents", json={"name": "Flight Booker"})

    response = client.post("/api/agents", json={"name": "Flight Booker"})

    assert response.status_code == 409


def test_delete_agent_removes_it_from_list():
    client = _client()
    client.post("/api/agents", json={"name": "Flight Booker"})

    response = client.delete("/api/agents/flight-booker")

    assert response.status_code == 200
    ids = [a["id"] for a in client.get("/api/agents").json()["agents"]]
    assert "flight-booker" not in ids


def test_delete_default_agent_is_rejected():
    client = _client()

    response = client.delete("/api/agents/cuga-default")

    assert response.status_code == 400
    ids = [a["id"] for a in client.get("/api/agents").json()["agents"]]
    assert "cuga-default" in ids


def test_delete_nonexistent_agent_returns_404():
    client = _client()

    response = client.delete("/api/agents/does-not-exist")

    assert response.status_code == 404


def test_registry_disabled_hides_extra_agents_and_blocks_mutations(monkeypatch):
    client = _client()
    assert client.post("/api/agents", json={"name": "Flight Booker"}).status_code == 200

    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: False,
    )

    listed = client.get("/api/agents")
    assert listed.status_code == 200
    assert [a["id"] for a in listed.json()["agents"]] == ["cuga-default"]

    assert client.post("/api/agents", json={"name": "Other Agent"}).status_code == 404
    assert client.delete("/api/agents/flight-booker").status_code == 404


def test_list_agents_allows_chat_access_without_manage():
    reset_config_db()
    app = FastAPI()
    app.include_router(router)

    def deny_manage():
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="manage only")

    app.dependency_overrides[require_manage_access] = deny_manage
    app.dependency_overrides[require_chat_access] = lambda: None
    client = TestClient(app)

    assert client.get("/api/agents").status_code == 200
    assert client.post("/api/agents", json={"name": "Flight Booker"}).status_code == 403
    assert client.delete("/api/agents/cuga-default").status_code == 403


async def test_delete_cleanup_does_not_clobber_a_concurrent_supervisor_publish(monkeypatch):
    """The supervisor ref cleanup on delete is a read-modify-write, and ``save_config`` is blind —
    it appends MAX(version)+1 and never compares the version that was loaded. So the cleanup must
    run under ``agent_draft_lock(SUPERVISOR_AGENT_ID)``, the same lock the publish path holds across
    its own load-modify-write of that config.

    Without the lock, a publish landing between the cleanup's load and its save is silently reverted
    (or, as staged here, the cleanup's removal is): this test drives exactly that interleaving.
    """
    import asyncio

    from cuga.backend.server import agents_routes, events_bridge
    from cuga.backend.server.config_store import load_config, save_config
    from cuga.backend.server.manage_routes import helpers as manage_helpers
    from cuga.backend.server.manage_routes.helpers import agent_draft_lock
    from cuga.supervisor_utils.roster_seed import SUPERVISOR_AGENT_ID

    reset_config_db()
    monkeypatch.setattr(events_bridge, "events_enabled", lambda: True)

    async def _no_cache_invalidation(*_args, **_kwargs):
        return None

    monkeypatch.setattr(manage_helpers, "invalidate_agent_graph_cache", _no_cache_invalidation)

    await save_config({"agent": {"name": "Doomed"}}, agent_id="doomed")
    await save_config(
        {
            "agent": {"name": "Sup", "kind": "supervisor"},
            "supervisor": {"subAgents": [{"ref": "doomed"}, {"ref": "keeper"}], "planApproval": False},
        },
        agent_id=SUPERVISOR_AGENT_ID,
    )

    # A publish takes the lock and reads the config it is about to rewrite.
    lock = agent_draft_lock(SUPERVISOR_AGENT_ID)
    await lock.acquire()
    try:
        publishing, _ = await load_config(None, SUPERVISOR_AGENT_ID)

        task = asyncio.create_task(agents_routes.delete_agent("doomed", request=object()))
        # Let the delete get past delete_all_configs (observable: the agent's config is gone) and up
        # to the supervisor lock, where it must now block until the publish below has landed.
        for _ in range(400):
            await asyncio.sleep(0.005)
            gone, _ = await load_config(None, "doomed")
            if gone is None or task.done():
                break
        await asyncio.sleep(0.05)

        publishing["supervisor"]["planApproval"] = True
        await save_config(publishing, agent_id=SUPERVISOR_AGENT_ID)
    finally:
        lock.release()

    await task

    final, _ = await load_config(None, SUPERVISOR_AGENT_ID)
    refs = [s["ref"] for s in final["supervisor"]["subAgents"]]
    assert refs == ["keeper"], "the publish clobbered the cleanup — the deleted agent is still listed"
    assert final["supervisor"]["planApproval"] is True, "the cleanup wrote a stale snapshot over the publish"
