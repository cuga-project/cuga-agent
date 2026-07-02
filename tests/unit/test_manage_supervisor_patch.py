from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.server.auth import require_auth
from cuga.backend.server.config_store import load_draft, reset_config_db
from cuga.backend.server.manage_routes import router


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


def test_patch_draft_supervisor_rejects_non_dict():
    client = _client()

    response = client.patch(
        "/api/manage/config/draft/supervisor",
        params={"agent_id": "trip-supervisor"},
        json={"supervisor": "not-a-dict"},
    )

    assert response.status_code == 400
