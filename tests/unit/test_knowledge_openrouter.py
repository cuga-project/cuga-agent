"""Tests for OpenRouter as a first-class embeddings provider.

OpenRouter is a single-key gateway to many embeddings models. Internally we
reuse ``langchain-openai``'s ``OpenAIEmbeddings`` since OpenRouter is
OpenAI-compatible — these tests lock the wiring that builds that client
correctly + that the provider survives snapshot/publish/import.
"""

from __future__ import annotations

import json
import tempfile

import pytest

from cuga.backend.knowledge.config import KnowledgeConfig


@pytest.fixture
def _isolated_knowledge_dir(monkeypatch):
    """Per-test persist_dir so engine flock doesn't fight a running cuga server."""
    tmpdir = tempfile.mkdtemp(prefix="cuga-or-test-")
    monkeypatch.setenv("DYNACONF_KNOWLEDGE__PERSIST_DIR", tmpdir)
    monkeypatch.setenv("DYNACONF_KNOWLEDGE__ENABLED", "true")
    yield tmpdir


# ---- create_embeddings factory ----


def _import_factory():
    """Lazy import to keep this module side-effect-free at collection time."""
    from cuga.backend.knowledge.engine import OPENROUTER_BASE_URL, create_embeddings

    return create_embeddings, OPENROUTER_BASE_URL


def test_create_embeddings_openrouter_auto_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """When base_url is empty, the factory auto-sets it to OpenRouter's URL."""
    create_embeddings, OPENROUTER_BASE_URL = _import_factory()

    cfg = KnowledgeConfig(
        embedding_provider="openrouter",
        embedding_model="openai/text-embedding-3-small",
        embedding_api_key="sk-or-test",
        # embedding_base_url intentionally left empty
    )
    emb = create_embeddings(cfg)
    # langchain-openai stores the base URL on ``openai_api_base`` (str) — its
    # exact attribute name has bounced between versions, so accept either.
    actual = getattr(emb, "openai_api_base", None) or getattr(emb, "base_url", None)
    actual_str = str(actual) if actual is not None else ""
    # Accept either exact match or trailing-slash variant (httpx URL normalises)
    assert OPENROUTER_BASE_URL.rstrip("/") in actual_str.rstrip("/"), (
        f"expected base URL containing {OPENROUTER_BASE_URL!r}, got {actual_str!r}"
    )


def test_create_embeddings_openrouter_openrouter_url_override_wins() -> None:
    """User can still override the URL if it points at an OpenRouter host
    (escape hatch — e.g. if a regional URL like eu.openrouter.ai is introduced)."""
    create_embeddings, _ = _import_factory()

    cfg = KnowledgeConfig(
        embedding_provider="openrouter",
        embedding_model="openai/text-embedding-3-small",
        embedding_api_key="sk-or-test",
        embedding_base_url="https://eu.openrouter.ai/api/v1",
    )
    emb = create_embeddings(cfg)
    actual = getattr(emb, "openai_api_base", None) or getattr(emb, "base_url", None)
    actual_str = str(actual) if actual is not None else ""
    assert "eu.openrouter.ai" in actual_str, f"openrouter-host override ignored; got {actual_str!r}"


def test_create_embeddings_openrouter_ignores_leaked_non_openrouter_url(caplog) -> None:
    """Stale base_url from a previous provider must NOT silently route OpenRouter
    creds to the wrong host. Regression for the user-reported flow: switched
    provider to openrouter but the IBM LiteLLM URL was still in the config →
    OpenRouter key got sent to IBM proxy → 401 'Invalid proxy server'.
    """
    import logging as _stdlib_logging

    create_embeddings, OPENROUTER_BASE_URL = _import_factory()

    cfg = KnowledgeConfig(
        embedding_provider="openrouter",
        embedding_model="openai/text-embedding-3-small",
        embedding_api_key="sk-or-test",
        embedding_base_url="https://ete-litellm.bx.cloud9.ibm.com",  # leaked from prior provider
    )
    with caplog.at_level(_stdlib_logging.WARNING):
        emb = create_embeddings(cfg)
    actual = getattr(emb, "openai_api_base", None) or getattr(emb, "base_url", None)
    actual_str = str(actual) if actual is not None else ""
    assert "ibm.com" not in actual_str, f"stale base_url leaked through: {actual_str!r}"
    assert OPENROUTER_BASE_URL.rstrip("/") in actual_str.rstrip("/"), (
        f"fallback to OpenRouter URL failed; got {actual_str!r}"
    )


