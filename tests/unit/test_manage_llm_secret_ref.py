"""GET /api/manage/config keeps vault refs; PATCH /draft/llm must not wipe them."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.server.auth import require_auth, require_manage_access
from cuga.backend.server.config_store import load_draft, reset_config_db, save_draft
from cuga.backend.server.manage_routes import router

VAULT_REF = "vault://secret/openai-api-key#value"


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.dependency_overrides[require_manage_access] = lambda: None
    return TestClient(app)


@pytest.mark.unit
def test_get_draft_config_preserves_vault_llm_ref():
    reset_config_db()
    asyncio.run(
        save_draft(
            {
                "llm": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "api_key": VAULT_REF,
                }
            },
            "cuga-default",
        )
    )
    client = _make_client()
    resp = client.get("/api/manage/config", params={"draft": "1", "agent_id": "cuga-default"})
    assert resp.status_code == 200, resp.text
    llm = resp.json()["config"]["llm"]
    assert llm["api_key"] == VAULT_REF


@pytest.mark.unit
def test_patch_llm_empty_api_key_preserves_stored_vault_ref():
    reset_config_db()
    asyncio.run(
        save_draft(
            {
                "llm": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "api_key": VAULT_REF,
                }
            },
            "cuga-default",
        )
    )
    client = _make_client()
    patch = client.patch(
        "/api/manage/config/draft/llm",
        params={"agent_id": "cuga-default"},
        json={"llm": {"provider": "openai", "model": "gpt-4o", "api_key": ""}},
    )
    assert patch.status_code == 200, patch.text

    draft = asyncio.run(load_draft("cuga-default"))
    assert draft is not None
    assert draft["llm"]["api_key"] == VAULT_REF
    assert draft["llm"]["model"] == "gpt-4o"

    get_resp = client.get("/api/manage/config", params={"draft": "1", "agent_id": "cuga-default"})
    assert get_resp.status_code == 200
    assert get_resp.json()["config"]["llm"]["api_key"] == VAULT_REF
