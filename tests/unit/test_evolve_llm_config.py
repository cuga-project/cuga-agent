"""Bundled Evolve shares resolved credentials without forwarding them over MCP."""

from unittest.mock import AsyncMock, Mock

import pytest

from cuga.backend.evolve import llm_config

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_config(monkeypatch):
    for name in (*llm_config._MODEL_OVERRIDES, "CUGA_EVOLVE_INHERIT_LLM"):
        monkeypatch.delenv(name, raising=False)
    import litellm

    for name in ("api_key", "api_base", "api_version", "headers", "client_session", "aclient_session"):
        monkeypatch.setattr(litellm, name, None)


def test_gateway_connection_uses_resolved_key_headers_and_tls_client():
    import httpx
    import litellm
    from langchain_openai import ChatOpenAI

    with httpx.Client(verify=False) as client:
        model = ChatOpenAI(
            model="azure/gpt-4o",
            api_key="dummy",
            base_url="https://gateway.example/v1",
            default_headers={"X-API-Key": "test-gateway-key"},
            http_client=client,
        )
        llm_config.apply_model(model)
        assert litellm.api_key == "dummy"
        assert litellm.headers == {"X-API-Key": "test-gateway-key"}
        assert litellm.api_base == "https://gateway.example/v1"
        assert litellm.client_session is client
        import os

        assert os.environ["EVOLVE_MODEL_NAME"] == "azure/gpt-4o"
        assert os.environ["EVOLVE_CUSTOM_LLM_PROVIDER"] == "openai"


def test_native_azure_uses_deployment_endpoint_and_version():
    import litellm
    from langchain_openai import AzureChatOpenAI

    model = AzureChatOpenAI(
        azure_deployment="my-gpt-deployment",
        azure_endpoint="https://example.openai.azure.com/",
        api_version="2024-02-01",
        api_key="test-azure-key",
    )
    llm_config.apply_model(model)
    assert litellm.api_key == "test-azure-key"
    assert litellm.api_base == "https://example.openai.azure.com/"
    assert litellm.api_version == "2024-02-01"
    import os

    assert os.environ["EVOLVE_MODEL_NAME"] == "my-gpt-deployment"
    assert os.environ["EVOLVE_CUSTOM_LLM_PROVIDER"] == "azure"


def test_litellm_gateway_retains_provider_and_key():
    import litellm
    from langchain_litellm import ChatLiteLLM

    model = ChatLiteLLM(
        model="azure/gpt-4o",
        custom_llm_provider="openai",
        api_base="https://gateway.example/v1",
        api_key="test-key",
    )
    llm_config.apply_model(model)
    assert litellm.api_key == "test-key"
    assert litellm.api_base == "https://gateway.example/v1"


@pytest.mark.asyncio
async def test_published_config_uses_cuga_secret_resolution(monkeypatch):
    from cuga.backend.llm import models
    from cuga.backend.server import config_store

    config = {"provider": "openai", "model": "azure/gpt-4o", "api_key": "db://model-key"}
    load = AsyncMock(return_value=({"llm": config}, "4"))
    resolve = Mock(return_value=object())
    apply = Mock()
    monkeypatch.setattr(config_store, "load_config", load)
    monkeypatch.setattr(models, "create_llm_from_config", resolve)
    monkeypatch.setattr(llm_config, "apply_model", apply)
    await llm_config.configure_bundled_llm()
    load.assert_awaited_once_with(None)
    resolve.assert_called_once_with(config)
    apply.assert_called_once_with(resolve.return_value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variable,value", [("EVOLVE_MODEL_NAME", "custom-model"), ("CUGA_EVOLVE_INHERIT_LLM", "false")]
)
async def test_explicit_evolve_configuration_is_preserved(monkeypatch, variable, value):
    from cuga.backend.server import config_store

    load = AsyncMock()
    monkeypatch.setattr(config_store, "load_config", load)
    monkeypatch.setenv(variable, value)
    await llm_config.configure_bundled_llm()
    load.assert_not_called()


