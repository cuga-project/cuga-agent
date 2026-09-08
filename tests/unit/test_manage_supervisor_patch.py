from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.server.auth import require_auth
from cuga.backend.server.config_store import load_draft, reset_config_db
from cuga.backend.server.manage_routes import router

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
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


def test_patch_draft_supervisor_persists_sub_agents():
    client = _client()

    response = client.patch(
        "/api/manage/config/draft/supervisor",
        params={"agent_id": "trip-supervisor"},
        json={
            "supervisor": {
                "subAgents": [
                    {"kind": "internal", "ref": "flight-booker"},
                    {"kind": "a2a", "name": "hotel-agent", "endpoint": "http://localhost:9000"},
                ],
                "planApproval": True,
            }
        },
    )

    assert response.status_code == 200

    draft = asyncio.run(load_draft("trip-supervisor"))
    assert draft is not None
    assert draft["supervisor"]["planApproval"] is True
    assert draft["supervisor"]["subAgents"] == [
        {"kind": "internal", "ref": "flight-booker"},
        {"kind": "a2a", "name": "hotel-agent", "endpoint": "http://localhost:9000"},
    ]


def test_patch_draft_supervisor_rejects_stale_save_seq():
    client = _client()
    first = client.patch(
        "/api/manage/config/draft/supervisor",
        params={"agent_id": "trip-supervisor"},
        json={
            "supervisor": {"subAgents": [{"kind": "internal", "ref": "crm-agent"}], "planApproval": False},
            "saveSeq": 1,
        },
    )
    second = client.patch(
        "/api/manage/config/draft/supervisor",
        params={"agent_id": "trip-supervisor"},
        json={"supervisor": {"subAgents": [], "planApproval": False}, "saveSeq": 2},
    )
    stale = client.patch(
        "/api/manage/config/draft/supervisor",
        params={"agent_id": "trip-supervisor"},
        json={
            "supervisor": {"subAgents": [{"kind": "internal", "ref": "crm-agent"}], "planApproval": False},
            "saveSeq": 1,
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert stale.status_code == 409
    assert stale.json()["detail"]["saveSeq"] == 2
    draft = asyncio.run(load_draft("trip-supervisor"))
    assert draft["supervisor"]["subAgents"] == []
    assert draft["supervisor"]["_saveSeq"] == 2


@pytest.mark.parametrize("save_seq", ["nope", True, 1.5])
def test_patch_draft_supervisor_rejects_non_integer_save_seq(save_seq):
    client = _client()

    response = client.patch(
        "/api/manage/config/draft/supervisor",
        params={"agent_id": "trip-supervisor"},
        json={"supervisor": {"subAgents": [], "planApproval": False}, "saveSeq": save_seq},
    )

    assert response.status_code == 400


def test_full_draft_save_does_not_clobber_newer_supervisor():
    client = _client()
    patched = client.patch(
        "/api/manage/config/draft/supervisor",
        params={"agent_id": "trip-supervisor"},
        json={
            "supervisor": {"subAgents": [{"kind": "internal", "ref": "crm-agent"}], "planApproval": True},
            "saveSeq": 2,
        },
    )
    assert patched.status_code == 200

    stale_full = client.post(
        "/api/manage/config/draft",
        params={"agent_id": "trip-supervisor"},
        json={
            "config": {
                "agent": {"name": "Trip", "kind": "supervisor"},
                "supervisor": {"subAgents": [], "planApproval": False, "_saveSeq": 1},
            }
        },
    )
    assert stale_full.status_code == 200
    draft = asyncio.run(load_draft("trip-supervisor"))
    assert draft["supervisor"]["subAgents"] == [{"kind": "internal", "ref": "crm-agent"}]
    assert draft["supervisor"]["planApproval"] is True
    assert draft["supervisor"]["_saveSeq"] == 2

    newer_full = client.post(
        "/api/manage/config/draft",
        params={"agent_id": "trip-supervisor"},
        json={
            "config": {
                "agent": {"name": "Trip", "kind": "supervisor"},
                "supervisor": {
                    "subAgents": [{"kind": "internal", "ref": "flight-booker"}],
                    "planApproval": False,
                    "_saveSeq": 3,
                },
            }
        },
    )
    assert newer_full.status_code == 200
    draft = asyncio.run(load_draft("trip-supervisor"))
    assert draft["supervisor"]["subAgents"] == [{"kind": "internal", "ref": "flight-booker"}]
    assert draft["supervisor"]["_saveSeq"] == 3


def test_patch_draft_supervisor_rejects_non_dict():
    client = _client()

    response = client.patch(
        "/api/manage/config/draft/supervisor",
        params={"agent_id": "trip-supervisor"},
        json={"supervisor": "not-a-dict"},
    )

    assert response.status_code == 400


def test_patch_draft_supervisor_404_when_registry_disabled(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: False,
    )
    client = _client()

    response = client.patch(
        "/api/manage/config/draft/supervisor",
        json={"supervisor": {"subAgents": [], "planApproval": False}},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Agent registry is disabled"
