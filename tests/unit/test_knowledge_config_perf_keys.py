"""Tests for the new ``[knowledge]`` config keys added by issue #183."""

from __future__ import annotations

from cuga.backend.knowledge.config import KnowledgeConfig


class _FakeSettings:
    """Minimal dict-like stand-in for the dynaconf settings object."""

    def __init__(self, knowledge: dict) -> None:
        self._kb = knowledge

    def get(self, key: str, default=None):
        if key == "knowledge":
            return self._kb
        return default


def test_from_settings_loads_perf_keys_from_toml() -> None:
    """settings.toml perf keys are the fallback when no profile sets them.

    Profile TOMLs (speed/standard/balanced/max_quality) own perf knobs in
    the 2026 profile rework, so to test that ``settings.toml`` -side
    loading still works we point ``rag_profile`` at a non-existent profile
    (which the loader treats as "no profile data" without raising) and
    assert settings.toml is then the source of truth.
    """
    settings = _FakeSettings(
        {
            "enabled": True,
            "search": {"rag_profile": "__no_such_profile__"},
            "embeddings": {
                "provider": "fastembed",
                "batch_size": 128,
                "concurrency": 8,
            },
            "engine": {
                "vector_insert_batch_size": 500,
            },
        }
    )
    cfg = KnowledgeConfig.from_settings(settings)
    assert cfg.embedding_batch_size == 128
    assert cfg.embedding_concurrency == 8
    assert cfg.vector_insert_batch_size == 500


def test_from_settings_defaults_when_keys_missing() -> None:
    """When neither profile nor settings.toml sets a perf knob, the
    dataclass default applies. Use a non-existent profile to bypass the
    standard.toml shipped-default values."""
    settings = _FakeSettings(
        {
            "enabled": True,
            "search": {"rag_profile": "__no_such_profile__"},
            "embeddings": {"provider": "fastembed"},
        }
    )
    cfg = KnowledgeConfig.from_settings(settings)
    assert cfg.embedding_batch_size == 64
    assert cfg.embedding_concurrency == 4
    assert cfg.vector_insert_batch_size == 200


def test_from_settings_default_profile_owns_perf_knobs() -> None:
    """The shipped 'standard' profile owns perf knobs — verify the Pareto
    matrix lands when from_settings runs with the default profile name."""
    settings = _FakeSettings({"enabled": True, "embeddings": {"provider": "fastembed"}})
    cfg = KnowledgeConfig.from_settings(settings)
    # These values come from configurations/knowledge/knowledge_profiles/standard.toml.
    # If you change that TOML, update this assertion deliberately.
    assert cfg.rag_profile == "standard"
    assert cfg.embedding_batch_size == 128
    assert cfg.vector_insert_batch_size == 500
    assert cfg.docling_pdf_mode == "balanced"
    assert cfg.search_hybrid_mode == "auto"
    assert cfg.rerank_enabled is False


def test_knowledge_config_dataclass_defaults() -> None:
    """Bare KnowledgeConfig() has the same defaults documented in settings.toml."""
    cfg = KnowledgeConfig()
    assert cfg.embedding_batch_size == 64
    assert cfg.embedding_concurrency == 4
    assert cfg.vector_insert_batch_size == 200


def test_perf_keys_round_trip_via_to_dict_and_coerce() -> None:
    """The new fields must survive snapshot/restore so a published config preserves tuning.

    ``to_dict()`` is what the manage-mode snapshot writes; ``coerce_and_validate``
    is what loads a published config back. Anything we don't round-trip here gets
    silently dropped on publish.
    """
    cfg = KnowledgeConfig(
        enabled=True,
        embedding_provider="openai",
        embedding_batch_size=256,
        embedding_concurrency=8,
        vector_insert_batch_size=500,
    )
    serialized = cfg.to_dict()
    # to_dict must include the new fields (auto-derived from dataclass, but
    # the assert catches anyone who later adds a manual allow-list).
    assert serialized["embedding_batch_size"] == 256
    assert serialized["embedding_concurrency"] == 8
    assert serialized["vector_insert_batch_size"] == 500

    restored = KnowledgeConfig.coerce_and_validate(serialized)
    assert restored.embedding_batch_size == 256
    assert restored.embedding_concurrency == 8
    assert restored.vector_insert_batch_size == 500


def test_perf_keys_coerce_strings_to_int() -> None:
    """coerce_and_validate must turn TOML/JSON string-ints into ints (defensive)."""
    restored = KnowledgeConfig.coerce_and_validate(
        {
            "embedding_batch_size": "128",
            "embedding_concurrency": "2",
            "vector_insert_batch_size": "100",
        }
    )
    assert restored.embedding_batch_size == 128
    assert restored.embedding_concurrency == 2
    assert restored.vector_insert_batch_size == 100


def test_docling_pdf_mode_default_is_accurate() -> None:
    assert KnowledgeConfig().docling_pdf_mode == "accurate"


def test_docling_pdf_mode_round_trips() -> None:
    """Published-config snapshot must preserve docling_pdf_mode across to_dict + restore."""
    for mode in ("fast", "balanced", "accurate"):
        cfg = KnowledgeConfig(docling_pdf_mode=mode)
        serialized = cfg.to_dict()
        assert serialized["docling_pdf_mode"] == mode
        restored = KnowledgeConfig.coerce_and_validate(serialized)
        assert restored.docling_pdf_mode == mode


def test_docling_pdf_mode_invalid_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="docling_pdf_mode"):
        KnowledgeConfig.coerce_and_validate({"docling_pdf_mode": "ultra"})


def test_docling_pdf_mode_loaded_from_settings() -> None:
    class _FakeSettings:
        def __init__(self, knowledge: dict) -> None:
            self._kb = knowledge

        def get(self, key: str, default=None):
            if key == "knowledge":
                return self._kb
            return default

    # Use a non-existent profile to bypass the standard.toml's pdf_mode
    # so we can verify settings.toml is honoured when no profile sets it.
    settings = _FakeSettings(
        {
            "enabled": True,
            "search": {"rag_profile": "__no_such_profile__"},
            "docling": {"pdf_mode": "fast"},
        }
    )
    cfg = KnowledgeConfig.from_settings(settings)
    assert cfg.docling_pdf_mode == "fast"


def test_invalid_batch_sizes_rejected() -> None:
    import pytest

    for field, bad in [
        ("embedding_batch_size", 0),
        ("embedding_concurrency", 0),
        ("vector_insert_batch_size", 0),
    ]:
        with pytest.raises(ValueError, match=field):
            KnowledgeConfig.coerce_and_validate({field: bad})
