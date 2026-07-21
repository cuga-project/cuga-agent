# tests/unit/test_session_settings_routes.py
"""HTTP-layer tests for GET/PATCH /api/knowledge/session/settings.

Complements test_session_citation_override.py (which tests the
_apply_session_settings_patch helper directly) by exercising the FastAPI
handlers: header requirements, provider availability, ownership
enforcement, and the PATCH -> GET round-trip. Uses the same lightweight
TestClient pattern as test_knowledge_routes.py — the session-settings
routes never touch the engine, so only ``knowledge_provider`` is stubbed
(with the real in-memory SessionProvider).
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from cuga.backend.knowledge.auth import KnowledgeIdentity, require_internal_or_auth
from cuga.backend.knowledge.routes import knowledge_router
from cuga.backend.knowledge.session_provider import SessionProvider
import pytest

pytestmark = pytest.mark.unit


def _make_app(provider, *, user_id: str | None = None, tenant_id: str | None = None):
    """App with the knowledge router, an identity override, and *provider*.

    ``user_id``/``tenant_id`` mirror auth.py behavior: both are None in
    internal/no-auth modes (ownership not enforced) and non-empty in
    external OIDC mode (ownership enforced).
    """

    async def _identity_override(request: Request) -> KnowledgeIdentity:
        return KnowledgeIdentity(
            user_id=user_id,
            tenant_id=tenant_id,
            agent_id="cuga-default",
            thread_id=request.headers.get("X-Thread-ID"),
            auth_mode="external",
        )

    app = FastAPI()
    app.include_router(knowledge_router)
    app.dependency_overrides[require_internal_or_auth] = _identity_override
    app.state.app_state = SimpleNamespace(
        knowledge_engine=None,
        knowledge_provider=provider,
    )
    return app


def test_get_requires_thread_id_header():
    client = TestClient(_make_app(SessionProvider()))
    resp = client.get("/api/knowledge/session/settings")
    assert resp.status_code == 400
    assert "X-Thread-ID" in resp.json()["detail"]


def test_patch_requires_thread_id_header():
    client = TestClient(_make_app(SessionProvider()))
    resp = client.patch("/api/knowledge/session/settings", json={"citations_enabled": False})
    assert resp.status_code == 400
    assert "X-Thread-ID" in resp.json()["detail"]


def test_get_returns_503_when_provider_missing():
    client = TestClient(_make_app(None))
    resp = client.get(
        "/api/knowledge/session/settings",
        headers={"X-Thread-ID": "thread-1"},
    )
    assert resp.status_code == 503


def test_patch_returns_503_when_provider_missing():
    client = TestClient(_make_app(None))
    resp = client.patch(
        "/api/knowledge/session/settings",
        headers={"X-Thread-ID": "thread-1"},
        json={"citations_enabled": False},
    )
    assert resp.status_code == 503


def test_patch_then_get_round_trip():
    provider = SessionProvider()
    client = TestClient(_make_app(provider))

    # Fresh session: GET returns empty overrides (no session yet).
    resp = client.get(
        "/api/knowledge/session/settings",
        headers={"X-Thread-ID": "thread-rt"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"thread_id": "thread-rt", "overrides": {}}

    # PATCH persists the override and echoes the new state.
    resp = client.patch(
        "/api/knowledge/session/settings",
        headers={"X-Thread-ID": "thread-rt"},
        json={"citations_enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["overrides"] == {"citations_enabled": False}

    # GET reads back what PATCH wrote.
    resp = client.get(
        "/api/knowledge/session/settings",
        headers={"X-Thread-ID": "thread-rt"},
    )
    assert resp.status_code == 200
    assert resp.json()["overrides"] == {"citations_enabled": False}


def test_patch_rejects_unknown_keys_with_400():
    client = TestClient(_make_app(SessionProvider()))
    resp = client.patch(
        "/api/knowledge/session/settings",
        headers={"X-Thread-ID": "thread-1"},
        json={"evil": True},
    )
    assert resp.status_code == 400
    assert "citations_enabled" in resp.json()["detail"]


def test_patch_malformed_json_body_returns_400():
    client = TestClient(_make_app(SessionProvider()))
    resp = client.patch(
        "/api/knowledge/session/settings",
        headers={"X-Thread-ID": "thread-1", "Content-Type": "application/json"},
        content="{not json",
    )
    assert resp.status_code == 400
    assert "JSON object" in resp.json()["detail"]


def test_patch_non_dict_json_body_returns_400():
    client = TestClient(_make_app(SessionProvider()))
    resp = client.patch(
        "/api/knowledge/session/settings",
        headers={"X-Thread-ID": "thread-1"},
        json=[{"citations_enabled": False}],
    )
    assert resp.status_code == 400
    assert "JSON object" in resp.json()["detail"]


def test_ownership_denied_for_other_users_session():
    """A session created by alice must not be readable/patchable by bob.

    Ownership is only enforced when identity carries user_id AND tenant_id
    (external OIDC auth) — mirrors resolve_collection's session-scope rule.
    """
    provider = SessionProvider()
    provider.get_or_create_session("thread-owned", user_id="alice", tenant_id="t1")

    client = TestClient(_make_app(provider, user_id="bob", tenant_id="t1"))

    resp = client.get(
        "/api/knowledge/session/settings",
        headers={"X-Thread-ID": "thread-owned"},
    )
    assert resp.status_code == 403

    resp = client.patch(
        "/api/knowledge/session/settings",
        headers={"X-Thread-ID": "thread-owned"},
        json={"citations_enabled": False},
    )
    assert resp.status_code == 403
    # And the owned session was not mutated.
    assert provider.get_session("thread-owned").overrides == {}


def test_owner_can_patch_own_session():
    provider = SessionProvider()
    provider.get_or_create_session("thread-owned", user_id="alice", tenant_id="t1")

    client = TestClient(_make_app(provider, user_id="alice", tenant_id="t1"))
    resp = client.patch(
        "/api/knowledge/session/settings",
        headers={"X-Thread-ID": "thread-owned"},
        json={"citations_enabled": False},
    )
    assert resp.status_code == 200
    assert provider.get_session("thread-owned").overrides == {"citations_enabled": False}
