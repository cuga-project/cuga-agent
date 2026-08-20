"""A missing or malformed JSON body must be 400, never 500.

The 400 detail deliberately reuses the exact string ``patch_session_settings``
already returned, so this fix changes status codes only — no shipped message
text moves under a client that might be matching on it.

``await request.json()`` raises ``JSONDecodeError`` when the body is absent or
not JSON. Unhandled, that escapes as an ASGI 500 with a full traceback — the
wrong contract for bad client input, and log noise that buries real failures
(#689). Every knowledge endpoint that reads a body now goes through
``_json_body``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from cuga.backend.knowledge.auth import KnowledgeIdentity, require_internal_or_auth
from cuga.backend.knowledge.routes import knowledge_router


class _Engine:
    """Enough engine for the body check, which runs before any real work."""

    def __init__(self) -> None:
        self._config = SimpleNamespace(enabled=True, max_files_per_request=5, default_limit=10)

    async def health(self, collection: str | None = None) -> dict:
        return {"status": "healthy"}

    async def reindex(self, collection: str) -> dict:
        return {"status": "started", "collection": collection}


async def _identity(request: Request) -> KnowledgeIdentity:
    return KnowledgeIdentity(
        user_id=None,
        tenant_id=None,
        agent_id="cuga-default",
        thread_id=request.headers.get("X-Thread-ID"),
        auth_mode="external",
    )


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(knowledge_router)
    app.dependency_overrides[require_internal_or_auth] = _identity
    app.state.app_state = SimpleNamespace(knowledge_engine=_Engine(), knowledge_provider=None)
    return TestClient(app)


# (method, path) for every body-reading endpoint on the knowledge router.
BODY_ENDPOINTS = [
    ("POST", "/api/knowledge/search"),
    ("POST", "/api/knowledge/documents/url"),
    ("DELETE", "/api/knowledge/documents"),
]


@pytest.mark.unit
@pytest.mark.parametrize(("method", "path"), BODY_ENDPOINTS)
def test_missing_body_is_400_not_500(client, method, path):
    r = client.request(method, path)
    assert r.status_code == 400, f"{method} {path} -> {r.status_code}: {r.text[:200]}"
    assert r.json()["detail"] == "request body must be a JSON object"


@pytest.mark.unit
@pytest.mark.parametrize(("method", "path"), BODY_ENDPOINTS)
def test_malformed_body_is_400_not_500(client, method, path):
    r = client.request(method, path, content=b"not json at all", headers={"Content-Type": "application/json"})
    assert r.status_code == 400, f"{method} {path} -> {r.status_code}: {r.text[:200]}"
    assert r.json()["detail"] == "request body must be a JSON object"


@pytest.mark.unit
@pytest.mark.parametrize(("method", "path"), BODY_ENDPOINTS)
def test_non_object_json_is_400_not_500(client, method, path):
    """A bare list or scalar parses fine but is not addressable with .get()."""
    r = client.request(method, path, json=[1, 2, 3])
    assert r.status_code == 400, f"{method} {path} -> {r.status_code}: {r.text[:200]}"
    assert r.json()["detail"] == "request body must be a JSON object"


@pytest.mark.unit
def test_reindex_still_accepts_an_empty_body(client):
    """``allow_empty`` keeps the documented no-body call working.

    Regression guard: the fix must not turn a valid empty-body reindex into a
    400. It previously special-cased content-length for exactly this.
    """
    r = client.post("/api/knowledge/reindex")
    assert r.status_code != 400, f"empty-body reindex regressed to 400: {r.text[:200]}"


@pytest.mark.unit
def test_reindex_rejects_a_malformed_body(client):
    """The old content-length guard let a malformed body through to a 500."""
    r = client.post(
        "/api/knowledge/reindex",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400, f"-> {r.status_code}: {r.text[:200]}"
    assert r.json()["detail"] == "request body must be a JSON object"


@pytest.mark.unit
def test_whitespace_only_body_is_treated_as_empty(client):
    """A proxy that pads the body must not produce a 500."""
    r = client.request(
        "DELETE",
        "/api/knowledge/documents",
        content=b"   \n  ",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "request body must be a JSON object"