def test_evolve_completion_uses_gateway_credentials_on_wire():
    import json

    import httpx
    import litellm
    from langchain_openai import ChatOpenAI

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "azure/gpt-4o",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "[]"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        model = ChatOpenAI(
            model="azure/gpt-4o",
            api_key="dummy",
            base_url="https://gateway.example/v1",
            default_headers={"Authorization": "Bearer test-resolved-key"},
            http_client=client,
        )
        llm_config.apply_model(model)
        import os

        result = litellm.completion(
            model=os.environ["EVOLVE_FACT_EXTRACTION_MODEL"],
            custom_llm_provider=os.environ["EVOLVE_CUSTOM_LLM_PROVIDER"],
            messages=[{"role": "user", "content": "Remember this preference"}],
        )
    assert result.choices[0].message.content == "[]"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://gateway.example/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer test-resolved-key"
    assert json.loads(requests[0].content)["model"] == "azure/gpt-4o"


@pytest.mark.asyncio
async def test_no_published_model_uses_cuga_toml(monkeypatch):
    from cuga.backend.llm import models
    from cuga.backend.server import config_store
    from cuga.config import settings

    manager = Mock()
    monkeypatch.setattr(config_store, "load_config", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(models, "LLMManager", lambda: manager)
    apply = Mock()
    monkeypatch.setattr(llm_config, "apply_model", apply)
    await llm_config.configure_bundled_llm()
    manager.get_model.assert_called_once_with(settings.agent.code.model)
    apply.assert_called_once_with(manager.get_model.return_value)


def test_worker_resolves_connection_before_creating_evolve(monkeypatch):
    import uvicorn

    from cuga.backend.evolve import http_worker

    configured = []

    async def configure():
        configured.append(True)

    def create_app():
        assert configured == [True]
        return "test-app"

    monkeypatch.setattr(llm_config, "configure_bundled_llm", configure)
    monkeypatch.setattr(http_worker, "create_app", create_app)
    serve = Mock()
    monkeypatch.setattr(uvicorn, "run", serve)
    http_worker.main()
    assert serve.call_args.args == ("test-app",)


def test_native_azure_completion_uses_resolved_key_on_wire():
    import httpx
    import litellm
    from langchain_openai import AzureChatOpenAI

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "[]"}, "finish_reason": "stop"}
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        model = AzureChatOpenAI(
            azure_deployment="my-deployment",
            azure_endpoint="https://example.openai.azure.com/",
            api_version="2024-02-01",
            api_key="test-resolved-azure-key",
            http_client=client,
        )
        llm_config.apply_model(model)
        litellm.completion(
            model="my-deployment",
            custom_llm_provider="azure",
            messages=[{"role": "user", "content": "Remember this"}],
        )
    assert len(requests) == 1
    assert requests[0].url.path == "/openai/deployments/my-deployment/chat/completions"
    assert requests[0].url.params["api-version"] == "2024-02-01"
    assert requests[0].headers["api-key"] == "test-resolved-azure-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["load", "published", "toml"])
async def test_resolution_failure_does_not_stop_memory_worker(monkeypatch, stage):
    from cuga.backend.llm import models
    from cuga.backend.server import config_store

    failure = ValueError("secret-value-must-not-appear")
    load = AsyncMock(return_value=({"llm": {"model": "test"}}, "1"))
    if stage == "load":
        load.side_effect = failure
    elif stage == "toml":
        load.return_value = (None, None)
    monkeypatch.setattr(config_store, "load_config", load)
    monkeypatch.setattr(models, "create_llm_from_config", Mock(side_effect=failure))
    manager = Mock()
    manager.get_model.side_effect = failure
    monkeypatch.setattr(models, "LLMManager", lambda: manager)
    warning = Mock()
    monkeypatch.setattr(llm_config.logger, "warning", warning)
    await llm_config.configure_bundled_llm()
    warning.assert_called_once()
    assert "secret-value" not in str(warning.call_args)
    import os

    assert "EVOLVE_MODEL_NAME" not in os.environ
