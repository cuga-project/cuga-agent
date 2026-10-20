"""Tests for the MiniMax LLM provider branch in LLMManager.

MiniMax is OpenAI-compatible, so it reuses ``ReasoningChatOpenAI`` with a fixed
default base URL and the ``MINIMAX_API_KEY`` env var — mirroring the OpenRouter
branch. These tests lock the model-name/base-url resolution and the client
wiring in ``_create_llm_instance``.
"""

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from dynaconf import Dynaconf
from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.utils.token_counter import TokenCounter
from cuga.backend.llm.models import LLMManager, create_llm_from_config, set_current_llm_override

pytestmark = pytest.mark.unit

BASE_MODEL_SETTINGS = {
    "platform": "minimax",
    "max_tokens": 100,
    "temperature": 0.1,
}

MINIMAX_DEFAULT_BASE_URL = "https://api.minimax.io/v1"


@pytest.fixture(autouse=True)
def reset_llm_state(monkeypatch):
    mgr = LLMManager()
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)
    for key in ("MINIMAX_BASE_URL", "MINIMAX_REGION", "MODEL_NAME"):
        monkeypatch.delenv(key, raising=False)
    yield
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)


class TestMinimaxModelName:
    def test_default_model_name(self):
        mgr = LLMManager()
        assert mgr._get_model_name({}, "minimax") == "MiniMax-M3.1-Flash"

    def test_toml_model_name_wins_over_default(self):
        mgr = LLMManager()
        assert mgr._get_model_name({"model_name": "minimax-M2.5"}, "minimax") == "minimax-M2.5"

    def test_config_model_wins_over_toml(self):
        mgr = LLMManager()
        settings = {"model": "MiniMax-M3", "model_name": "minimax-M2.5"}
        assert mgr._get_model_name(settings, "minimax") == "MiniMax-M3"


class TestMinimaxBaseUrl:
    def test_default_base_url(self):
        mgr = LLMManager()
        assert mgr._get_base_url({}, "minimax") == MINIMAX_DEFAULT_BASE_URL

    def test_toml_url_wins_over_default(self):
        mgr = LLMManager()
        assert mgr._get_base_url({"url": "https://proxy.internal/v1"}, "minimax") == (
            "https://proxy.internal/v1"
        )

    def test_env_base_url_wins(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_BASE_URL", "https://env.minimax/v1")
        mgr = LLMManager()
        assert mgr._get_base_url({}, "minimax") == "https://env.minimax/v1"

    def test_china_region_endpoint(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
        monkeypatch.setenv("MINIMAX_REGION", "cn_zh")
        mgr = LLMManager()
        assert mgr._get_base_url({}, "minimax") == "https://api.minimaxi.com/v1"


class TestMinimaxCreateInstance:
    def test_client_receives_key_base_url_and_timeout(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_reasoning_chat_openai") as mock_factory:
                mock_openai = mock_factory.return_value
                mock_openai.return_value = object()
                mgr = LLMManager()
                mgr._create_llm_instance({**BASE_MODEL_SETTINGS, "timeout": 200})

        kwargs = mock_openai.call_args.kwargs
        assert kwargs["openai_api_key"] == "test-key"
        assert kwargs["openai_api_base"] == MINIMAX_DEFAULT_BASE_URL
        assert kwargs["model_name"] == "MiniMax-M3.1-Flash"
        assert kwargs["timeout"] == 200.0

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            mgr = LLMManager()
            with pytest.raises(ValueError, match="MINIMAX_API_KEY"):
                mgr._create_llm_instance({**BASE_MODEL_SETTINGS})


@pytest.fixture
def captured_requests(monkeypatch):
    """Exercise the real client and SDK, replacing only the HTTP transport."""
    requests = []
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr("cuga.backend.llm.models.resolve_secret", lambda _: None)

    def respond(request):
        payload = json.loads(request.content)
        requests.append((request, payload))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "Hello",
                            "reasoning_content": "Test reasoning",
                        },
                    }
                ],
            },
        )

    async def async_respond(self, request):
        return respond(request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, request: respond(request))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_respond)
    return requests