def test_create_embeddings_openrouter_missing_key_warns_and_defers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No api_key (and no OPENROUTER_API_KEY env var) → construct succeeds with
    a warning. Actual auth failure surfaces at first embed call so that an
    imported published snapshot (key stripped) can still apply on a machine
    where the env var will be set before first ingest."""
    create_embeddings, _ = _import_factory()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    cfg = KnowledgeConfig(
        embedding_provider="openrouter",
        embedding_model="openai/text-embedding-3-small",
    )
    warned: list[str] = []
    from loguru import logger as _logger

    handler_id = _logger.add(lambda msg: warned.append(str(msg)), level="WARNING")
    try:
        embeddings = create_embeddings(cfg)
    finally:
        _logger.remove(handler_id)
    assert embeddings is not None
    assert any("OPENROUTER_API_KEY" in w for w in warned), (
        f"expected a warning naming OPENROUTER_API_KEY, got: {warned}"
    )


def test_create_embeddings_openrouter_missing_model_raises() -> None:
    """No model → clear ValueError pointing the user at OpenRouter's catalog."""
    create_embeddings, _ = _import_factory()

    cfg = KnowledgeConfig(
        embedding_provider="openrouter",
        embedding_api_key="sk-or-test",
        # embedding_model intentionally empty
    )
    with pytest.raises(ValueError, match="openrouter.ai/models"):
        create_embeddings(cfg)


def test_create_embeddings_openrouter_strips_whitespace() -> None:
    """Copy-pasted keys often carry a trailing newline/space — strip silently."""
    create_embeddings, _ = _import_factory()

    cfg = KnowledgeConfig(
        embedding_provider="openrouter",
        embedding_model="  openai/text-embedding-3-small  ",
        embedding_api_key="  sk-or-pasted  \n",
    )
    emb = create_embeddings(cfg)
    actual_key = getattr(emb, "openai_api_key", None)
    actual_key_str = (
        actual_key.get_secret_value() if hasattr(actual_key, "get_secret_value") else str(actual_key)
    )
    assert actual_key_str == "sk-or-pasted"
    actual_model = getattr(emb, "model", None) or getattr(emb, "_model", None)
    assert "openai/text-embedding-3-small" == str(actual_model).strip()


def test_create_embeddings_openrouter_whitespace_only_model_raises() -> None:
    """A model field that is just whitespace must be rejected after strip."""
    create_embeddings, _ = _import_factory()

    cfg = KnowledgeConfig(
        embedding_provider="openrouter",
        embedding_model="   ",
        embedding_api_key="sk-or-test",
    )
    with pytest.raises(ValueError, match="openrouter.ai/models"):
        create_embeddings(cfg)


def test_create_embeddings_openrouter_whitespace_only_key_warns_and_defers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key field that is just whitespace strips to empty and follows the
    same deferred-validation path as a missing key — construct succeeds
    with a warning, no eager raise."""
    create_embeddings, _ = _import_factory()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    cfg = KnowledgeConfig(
        embedding_provider="openrouter",
        embedding_model="openai/text-embedding-3-small",
        embedding_api_key="   \t  ",
    )
    warned: list[str] = []
    from loguru import logger as _logger

    handler_id = _logger.add(lambda msg: warned.append(str(msg)), level="WARNING")
    try:
        embeddings = create_embeddings(cfg)
    finally:
        _logger.remove(handler_id)
    assert embeddings is not None
    assert any("OPENROUTER_API_KEY" in w for w in warned), (
        f"expected a warning naming OPENROUTER_API_KEY, got: {warned}"
    )


def test_create_embeddings_openrouter_picks_up_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the user leaves api_key empty, fall back to OPENROUTER_API_KEY env var."""
    create_embeddings, _ = _import_factory()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-from-env")

    cfg = KnowledgeConfig(
        embedding_provider="openrouter",
        embedding_model="openai/text-embedding-3-small",
    )
    emb = create_embeddings(cfg)
    # The key landed on the client (langchain-openai stores it as SecretStr).
    actual = getattr(emb, "openai_api_key", None)
    actual_str = actual.get_secret_value() if hasattr(actual, "get_secret_value") else str(actual)
    assert actual_str == "sk-or-from-env"


# ---- validation ----


def test_openrouter_passes_validation() -> None:
    """coerce_and_validate accepts provider=openrouter."""
    cfg = KnowledgeConfig.coerce_and_validate(
        {
            "embedding_provider": "openrouter",
            "embedding_model": "openai/text-embedding-3-small",
            "embedding_api_key": "sk-or-test",
        }
    )
    assert cfg.embedding_provider == "openrouter"


def test_unknown_provider_still_rejected() -> None:
    """openrouter joining the allow-list must not weaken validation for typos."""
    with pytest.raises(ValueError, match="Unknown embedding_provider"):
        KnowledgeConfig.coerce_and_validate({"embedding_provider": "openrout"})


