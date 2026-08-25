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
