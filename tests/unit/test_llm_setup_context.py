import asyncio
from types import SimpleNamespace

import pytest

from cuga.backend.llm import models

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_concurrent_provider_contexts_reset_after_errors():
    async def task(model):
        with models.llm_config_context({"provider": "openai", "model": model}):
            await asyncio.sleep(0)
            assert models.get_current_llm_override()["model"] == model
            with pytest.raises(ValueError):
                with models.llm_config_context({"provider": "groq", "model": "inner"}):
                    raise ValueError()
            assert models.get_current_llm_override()["model"] == model
        assert models.get_current_llm_override() is None

    await asyncio.gather(task("draft"), task("published"))
    assert models.get_current_llm_override() is None


def test_local_config_uses_selected_provider_when_force_env_is_false(monkeypatch):
    received = []
    monkeypatch.setattr(
        models,
        "settings",
        SimpleNamespace(
            secrets=SimpleNamespace(mode="local", force_env=False),
            agent=SimpleNamespace(
                code=SimpleNamespace(model={"platform": "groq", "model": "toml", "max_tokens": 16000})
            ),
        ),
    )
    monkeypatch.setattr(models, "resolve_secret", lambda _: "resolved-test-credential")
    monkeypatch.setattr(
        models.LLMManager,
        "_create_llm_instance",
        lambda self, config: received.append(config.to_dict()) or object(),
    )
    monkeypatch.setattr(models.LLMManager, "_update_model_parameters", lambda self, model, **kwargs: model)
    models.create_llm_from_config(
        {
            "provider": "openai",
            "model": "selected",
            "api_key": "db://local",  # pragma: allowlist secret
            "base_url": "http://local/v1",
            "timeout": 20,
        }
    )
    assert received[0]["platform"] == "openai"
    assert received[0]["model"] == "selected"
    assert received[0]["api_key"] == "db://local"
    assert received[0]["url"] == "http://local/v1"
    assert received[0]["timeout"] == 20


def test_openrouter_uses_saved_credential_without_environment(monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        models, "resolve_secret", lambda ref: "selected-test-key" if ref == "db://local" else None
    )
    client = MagicMock()
    monkeypatch.setattr(models, "_get_reasoning_chat_openai", lambda: client)
    models.LLMManager()._create_llm_instance(
        models._ModelSettingsWrap(
            {
                "platform": "openrouter",
                "model": "selected",
                "api_key": "db://local",  # pragma: allowlist secret
                "max_tokens": 32,
                "temperature": 0.1,
            }
        )
    )
    assert client.call_args.kwargs["openai_api_key"] == "selected-test-key"


@pytest.mark.asyncio
async def test_local_provider_validation_resolves_encrypted_agent_scoped_secret(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    from cuga.backend.secrets import secret_resolver
    from cuga.backend.secrets.backends.db_backend import DbBackend
    from cuga.backend.storage import secrets_store
    from cuga.backend.storage.relational.local import LocalRelationalStore
    from cuga.backend.server.onboarding import validate_llm
    from cuga.backend.server.manage_routes.apply import apply_llm_to_draft_state
    from cuga.backend.server.manage_routes.draft_ops import rebuild_agent_from_config
    from unittest.mock import AsyncMock

    store = LocalRelationalStore(tmp_path / "config.db")
    monkeypatch.setenv("CUGA_GUIDED_SETUP", "true")
    monkeypatch.setattr(secrets_store, "_get_store", lambda: store)
    monkeypatch.setenv("CUGA_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(secret_resolver, "_active_backends", lambda: [DbBackend()])
    await secrets_store.set_secret("local-model", "non-production-value", agent_id="cuga-default")
    assert secret_resolver.resolve_secret("db://local-model") is None
    invoke = AsyncMock(return_value="OK")

    def create(config):
        assert secret_resolver.resolve_secret(config["api_key"]) == "non-production-value"
        return SimpleNamespace(ainvoke=invoke)

    monkeypatch.setattr(models, "create_llm_from_config", create)
    await validate_llm({"model": "selected", "api_key": "db://local-model"})  # pragma: allowlist secret
    invoke.assert_awaited_once()
    llm = {"model": "selected", "api_key": "db://local-model"}  # pragma: allowlist secret
    state = SimpleNamespace(current_llm=None)
    apply_llm_to_draft_state(state, llm)
    assert state.current_llm is not None

    async def build():
        assert secret_resolver.resolve_secret("db://local-model") == "non-production-value"
        assert models.get_current_llm_override()["model"] == "selected"

    graph = SimpleNamespace(tool_provider=None, build_graph=AsyncMock(side_effect=build))
    await rebuild_agent_from_config(graph, {"llm": llm})
    graph.build_graph.assert_awaited_once()
    assert graph.llm_config == llm
    assert models.get_current_llm_override() is None
    assert secret_resolver.resolve_secret("db://local-model") is None
    with secret_resolver.secret_agent_context("another-agent"):
        assert secret_resolver.resolve_secret("db://local-model") is None
    await store.close()


def test_cached_clients_do_not_share_agent_scoped_credentials(monkeypatch):
    from cuga.backend.secrets import secret_resolver
    from unittest.mock import Mock

    client = models.LLMManager()
    monkeypatch.setattr(client, "_models", {})
    monkeypatch.setattr(client, "_pre_instantiated_model", None)
    monkeypatch.setattr(client, "rebind_async_clients_to_running_loop", lambda: None)
    monkeypatch.setattr(client, "_update_model_parameters", lambda model, **kwargs: model)
    monkeypatch.setattr(models, "is_mock_llm_enabled", lambda: False)

    class Backend:
        scheme = "db"

        def get(self, path, *, agent_id, **kwargs):
            return {"first": "first-test-value", "second": "second-test-value"}.get(agent_id)

    monkeypatch.setattr(secret_resolver, "_active_backends", lambda: [Backend()])

    def create(config):
        credential = secret_resolver.resolve_secret(config["api_key"])
        if credential is None:
            raise ValueError("No credential for this agent")
        return SimpleNamespace(credential=credential)

    factory = Mock(side_effect=create)
    monkeypatch.setattr(client, "_create_llm_instance", factory)
    config = {
        "platform": "openai",
        "model": "selected",
        "api_key": "db://same-name",  # pragma: allowlist secret
        "max_tokens": 32,
    }  # pragma: allowlist secret
    with secret_resolver.secret_agent_context("first"):
        first = client.get_model(config)
        assert client.get_model(config) is first
    with secret_resolver.secret_agent_context("second"):
        second = client.get_model(config)
    assert first.credential == "first-test-value"
    assert second.credential == "second-test-value"
    assert first is not second
    with secret_resolver.secret_agent_context("missing"):
        with pytest.raises(ValueError, match="No credential"):
            client.get_model(config)
    assert factory.call_count == 3