def test_validate_rejects_openrouter_without_model() -> None:
    """validate() must catch missing model at config-load time, not at engine init.

    The user could otherwise publish a config that passes validation but blows
    up later when the engine tries to embed. Failing here surfaces the right
    field name in the error message.
    """
    with pytest.raises(ValueError, match="OpenRouter.*embedding_model"):
        KnowledgeConfig.coerce_and_validate(
            {
                "embedding_provider": "openrouter",
                "embedding_model": "",
                "embedding_api_key": "sk-or-test",
            }
        )


def test_validate_rejects_openrouter_with_whitespace_only_model() -> None:
    """Whitespace-only model is the same as empty — must be rejected."""
    with pytest.raises(ValueError, match="OpenRouter.*embedding_model"):
        KnowledgeConfig.coerce_and_validate(
            {
                "embedding_provider": "openrouter",
                "embedding_model": "   ",
                "embedding_api_key": "sk-or-test",
            }
        )


def test_validate_does_not_require_api_key_at_load_time() -> None:
    """OPENROUTER_API_KEY env var fallback means validate() must NOT block on
    missing api_key — that check belongs in ``create_embeddings`` after env
    resolution. This guards against over-eager validation that would break the
    env-var workflow.
    """
    cfg = KnowledgeConfig.coerce_and_validate(
        {
            "embedding_provider": "openrouter",
            "embedding_model": "openai/text-embedding-3-small",
            "embedding_api_key": "",  # empty here, can come from env
        }
    )
    assert cfg.embedding_provider == "openrouter"


# ---- snapshot round-trip ----


def test_openrouter_round_trips_through_snapshot() -> None:
    """All OpenRouter-relevant fields survive to_dict → JSON → coerce_and_validate.

    This is the contract that makes the published config safe to import: a
    snapshot taken in the manager UI with provider=openrouter must apply
    identically when another teammate restores it.
    """
    cfg = KnowledgeConfig(
        enabled=True,
        embedding_provider="openrouter",
        embedding_model="openai/text-embedding-3-small",
        embedding_api_key="sk-or-test-key",
        embedding_base_url="",  # auto-set on use; empty string is what gets saved
    )
    payload = json.dumps(cfg.to_dict(), default=str)
    restored = KnowledgeConfig.coerce_and_validate(json.loads(payload))
    assert restored.embedding_provider == "openrouter"
    assert restored.embedding_model == "openai/text-embedding-3-small"
    assert restored.embedding_api_key == "sk-or-test-key"
    assert restored.embedding_base_url == ""


# ---- network-flag (concurrency gate) ----


def test_embedder_is_network_includes_openrouter() -> None:
    """OpenRouter is HTTP-bound → C4 concurrent gather should kick in.

    The previous form constructed a tuple inside the test and asserted
    against it — vacuous, would pass even if the production code dropped
    openrouter entirely (CodeRabbit M6). Now grep the actual production
    source for the live tuple so a narrowing in
    ``KnowledgeEngine._create_vector_adapter`` fires this test.
    """
    import inspect

    from cuga.backend.knowledge.engine import KnowledgeEngine

    src = inspect.getsource(KnowledgeEngine._create_vector_adapter)
    assert "embedder_is_network" in src, (
        "production code dropped the embedder_is_network flag — concurrent gather is no longer dispatched"
    )
    assert '"openrouter"' in src, (
        "openrouter was removed from the network-providers tuple — concurrent embed will fall back to serial"
    )
    # And the network-providers tuple still contains the other expected
    # entries — narrowing the set without intent should fire this test too.
    for provider in ("openai", "ollama", "litellm"):
        assert f'"{provider}"' in src, f"network-providers tuple narrowed: {provider!r} removed"


# ---- SDK round-trip via CugaAgent(knowledge_config=...) ----


# ---- settings.toml load ----


def test_openrouter_loads_from_settings() -> None:
    """KnowledgeConfig.from_settings picks up provider=openrouter from TOML."""

    class _FakeSettings:
        def __init__(self, knowledge: dict) -> None:
            self._kb = knowledge

        def get(self, key: str, default=None):
            if key == "knowledge":
                return self._kb
            return default

    # Pareto-locked profiles (2026-06) own embedding_model. To verify the
    # settings.toml fallback path that explicitly chooses openrouter, opt
    # out of profile loading by naming a profile the loader won't find.
    settings = _FakeSettings(
        {
            "enabled": True,
            "search": {"rag_profile": "__no_such_profile__"},
            "embeddings": {
                "provider": "openrouter",
                "model": "openai/text-embedding-3-small",
                "api_key": "sk-or-from-toml",
            },
        }
    )
    cfg = KnowledgeConfig.from_settings(settings)
    assert cfg.embedding_provider == "openrouter"
    assert cfg.embedding_model == "openai/text-embedding-3-small"
    assert cfg.embedding_api_key == "sk-or-from-toml"