@pytest.mark.parametrize("region,host", [("global_en", "api.minimax.io"), ("cn_zh", "api.minimaxi.com")])
@pytest.mark.parametrize("preset", [None, "planner", "chat", "final_answer", "code", "qa", "action"])
def test_default_and_shipped_presets_reach_http_boundary(
    captured_requests, monkeypatch, region, host, preset
):
    monkeypatch.setenv("MINIMAX_REGION", region)
    model_settings = dict(BASE_MODEL_SETTINGS)
    if preset:
        path = Path(__file__).parents[2] / "src/cuga/configurations/models/settings.minimax.toml"
        config = Dynaconf(settings_files=[str(path)], environments=False, load_dotenv=False)
        model_settings = config.agent[preset].model.to_dict()
    mgr = LLMManager()
    llm = mgr.get_model(model_settings)
    result = llm.invoke("Hello")
    # Reusing the cached client must retain the selected model and task limits.
    assert mgr.get_model(model_settings) is llm
    assert TokenCounter().get_model_context_size(llm) == 1000000
    llm.invoke("Hello again")
    assert result.additional_kwargs["reasoning_content"] == "Test reasoning"
    assert len(captured_requests) == 2
    for request, payload in captured_requests:
        assert str(request.url) == f"https://{host}/v1/chat/completions"
        assert payload["model"] == "MiniMax-M3.1-Flash"
        assert payload.get("max_completion_tokens", payload.get("max_tokens")) == model_settings["max_tokens"]
        # Let MiniMax apply adaptive thinking and its default effort.
        assert not {"thinking", "reasoning_effort", "reasoning_split", "service_tier"} & payload.keys()


@pytest.mark.parametrize("model", ["MiniMax-M3", "MiniMax-M2.7", "custom-model"])
@pytest.mark.parametrize("selection", ["model", "model_name", "environment", "override", "saved"])
def test_explicit_models_reach_http_boundary(captured_requests, monkeypatch, model, selection):
    model_settings = dict(BASE_MODEL_SETTINGS)
    if selection == "environment":
        monkeypatch.setenv("MODEL_NAME", model)
    elif selection == "override":
        set_current_llm_override({"model": model})
    elif selection != "saved":
        model_settings[selection] = model
    if selection == "saved":
        with patch("cuga.backend.llm.models.settings") as settings:
            settings.secrets = None
            settings.agent.code.model = {"max_tokens": 100}
            llm = create_llm_from_config({"provider": "minimax", "model": model})
    else:
        llm = LLMManager().get_model(model_settings)
    llm.invoke("Hello")
    assert captured_requests[0][1]["model"] == model
    if model == "MiniMax-M3" and selection != "saved":
        assert TokenCounter().get_model_context_size(llm) == 1000000


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
async def test_flash_effort_and_multimodal_payload_reach_http_boundary(captured_requests, effort):
    llm = LLMManager().get_model(
        {
            **BASE_MODEL_SETTINGS,
            "extra_params": {"reasoning_effort": effort, "extra_body": {"thinking": {"type": "adaptive"}}},
        }
    )
    content = [
        {"type": "text", "text": "Describe this image"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,dGVzdA=="}},
    ]
    await llm.ainvoke([HumanMessage(content=content)])
    payload = captured_requests[0][1]
    assert payload["model"] == "MiniMax-M3.1-Flash"
    assert payload["reasoning_effort"] == effort
    assert payload["thinking"] == {"type": "adaptive"}
    assert payload["messages"][0]["content"] == content
    assert "service_tier" not in payload
    assert "reasoning_split" not in payload


def test_m3_disabled_thinking_reaches_http_boundary(captured_requests):
    llm = LLMManager().get_model(
        {
            **BASE_MODEL_SETTINGS,
            "model": "MiniMax-M3",
            "extra_params": {"extra_body": {"thinking": {"type": "disabled"}}},
        }
    )
    llm.invoke("Hello")
    assert captured_requests[0][1]["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize("source", ["url", "environment"])
def test_custom_endpoint_reaches_http_boundary(captured_requests, monkeypatch, source):
    model_settings = dict(BASE_MODEL_SETTINGS)
    url = "https://proxy.example.test/v1"
    if source == "environment":
        monkeypatch.setenv("MINIMAX_BASE_URL", url)
        monkeypatch.setenv("MINIMAX_REGION", "cn_zh")
    else:
        model_settings["url"] = url
    LLMManager().get_model(model_settings).invoke("Hello")
    assert str(captured_requests[0][0].url) == f"{url}/chat/completions"
