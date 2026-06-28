"""Tests for GET /api/manage/knowledge/defaults — the endpoint that powers
the UI's per-section "Reset to defaults" buttons.

Contract:
  - Returns 200 with { defaults: { ... } }
  - Defaults match a fresh KnowledgeConfig() (modulo internal fields)
  - Secrets are blank in the response (never leak a key via factory defaults)
  - persist_dir is excluded (path is environment-specific)
  - Underscore-prefixed internal fields (_vector_config_hash, etc.) are stripped
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


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
    client = TestClient(app)
    r = client.get("/api/manage/knowledge/defaults")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "defaults" in body
    assert isinstance(body["defaults"], dict)


def test_defaults_endpoint_matches_KnowledgeConfig_factory_defaults():
    from cuga.backend.knowledge.config import KnowledgeConfig

    app = _build_app()
    client = TestClient(app)
    body = client.get("/api/manage/knowledge/defaults").json()
    defaults = body["defaults"]

    factory = KnowledgeConfig().to_dict(include_secrets=False)
    factory = {k: v for k, v in factory.items() if not k.startswith("_")}

    # Coerce Path values to str for JSON comparison
    def _normalize(v):
        from pathlib import Path as _P

        return str(v) if isinstance(v, _P) else v

    factory = {k: _normalize(v) for k, v in factory.items()}

    assert defaults == factory, f"endpoint returned {set(defaults) ^ set(factory)} diff vs factory"


def test_defaults_endpoint_blanks_secret_fields():
    """Even if someone later flips include_secrets, the endpoint must NOT leak a key."""
    app = _build_app()
    client = TestClient(app)
    defaults = client.get("/api/manage/knowledge/defaults").json()["defaults"]
    from cuga.backend.knowledge.config import KnowledgeConfig

    for k in KnowledgeConfig._SECRET_FIELDS:
        assert defaults.get(k, "") == "", f"secret field {k!r} non-empty in defaults: {defaults.get(k)!r}"


def test_defaults_endpoint_excludes_persist_dir():
    """persist_dir is environment-specific and would leak the server's filesystem layout."""
    app = _build_app()
    client = TestClient(app)
    defaults = client.get("/api/manage/knowledge/defaults").json()["defaults"]
    assert "persist_dir" not in defaults


def test_defaults_endpoint_excludes_internal_fields():
    """Underscore-prefixed fields are implementation details — strip them."""
    app = _build_app()
    client = TestClient(app)
    defaults = client.get("/api/manage/knowledge/defaults").json()["defaults"]
    for k in defaults:
        assert not k.startswith("_"), f"internal field leaked: {k!r}"


def test_defaults_contain_new_ui_fields():
    """Make sure the new fields surfaced by the UI's reset buttons are present —
    fast feedback if a future refactor drops one accidentally."""
    app = _build_app()
    client = TestClient(app)
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
    client = TestClient(app)
    r1 = client.get("/api/manage/knowledge/defaults").json()["defaults"]
    r2 = client.get("/api/manage/knowledge/defaults").json()["defaults"]
    assert r1 == r2
