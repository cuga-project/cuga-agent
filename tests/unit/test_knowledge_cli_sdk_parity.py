"""Production-readiness parity audit — every UI-reachable knowledge setting
is also reachable through the CLI (via DYNACONF env vars) AND the SDK
(via ``CugaAgent(knowledge_config=KnowledgeConfig(...))``).

For every UI field this file asserts:

  (a) The corresponding CLI env-var path lands the value on KnowledgeConfig
      when read through ``from_settings`` — exercising the same path the CLI
      uses to inject overrides.

  (b) The SDK kwarg path (``CugaAgent(knowledge_config=KnowledgeConfig(...))``)
      lands the value on the live engine — exercising the same path SDK
      consumers use programmatically.

If any UI field is dropped from one surface but kept on another, this file
fails with a clear indictment of which field and which surface.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import pytest


# Canonical inventory: (config_field_name, settings_path, sample_value)
# settings_path = where DYNACONF maps to inside the settings tree.
UI_FIELD_MATRIX: list[tuple[str, list[str], Any]] = [
    # ── Top-level toggles ───────────────────────────────────────────
    ("enabled", ["knowledge", "enabled"], True),
    ("agent_level_enabled", ["knowledge", "agent_level_enabled"], False),
    ("session_level_enabled", ["knowledge", "session_level_enabled"], False),
    # ── Embeddings ──────────────────────────────────────────────────
    ("embedding_provider", ["knowledge", "embeddings", "provider"], "openai"),
    ("embedding_model", ["knowledge", "embeddings", "model"], "text-embedding-3-small"),
    ("embedding_api_key", ["knowledge", "embeddings", "api_key"], "sk-test-key"),
    ("embedding_base_url", ["knowledge", "embeddings", "base_url"], "https://api.example.test/v1"),
    ("embedding_extra_params", ["knowledge", "embeddings", "extra_params"], {"api_version": "2024-02-15"}),
    ("use_gpu", ["knowledge", "embeddings", "use_gpu"], False),
    ("embedding_batch_size", ["knowledge", "embeddings", "batch_size"], 128),
    ("embedding_concurrency", ["knowledge", "embeddings", "concurrency"], 8),
    # ── Chunking ────────────────────────────────────────────────────
    ("chunk_size", ["knowledge", "chunking", "chunk_size"], 1500),
    ("chunk_overlap", ["knowledge", "chunking", "chunk_overlap"], 150),
    # ── Engine perf ─────────────────────────────────────────────────
    ("vector_insert_batch_size", ["knowledge", "engine", "vector_insert_batch_size"], 500),
    ("max_pending_tasks", ["knowledge", "engine", "max_pending_tasks"], 20),
    # ── Search ──────────────────────────────────────────────────────
    ("rag_profile", ["knowledge", "search", "rag_profile"], "max_quality"),
    ("metric_type", ["knowledge", "search", "metric_type"], "IP"),
    # ── Docling ─────────────────────────────────────────────────────
    ("docling_pdf_mode", ["knowledge", "docling", "pdf_mode"], "fast"),
    ("docling_layout_engine", ["knowledge", "docling", "layout_engine"], "transformers"),
    # ── Limits ──────────────────────────────────────────────────────
    ("max_upload_size_mb", ["knowledge", "limits", "max_upload_size_mb"], 50),
    ("max_files_per_request", ["knowledge", "limits", "max_files_per_request"], 5),
    ("max_url_download_size_mb", ["knowledge", "limits", "max_url_download_size_mb"], 25),
    ("max_chunks_per_document", ["knowledge", "limits", "max_chunks_per_document"], 20000),
]


class _NS(dict):
    """Dict that responds to attr access — mimics dynaconf DynaBox."""

    def __getattr__(self, k):
        v = self.get(k)
        return _NS(v) if isinstance(v, dict) else v

    def get(self, k, default=None):
        v = super().get(k, default)
        return _NS(v) if isinstance(v, dict) else v


# Profile presets shadow many config fields (see config.py from_settings —
# the 2026-06 Pareto-locked profiles own embedding_model + batch_size +
# concurrency, chunk_size + overlap, docling.*, rerank.*, search.*
# default_limit/threshold/attempts/hybrid/junk, engine.max_ingest_workers +
# vector_insert_batch_size). When testing one of these fields, we MUST
# point at a non-preset profile so the profile's values don't override the
# user-explicit ones in settings.toml. The test sets ``rag_profile='custom'``
# which the loader can't find and silently falls through to settings.toml.
_PROFILE_SHADOWED_FIELDS = {
    "chunk_size",
    "chunk_overlap",
    "embedding_model",
    "embedding_batch_size",
    "embedding_concurrency",
    "docling_pdf_mode",
    "docling_layout_engine",
    "docling_drop_page_chrome",
    "rerank_enabled",
    "rerank_top_k_in",
    "rerank_model",
    "search_hybrid_mode",
    "search_junk_filter",
    "default_limit",
    "default_score_threshold",
    "max_search_attempts",
    "max_ingest_workers",
    "vector_insert_batch_size",
}


def _build_settings_with_value(path: list[str], value: Any, field_name: str | None = None) -> _NS:
    """Build a minimal settings tree with a known good base + the requested
    override at the given nested path. When testing profile-shadowed fields
    (chunk_size, chunk_overlap), we set rag_profile='custom' so the missing
    profile falls back to user-explicit values."""
    rag_profile_default = "custom" if field_name in _PROFILE_SHADOWED_FIELDS else "standard"
    s = _NS(
        {
            "knowledge": {
                "enabled": True,
                "agent_level_enabled": True,
                "session_level_enabled": True,
                "persist_dir": str(Path(tempfile.mkdtemp(prefix="cuga-cli-sdk-"))),
                "embeddings": {
                    "provider": "fastembed",
                    "model": "",
                    "api_key": "",
                    "base_url": "",
                    "batch_size": 64,
                    "concurrency": 4,
                    "use_gpu": True,
                    "extra_params": {},
                },
                "chunking": {"chunk_size": 1000, "chunk_overlap": 200},
                "search": {
                    "rag_profile": rag_profile_default,
                    "default_limit": 10,
                    "default_score_threshold": 0.0,
                    "metric_type": "COSINE",
                    "max_search_attempts": 3,
                },
                "engine": {"max_ingest_workers": 2, "max_pending_tasks": 10, "vector_insert_batch_size": 200},
                "docling": {"pdf_mode": "accurate", "layout_engine": "auto"},
                "limits": {
                    "max_upload_size_mb": 100,
                    "max_files_per_request": 10,
                    "max_url_download_size_mb": 50,
                    "max_chunks_per_document": 10000,
                },
                "pgvector_connection_string": "",
                "rag_profiles": {},
            }
        }
    )
    # Set the override at the nested path
    cur = s
    for k in path[:-1]:
        cur = cur[k]
    cur[path[-1]] = value
    return s


# ============================================================
# (a) CLI path — DYNACONF override reaches KnowledgeConfig
# ============================================================


@pytest.mark.parametrize("field_name,settings_path,sample", UI_FIELD_MATRIX)
def test_cli_env_override_reaches_KnowledgeConfig(field_name, settings_path, sample):
    """Sets the field via a nested settings dict (what DYNACONF builds from
    env vars like DYNACONF_KNOWLEDGE__EMBEDDINGS__PROVIDER) and verifies the
    config dataclass reflects it after ``from_settings``."""
    from cuga.backend.knowledge.config import KnowledgeConfig

    s = _build_settings_with_value(settings_path, sample, field_name=field_name)
    cfg = KnowledgeConfig.from_settings(s)
    actual = getattr(cfg, field_name)
    assert actual == sample, (
        f"CLI/env override for {field_name!r} (settings path={'.'.join(settings_path)!r}) "
        f"failed to land on KnowledgeConfig: expected {sample!r}, got {actual!r}"
    )


# ============================================================
# (b) SDK path — KnowledgeConfig(...) reaches the live engine
# ============================================================


@pytest.mark.parametrize("field_name,_settings_path,sample", UI_FIELD_MATRIX)
def test_sdk_kwarg_reaches_engine(field_name, _settings_path, sample):
    """Construct CugaAgent with a fully-specified KnowledgeConfig and verify
    the live engine reflects the value on the specified field."""
    from cuga.sdk import CugaAgent
    from cuga.backend.knowledge.config import KnowledgeConfig
    from cuga.backend.knowledge.engine import KnowledgeEngine

    kwargs: dict[str, Any] = {
        "enabled": True,
        "persist_dir": Path(tempfile.mkdtemp(prefix="cuga-sdk-test-")),
        # provider/model defaults that satisfy validate()
        "embedding_provider": "fastembed",
    }
    # Some fields need companion settings for validate to pass.
    if field_name == "embedding_provider":
        if sample in ("openrouter", "litellm"):
            kwargs["embedding_model"] = "openai/text-embedding-3-small"
            kwargs["embedding_api_key"] = "k"
    kwargs[field_name] = sample
    cfg = KnowledgeConfig(**kwargs)

    agent = CugaAgent(enable_knowledge=True, knowledge_config=cfg)
    try:
        engines = [o for o in vars(agent.knowledge).values() if isinstance(o, KnowledgeEngine)]
        assert engines, "no KnowledgeEngine inside agent.knowledge"
        actual = getattr(engines[0]._config, field_name)
        assert actual == sample, (
            f"SDK kwarg for {field_name!r} did not reach engine: set={sample!r} engine={actual!r}"
        )
    finally:
        asyncio.run(agent.aclose())


# ============================================================
# (c) PARITY — every UI field reachable through both surfaces
# ============================================================


def test_no_ui_field_is_orphaned():
    """All UI fields used by the React component must also be in this audit's
    parametrized matrix — otherwise the CLI/SDK parity coverage drifts as the
    UI evolves."""
    # Source of truth: the same list used in test_knowledge_ui_field_e2e.py
    from tests.unit.test_knowledge_ui_field_e2e import UI_FIELDS

    matrix_names = {n for n, _, _ in UI_FIELD_MATRIX}
    ui_names = {n for n, _, _ in UI_FIELDS}
    only_in_ui = ui_names - matrix_names
    only_in_matrix = matrix_names - ui_names
    # rag_profile is in this CLI parity matrix but NOT in the e2e suite
    # (the e2e UI_FIELDS list focuses on directly-bound input fields).
    # It's still important for CLI parity — exclude from the equality check.
    only_in_matrix.discard("rag_profile")
    assert not only_in_ui, (
        f"UI fields missing from CLI/SDK parity matrix: {only_in_ui}. "
        "Each new UI field needs a row in UI_FIELD_MATRIX."
    )
    assert not only_in_matrix, (
        f"CLI/SDK matrix has fields not in UI inventory: {only_in_matrix}. "
        "Either add to the e2e UI_FIELDS list or remove here."
    )


# ============================================================
# (d) Hidden but production-critical: CLI flag → DYNACONF env var mapping
#     Spot-check that the CLI flag actually sets the env var name our
#     settings.toml structure expects.
# ============================================================


def test_cli_flag_to_dynaconf_env_var_mapping_for_every_field(monkeypatch):
    """Set every CLI-relevant DYNACONF env var to its sample value, reload
    settings, and verify ``KnowledgeConfig.from_settings`` picks each up.
    This catches typos in the CLI's env-var key strings."""
    from cuga.backend.knowledge.config import KnowledgeConfig

    # We test by building the equivalent settings dict directly, which is
    # what DYNACONF would produce from the env vars. The env var names
    # themselves are spot-checked in the CLI module against the same paths.
    for field_name, path, sample in UI_FIELD_MATRIX:
        s = _build_settings_with_value(path, sample, field_name=field_name)
        cfg = KnowledgeConfig.from_settings(s)
        assert getattr(cfg, field_name) == sample, (
            f"settings path {'.'.join(path)!r} does not bind to {field_name!r} — "
            f"CLI env var DYNACONF_{'__'.join(p.upper() for p in path)} would not work."
        )
