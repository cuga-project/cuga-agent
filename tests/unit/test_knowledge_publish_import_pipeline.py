"""End-to-end audit of the publish → import → ingest pipeline for knowledge.

Goal: prove that EVERY field a user can set on the Embeddings panel
survives a publish/restart/import cycle correctly:
  - non-secret fields persist
  - secret fields are stripped before disk
  - on import, the running engine actually reflects the published config
  - legacy snapshots (missing new fields) load with sensible defaults
  - dim change between current engine and imported config triggers reindex
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine


def _full_cfg(**over):
    """A 'maxed-out' knowledge config that touches every UI-reachable field."""
    d = dict(
        enabled=True,
        persist_dir=Path(tempfile.mkdtemp(prefix="cuga-pubimp-")),
        embedding_provider="openai",
        embedding_model="Azure/text-embedding-3-small-1",
        embedding_api_key="sk-must-not-leak-XYZ",
        embedding_base_url="https://ete-litellm.bx.cloud9.ibm.com/v1",
        embedding_extra_params={"api_version": "2024-02-15"},
        use_gpu=True,
        docling_pdf_mode="fast",
        docling_layout_engine="transformers",
        chunk_size=800,
        chunk_overlap=100,
    )
    d.update(over)
    return KnowledgeConfig(**d)


def _build_app(eng: KnowledgeEngine | None = None):
    from cuga.backend.server.manage_routes import router
    from cuga.backend.server.auth import require_auth

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        knowledge_engine=eng, agent=None, config_version=None, tools_include_version=0
    )
    return app


# ============================================================
# 1. PUBLISH: every UI-reachable field survives to_dict
# ============================================================


def test_publish_snapshot_includes_every_ui_field():
    cfg = _full_cfg()
    snap = cfg.to_dict()
    for k in (
        "embedding_provider",
        "embedding_model",
        "embedding_base_url",
        "embedding_extra_params",
        "use_gpu",
        "docling_pdf_mode",
        "docling_layout_engine",
        "chunk_size",
        "chunk_overlap",
    ):
        assert k in snap, f"published snapshot dropped {k!r}"


def test_publish_snapshot_strips_only_secret_fields():
    cfg = _full_cfg()
    pub = cfg.to_dict(include_secrets=False)
    # Secret-stripped fields
    assert pub["embedding_api_key"] == ""
    # Non-secret fields preserved
    assert pub["embedding_provider"] == "openai"
    assert pub["embedding_model"] == "Azure/text-embedding-3-small-1"
    assert pub["embedding_base_url"] == "https://ete-litellm.bx.cloud9.ibm.com/v1"
    assert pub["embedding_extra_params"] == {"api_version": "2024-02-15"}
    assert pub["use_gpu"] is True
    assert pub["docling_pdf_mode"] == "fast"
    assert pub["docling_layout_engine"] == "transformers"


def test_publish_via_post_route_strips_keys_from_disk(monkeypatch):
    """The POST /api/manage/config flow must strip secrets BEFORE save_config."""
    from cuga.backend.server import config_store

    # Capture what gets passed to save_config
    captured = {}

    async def _capture_save(config, agent_id):
        captured["saved"] = config
        return "1"

    # The route imports save_config / save_draft inside the function, so
    # patching config_store is sufficient — the inline `from ... import` picks
    # up the patched version.
    monkeypatch.setattr(config_store, "save_config", _capture_save)
    monkeypatch.setattr(config_store, "save_draft", _no_op_async)
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes._apply_published_config",
        _no_op_async,
    )

    eng = KnowledgeEngine(_full_cfg())
    app = _build_app(eng)
    client = TestClient(app)
    payload = {
        "config": {
            "agent": {"name": "pub-strip-test"},
            "knowledge": {
                "enabled": True,
                "embedding_provider": "openai",
                "embedding_model": "Azure/text-embedding-3-small-1",
                "embedding_api_key": "sk-must-not-leak-XYZ",
                "embedding_base_url": "https://ete-litellm.bx.cloud9.ibm.com/v1",
            },
        }
    }
    resp = client.post("/api/manage/config", params={"agent_id": "pub-strip-test"}, json=payload)
    # Don't assert on status; the publish flow has many side-effects we mock
    # out. Just verify what hit the disk save call.
    assert "saved" in captured, f"save_config was not called (status={resp.status_code}, body={resp.text})"
    saved_kb = captured["saved"].get("knowledge", {})
    # KEY must be stripped on disk
    assert saved_kb.get("embedding_api_key", "") == "", (
        f"API key leaked to published config-store: {saved_kb.get('embedding_api_key')!r}"
    )
    # Non-secret fields must survive
    assert saved_kb.get("embedding_provider") == "openai"
    assert saved_kb.get("embedding_model") == "Azure/text-embedding-3-small-1"
    assert saved_kb.get("embedding_base_url") == "https://ete-litellm.bx.cloud9.ibm.com/v1"


async def _no_op_async(*args, **kwargs):
    return None


# ============================================================
# 2. IMPORT: published config → engine
# ============================================================


def test_import_published_config_reaches_engine_via_apply():
    """Publishing on machine A then loading on machine B must result in the
    engine using the published embedding settings — not whatever was in the
    target machine's settings.toml.

    This is what apply_knowledge_config does — verifying it round-trips."""
    snap = _full_cfg().to_dict(include_secrets=False)
    # Pretend we're importing on a fresh machine: engine starts with defaults.
    eng = KnowledgeEngine(
        KnowledgeConfig(
            enabled=True,
            persist_dir=Path(tempfile.mkdtemp()),
            embedding_provider="fastembed",  # default before import
        )
    )
    # Apply the published snapshot (this is the gap-fix path).
    eng.apply_knowledge_config(snap)
    assert eng._config.embedding_provider == "openai"
    assert eng._config.embedding_model == "Azure/text-embedding-3-small-1"
    assert eng._config.embedding_base_url == "https://ete-litellm.bx.cloud9.ibm.com/v1"
    assert eng._config.embedding_extra_params == {"api_version": "2024-02-15"}
    assert eng._config.docling_pdf_mode == "fast"
    assert eng._config.docling_layout_engine == "transformers"
    assert eng._config.use_gpu is True
    # Published config dropped the api_key — engine sees empty string (falls back to env).
    assert eng._config.embedding_api_key == ""


def test_import_legacy_snapshot_without_new_fields_defaults_cleanly():
    """Old published snapshots predate docling_layout_engine / extra_params /
    use_gpu. coerce_and_validate must default them, not crash."""
    legacy = {
        "embedding_provider": "fastembed",
        "embedding_model": "",
        "chunk_size": 1000,
        "chunk_overlap": 200,
        "metric_type": "COSINE",
        # NO use_gpu, NO docling_pdf_mode, NO docling_layout_engine,
        # NO embedding_extra_params — these were added later
    }
    cfg = KnowledgeConfig.coerce_and_validate(legacy)
    # Defaults applied
    assert cfg.use_gpu is True  # safe autodetect default
    assert cfg.docling_pdf_mode == "accurate"  # safe default
    assert cfg.docling_layout_engine == "auto"  # safe default
    assert cfg.embedding_extra_params == {}  # safe default


def test_import_then_apply_invalidates_docling_cache():
    """If imported config changes pdf_mode or layout_engine vs current,
    cached Docling converters must be cleared. Without this, the runtime
    keeps the old converter and the published change has no effect."""
    eng = KnowledgeEngine(
        KnowledgeConfig(
            enabled=True,
            persist_dir=Path(tempfile.mkdtemp()),
            embedding_provider="fastembed",
            docling_pdf_mode="accurate",
            docling_layout_engine="auto",
        )
    )
    # Prime the cache
    _ = eng._get_docling_converter()
    assert len(eng._docling_converters) == 1
    # Import a published config with different docling settings
    result = eng.apply_knowledge_config(
        {
            "docling_pdf_mode": "fast",
            "docling_layout_engine": "transformers",
        }
    )
    assert result["docling_changed"] is True
    # Cache must be cleared — next get_docling_converter rebuilds.
    assert len(eng._docling_converters) == 0


def test_import_dim_change_signals_reindex_recommended():
    """Importing a snapshot whose embedding model has a different dimension
    than the current engine's stored vectors must signal reindex."""
    eng = KnowledgeEngine(
        KnowledgeConfig(
            enabled=True,
            persist_dir=Path(tempfile.mkdtemp()),
            embedding_provider="fastembed",  # 384 dim
        )
    )
    eng._default_embedding_dim = 384  # simulate ingested data

    fake = MagicMock()
    fake.embed_query.return_value = [0.0] * 1536  # text-embedding-3-small dim
    with patch("cuga.backend.knowledge.engine.create_embeddings", return_value=fake):
        result = eng.apply_knowledge_config(
            {
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-small",
                "embedding_api_key": "k",
            }
        )
    assert result["dim_changed"] is True
    assert result["previous_dim"] == 384
    assert result["new_dim"] == 1536


