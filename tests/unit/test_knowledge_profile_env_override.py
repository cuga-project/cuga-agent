"""An explicit env override must beat the RAG profile.

The profile is deliberately the source of truth over ``settings.toml`` so that
switching ``rag_profile`` actually changes behaviour. An environment variable
is different in kind — it is an explicit operator action, not a default in a
config file.

Before this, every ``cuga start`` flag documented as "Sets
DYNACONF_KNOWLEDGE__…" was silently dead whenever a profile was active, which
is always (``rag_profile`` defaults to ``standard``). That left no working way
to lower ``embedding_batch_size`` on a memory-constrained deployment short of
editing TOML inside the image — the exact situation that OOM-killed a 4 GB pod.
"""

from __future__ import annotations

import pytest

from cuga.backend.knowledge.config import _profile_key

# (env var, profile key, profile value, settings value, override, expected, cast)
KNOBS = [
    ("DYNACONF_KNOWLEDGE__EMBEDDINGS__BATCH_SIZE", "batch_size", 128, 64, "16", 16, int),
    ("DYNACONF_KNOWLEDGE__EMBEDDINGS__CONCURRENCY", "concurrency", 4, 4, "2", 2, int),
    (
        "DYNACONF_KNOWLEDGE__ENGINE__VECTOR_INSERT_BATCH_SIZE",
        "vector_insert_batch_size",
        500,
        200,
        "100",
        100,
        int,
    ),
    ("DYNACONF_KNOWLEDGE__CHUNKING__CHUNK_SIZE", "chunk_size", 800, 1000, "400", 400, int),
    ("DYNACONF_KNOWLEDGE__CHUNKING__CHUNK_OVERLAP", "chunk_overlap", 100, 200, "50", 50, int),
    ("DYNACONF_KNOWLEDGE__DOCLING__PDF_MODE", "pdf_mode", "balanced", "accurate", "fast", "fast", None),
    ("DYNACONF_KNOWLEDGE__DOCLING__LAYOUT_ENGINE", "layout_engine", "auto", "auto", "onnx", "onnx", None),
]


@pytest.mark.unit
@pytest.mark.parametrize(("env", "key", "prof", "setting", "override", "expected", "cast"), KNOBS)
def test_env_override_beats_the_profile(monkeypatch, env, key, prof, setting, override, expected, cast):
    monkeypatch.setenv(env, override)
    got = _profile_key(env, {key: prof}, {key: setting}, key, "unused", cast)
    assert got == expected, f"{env} was ignored; got {got!r} instead of {expected!r}"


@pytest.mark.unit
@pytest.mark.parametrize(("env", "key", "prof", "setting", "override", "expected", "cast"), KNOBS)
def test_profile_still_wins_without_an_override(
    monkeypatch, env, key, prof, setting, override, expected, cast
):
    """No env set: the profile must remain authoritative over settings.toml."""
    monkeypatch.delenv(env, raising=False)
    assert _profile_key(env, {key: prof}, {key: setting}, key, "unused", cast) == prof


@pytest.mark.unit
def test_settings_fallback_when_profile_omits_the_key(monkeypatch):
    """A profile that leaves a key unset still falls through to settings.toml."""
    monkeypatch.delenv("DYNACONF_KNOWLEDGE__EMBEDDINGS__BATCH_SIZE", raising=False)
    assert (
        _profile_key(
            "DYNACONF_KNOWLEDGE__EMBEDDINGS__BATCH_SIZE", {}, {"batch_size": 64}, "batch_size", 32, int
        )
        == 64
    )


@pytest.mark.unit
def test_default_when_neither_profile_nor_settings_has_it(monkeypatch):
    monkeypatch.delenv("DYNACONF_KNOWLEDGE__EMBEDDINGS__BATCH_SIZE", raising=False)
    assert _profile_key("DYNACONF_KNOWLEDGE__EMBEDDINGS__BATCH_SIZE", {}, {}, "batch_size", 32, int) == 32


@pytest.mark.unit
@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_env_is_ignored_not_treated_as_a_value(monkeypatch, blank):
    """An empty var (common in k8s manifests) must not blank out the profile."""
    monkeypatch.setenv("DYNACONF_KNOWLEDGE__EMBEDDINGS__BATCH_SIZE", blank)
    assert (
        _profile_key(
            "DYNACONF_KNOWLEDGE__EMBEDDINGS__BATCH_SIZE", {"batch_size": 128}, {}, "batch_size", 64, int
        )
        == 128
    )


@pytest.mark.unit
def test_a_bad_override_fails_loudly(monkeypatch):
    """Silently falling back would hide a typo'd deployment knob."""
    monkeypatch.setenv("DYNACONF_KNOWLEDGE__EMBEDDINGS__BATCH_SIZE", "not-a-number")
    with pytest.raises(ValueError):
        _profile_key(
            "DYNACONF_KNOWLEDGE__EMBEDDINGS__BATCH_SIZE", {"batch_size": 128}, {}, "batch_size", 64, int
        )


@pytest.mark.unit
def test_end_to_end_from_settings_honours_the_override(monkeypatch):
    """The whole path, not just the helper: env -> KnowledgeConfig."""
    from cuga.config import settings
    from cuga.backend.knowledge.config import KnowledgeConfig

    monkeypatch.setenv("DYNACONF_KNOWLEDGE__EMBEDDINGS__BATCH_SIZE", "16")
    cfg = KnowledgeConfig.from_settings(settings)
    assert cfg.embedding_batch_size == 16, (
        f"profile ({cfg.rag_profile}) swallowed the operator override; got {cfg.embedding_batch_size}"
    )
