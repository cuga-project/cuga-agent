"""Tests for GET /api/manage/knowledge/defaults — the endpoint that powers
the UI's per-section "Reset to defaults" buttons.

Contract:
  - Returns 200 with { defaults: { ... } }
  - Defaults are the selected profile's resolved config (``?profile=``, default ``standard``)
  - An unknown profile is a 400
  - Secrets are blank in the response (never leak a key via factory defaults)
  - persist_dir is excluded (path is environment-specific)
  - Underscore-prefixed internal fields (_vector_config_hash, etc.) are stripped
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def _build_app():
    from cuga.backend.server.manage_routes import router
    from cuga.backend.server.auth import require_auth

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace()
    return app


def test_defaults_endpoint_returns_200_with_defaults_dict():
    app = _build_app()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost")
    r = client.get("/api/manage/knowledge/defaults")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "defaults" in body
    assert isinstance(body["defaults"], dict)


def test_defaults_endpoint_matches_the_standard_profile_by_default():
    from cuga.backend.knowledge.config import KnowledgeConfig

    app = _build_app()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost")
    defaults = client.get("/api/manage/knowledge/defaults").json()["defaults"]

    expected = KnowledgeConfig.coerce_and_validate({"rag_profile": "standard"}).to_dict(include_secrets=False)
    expected = {
        k: (str(v) if hasattr(v, "as_posix") else v) for k, v in expected.items() if not k.startswith("_")
    }

    assert defaults == expected, f"endpoint returned {set(defaults) ^ set(expected)} diff vs standard"


@pytest.mark.parametrize(
    ("profile", "chunk_size", "chunk_overlap"),
    [("speed", 1000, 100), ("standard", 800, 100), ("balanced", 800, 150), ("max_quality", 1000, 200)],
)
def test_defaults_endpoint_returns_each_profiles_values(profile, chunk_size, chunk_overlap):
    """Reset must restore what the selected profile sets, not the bare dataclass values."""
    app = _build_app()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost")
    defaults = client.get(f"/api/manage/knowledge/defaults?profile={profile}").json()["defaults"]

    assert defaults["rag_profile"] == profile
    assert (defaults["chunk_size"], defaults["chunk_overlap"]) == (chunk_size, chunk_overlap)


def test_defaults_endpoint_rejects_an_unknown_profile():
    app = _build_app()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost")
    r = client.get("/api/manage/knowledge/defaults?profile=custom")
    assert r.status_code == 400


def test_defaults_endpoint_blanks_secret_fields():
    """Even if someone later flips include_secrets, the endpoint must NOT leak a key."""
    app = _build_app()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost")
    defaults = client.get("/api/manage/knowledge/defaults").json()["defaults"]
    from cuga.backend.knowledge.config import KnowledgeConfig

    for k in KnowledgeConfig._SECRET_FIELDS:
        assert defaults.get(k, "") == "", f"secret field {k!r} non-empty in defaults: {defaults.get(k)!r}"


def test_defaults_endpoint_excludes_persist_dir():
    """persist_dir is environment-specific and would leak the server's filesystem layout."""
    app = _build_app()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost")
    defaults = client.get("/api/manage/knowledge/defaults").json()["defaults"]
    assert "persist_dir" not in defaults


def test_defaults_endpoint_excludes_internal_fields():
    """Underscore-prefixed fields are implementation details — strip them."""
    app = _build_app()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost")
    defaults = client.get("/api/manage/knowledge/defaults").json()["defaults"]
    for k in defaults:
        assert not k.startswith("_"), f"internal field leaked: {k!r}"


def test_defaults_contain_new_ui_fields():
    """Make sure the new fields surfaced by the UI's reset buttons are present —
    fast feedback if a future refactor drops one accidentally."""
    app = _build_app()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost")
    defaults = client.get("/api/manage/knowledge/defaults").json()["defaults"]
    for field in (
        "embedding_provider",
        "embedding_model",
        "embedding_base_url",
        "embedding_extra_params",
        "use_gpu",
        "embedding_batch_size",
        "embedding_concurrency",
        "vector_insert_batch_size",
        "chunk_size",
        "chunk_overlap",
        "docling_pdf_mode",
        "docling_layout_engine",
        "metric_type",
        "max_upload_size_mb",
        "max_files_per_request",
        "max_url_download_size_mb",
        "max_chunks_per_document",
        "max_pending_tasks",
    ):
        assert field in defaults, f"UI reset relies on {field!r} — missing from /defaults response"


def test_defaults_endpoint_idempotent():
    """Calling twice yields identical result — no hidden state, safe to cache client-side."""
    app = _build_app()
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost")
    r1 = client.get("/api/manage/knowledge/defaults").json()["defaults"]
    r2 = client.get("/api/manage/knowledge/defaults").json()["defaults"]
    assert r1 == r2