# ============================================================
# 3. ROUND-TRIP: publish → import → publish again preserves all
# ============================================================


def test_round_trip_publish_import_publish_preserves_all_non_secret_fields():
    """A full cycle through publish + import + publish again must preserve
    every non-secret field. Catches type coercion bugs that lose data."""
    original = _full_cfg()
    pub1 = original.to_dict(include_secrets=False)

    # Import on a new engine
    eng = KnowledgeEngine(
        KnowledgeConfig(
            enabled=True,
            persist_dir=Path(tempfile.mkdtemp()),
            embedding_provider="fastembed",
        )
    )
    eng.apply_knowledge_config(pub1)

    # Re-publish from this engine
    pub2 = eng._config.to_dict(include_secrets=False)

    # Every non-secret field must match
    non_secret = set(pub1.keys()) - set(KnowledgeConfig._SECRET_FIELDS)
    for k in non_secret:
        if k == "persist_dir":
            continue  # local to each install
        assert pub1.get(k) == pub2.get(k), (
            f"round-trip lost field {k!r}: pub1={pub1.get(k)!r} pub2={pub2.get(k)!r}"
        )


def test_startup_applies_saved_knowledge_config_to_engine():
    """REGRESSION GUARD for the real bug found in audit:

    Before the fix, the server restarted, KnowledgeEngine was rebuilt purely
    from settings.toml, and ANY published knowledge config (provider, model,
    layout_engine, ...) was silently ignored. The user would publish a change,
    restart, and find their setting reset.

    The fix calls engine.apply_knowledge_config(saved_knowledge) during the
    startup `_apply_published_config` flow. This test pins that contract by
    simulating what the startup code does, end-to-end.
    """
    # 1. Simulate publish: user has openai+Azure config on machine A
    published_snapshot = _full_cfg().to_dict(include_secrets=False)

    # 2. Simulate restart on machine B: engine built from settings.toml defaults
    eng = KnowledgeEngine(
        KnowledgeConfig(
            enabled=True,
            persist_dir=Path(tempfile.mkdtemp()),
            embedding_provider="fastembed",  # settings.toml default
            embedding_model="",
            docling_pdf_mode="accurate",  # settings.toml default
            docling_layout_engine="auto",  # settings.toml default
        )
    )

    # 3. Server startup applies the published config
    # (mirrors the main.py code path I added)
    saved_kb = {k: v for k, v in published_snapshot.items() if not k.startswith("_")}
    result = eng.apply_knowledge_config(saved_kb)

    # 4. After "restart": engine reflects the PUBLISHED settings, not settings.toml
    assert eng._config.embedding_provider == "openai", (
        "Restart on machine B reverted to settings.toml fastembed — startup gap not fixed"
    )
    assert eng._config.embedding_model == "Azure/text-embedding-3-small-1"
    assert eng._config.embedding_base_url == "https://ete-litellm.bx.cloud9.ibm.com/v1"
    assert eng._config.embedding_extra_params == {"api_version": "2024-02-15"}
    assert eng._config.docling_pdf_mode == "fast"
    assert eng._config.docling_layout_engine == "transformers"
    assert eng._config.use_gpu is True
    # Secret stripped on disk → still "" after import (env-var fallback at embed time)
    assert eng._config.embedding_api_key == ""
    # No reindex flag because the engine's previous dim wasn't initialized
    # (fresh engine on machine B has never embedded anything).
    assert result["embedding_changed"] is True


def test_imported_published_snapshot_never_re_introduces_secrets():
    """Even after applying an import, the next publish snapshot must still
    strip secrets — defense against accidental 're-leak' bugs."""
    snap = _full_cfg().to_dict(include_secrets=False)
    eng = KnowledgeEngine(
        KnowledgeConfig(
            enabled=True,
            persist_dir=Path(tempfile.mkdtemp()),
            embedding_provider="fastembed",
        )
    )
    eng.apply_knowledge_config(snap)
    # The published snapshot we apply has key=""; engine ends up with "".
    assert eng._config.embedding_api_key == ""
    # If on this machine the user enters a key via UI, that key is in MEMORY
    # but the NEXT published snapshot must still strip it.
    eng._config.embedding_api_key = "sk-newly-typed-on-machine-B"
    next_pub = eng._config.to_dict(include_secrets=False)
    assert next_pub["embedding_api_key"] == ""
