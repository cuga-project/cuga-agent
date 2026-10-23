"""Runtime context-window lookup: settings, endpoint, LiteLLM map, table, profile, default."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cuga.backend.llm import context_window as cw

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_cache():
    cw.clear_context_window_cache()
    yield
    cw.clear_context_window_cache()


class _Response:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data


class _FakeHTTP:
    """Serve canned JSON per URL and count the calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, params=None, headers=None, timeout=None, verify=None):
        self.calls.append((url, params, headers))
        if url not in self.routes:
            return _Response(None, status=404)
        route = self.routes[url]
        if isinstance(route, Exception):
            raise route
        return _Response(route)


def _openai_model(name, base_url=None, api_key=None, profile=None):
    return SimpleNamespace(model_name=name, openai_api_base=base_url, openai_api_key=api_key, profile=profile)


class ChatWatsonx:  # detected by class name, like langchain_ibm.ChatWatsonx
    def __init__(self, model_id, url, profile=None):
        self.model_id = model_id
        self.url = url
        self.profile = profile


def test_settings_override_wins_over_every_other_source():
    model = _openai_model("deepseek/deepseek-v3.2", "https://gw.example/v1")
    cw.set_context_window_override(model, 50_000)
    http = _FakeHTTP({})
    with patch("httpx.get", http):
        window = cw.resolve_context_window(model)
    assert window == cw.ContextWindow(50_000, "settings")
    assert http.calls == [], "no lookup when the setting answers"


@pytest.mark.parametrize("bad", [0, -1, True, "abc"])
def test_settings_override_rejects_invalid_values(bad):
    with pytest.raises(ValueError):
        cw.set_context_window_override(_openai_model("m"), bad)


def test_openrouter_uses_the_smaller_of_model_and_top_provider_window():
    http = _FakeHTTP(
        {
            "https://openrouter.ai/api/v1/models": {
                "data": [
                    {"id": "other/model", "context_length": 8_000},
                    {
                        "id": "deepseek/deepseek-v3.2",
                        "context_length": 163_840,
                        "top_provider": {"context_length": 131_072},
                    },
                ]
            }
        }
    )
    with patch("httpx.get", http):
        window = cw.resolve_context_window(
            _openai_model("deepseek/deepseek-v3.2", "https://openrouter.ai/api/v1")
        )
    assert window == cw.ContextWindow(131_072, "openrouter")


def test_watsonx_reads_max_sequence_length_from_foundation_model_specs():
    url = "https://us-south.ml.cloud.ibm.com"
    http = _FakeHTTP(
        {
            f"{url}/ml/v1/foundation_model_specs": {
                "resources": [
                    {"model_id": "openai/gpt-oss-120b", "model_limits": {"max_sequence_length": 131_072}}
                ]
            }
        }
    )
    with patch("httpx.get", http):
        window = cw.resolve_context_window(
            ChatWatsonx("openai/gpt-oss-120b", url, profile={"max_input_tokens": 8192})
        )
    assert window == cw.ContextWindow(131_072, "watsonx")
    assert http.calls[0][1]["filters"] == "modelid_openai/gpt-oss-120b"


def test_watsonx_generic_8k_profile_is_never_used():
    http = _FakeHTTP({})  # specs unreachable
    with patch("httpx.get", http):
        window = cw.resolve_context_window(
            ChatWatsonx(
                "ibm/unknown-granite", "https://us-south.ml.cloud.ibm.com", profile={"max_input_tokens": 8192}
            )
        )
    assert window.source == "default"


def test_litellm_proxy_model_info_gives_the_deployment_window():
    http = _FakeHTTP(
        {
            "https://gw.example/model/info": {
                "data": [
                    {
                        "model_name": "aws/gpt-oss-120b",
                        "model_info": {"max_input_tokens": 128_000},
                        "litellm_params": {"model": "bedrock/openai.gpt-oss-120b-1:0"},
                    }
                ]
            }
        }
    )
    with patch("httpx.get", http):
        window = cw.resolve_context_window(
            _openai_model("aws/gpt-oss-120b", "https://gw.example/v1", "sk-test")
        )
    assert window == cw.ContextWindow(128_000, "litellm-proxy"), "the deployment, not the table's 131,072"
    assert http.calls[0][2] == {"Authorization": "Bearer sk-test"}


def test_litellm_proxy_without_a_window_falls_back_to_the_backend_model_in_litellm_map():
    http = _FakeHTTP(
        {
            "https://gw.example/model/info": {
                "data": [
                    {
                        "model_name": "azure/DeepSeek-V3.2",
                        "model_info": {"max_input_tokens": None},
                        "litellm_params": {"model": "deepseek/deepseek-v3.2"},
                    }
                ]
            }
        }
    )
    with patch("httpx.get", http):
        window = cw.resolve_context_window(_openai_model("azure/DeepSeek-V3.2", "https://gw.example"))
    assert window == cw.ContextWindow(163_840, "litellm-proxy-backend")


def test_litellm_client_routing_prefix_matches_the_proxy_alias():
    http = _FakeHTTP(
        {
            "https://gw.example/model/info": {
                "data": [{"model_name": "aws/gpt-oss-120b", "model_info": {"max_input_tokens": 128_000}}]
            }
        }
    )
    model = SimpleNamespace(
        model="openai/aws/gpt-oss-120b", api_base="https://gw.example", api_key=None, profile=None
    )
    with patch("httpx.get", http):
        assert cw.resolve_context_window(model) == cw.ContextWindow(128_000, "litellm-proxy")


def test_vllm_models_listing_gives_max_model_len():
    http = _FakeHTTP(
        {
            "https://vllm.example/v1/models": {
                "data": [{"id": "ibm-granite/granite-4.0-h-small", "max_model_len": 131_072}]
            }
        }
    )
    with patch("httpx.get", http):
        window = cw.resolve_context_window(
            _openai_model("ibm-granite/granite-4.0-h-small", "https://vllm.example/v1")
        )
    assert window == cw.ContextWindow(131_072, "vllm")


def test_failed_endpoint_falls_back_to_litellm_map_and_is_not_retried():
    http = _FakeHTTP(
        {
            "https://gw.example/model/info": ConnectionError("down"),
            "https://gw.example/models": ConnectionError("down"),
        }
    )
    with patch("httpx.get", http):
        first = cw.resolve_context_window(_openai_model("deepseek/deepseek-v3", "https://gw.example"))
        calls_after_first = len(http.calls)
        cw.resolve_context_window(_openai_model("deepseek/deepseek-chat", "https://gw.example"))
    assert first == cw.ContextWindow(65_536, "litellm-map"), "LiteLLM's exact key beats the table's 64,000"
    assert len(http.calls) == calls_after_first, "a failed endpoint is asked once per process"


def test_one_listing_serves_every_model_on_the_endpoint():
    http = _FakeHTTP(
        {
            "https://openrouter.ai/api/v1/models": {
                "data": [{"id": "a/one", "context_length": 32_000}, {"id": "b/two", "context_length": 64_000}]
            }
        }
    )
    with patch("httpx.get", http):
        assert (
            cw.resolve_context_window(_openai_model("a/one", "https://openrouter.ai/api/v1")).tokens == 32_000
        )
        assert (
            cw.resolve_context_window(_openai_model("b/two", "https://openrouter.ai/api/v1")).tokens == 64_000
        )
    assert len(http.calls) == 1


def test_public_provider_apis_are_not_probed():
    http = _FakeHTTP({})
    with patch("httpx.get", http):
        window = cw.resolve_context_window(_openai_model("gpt-4.1", "https://api.openai.com/v1"))
    assert http.calls == []
    assert window == cw.ContextWindow(1_047_576, "litellm-map")


def test_discovery_setting_off_skips_endpoints():
    http = _FakeHTTP(
        {"https://openrouter.ai/api/v1/models": {"data": [{"id": "x/y", "context_length": 1_000}]}}
    )
    with (
        patch("cuga.config.settings.advanced_features.context_window_discovery", False, create=True),
        patch("httpx.get", http),
    ):
        window = cw.resolve_context_window(
            _openai_model("x/y", "https://openrouter.ai/api/v1", profile={"max_input_tokens": 9_000})
        )
    assert http.calls == []
    assert window == cw.ContextWindow(9_000, "profile")


def test_litellm_watsonx_entries_are_not_trusted():
    # LiteLLM says 8,192 for watsonx/openai/gpt-oss-120b; watsonx.ai serves 131,072.
    assert cw.litellm_map_tokens("watsonx/openai/gpt-oss-120b") is None
    window = cw.resolve_context_window(_openai_model("watsonx/openai/gpt-oss-120b"))
    assert window == cw.ContextWindow(131_072, "table")


def test_table_answers_names_litellm_does_not_know():
    window = cw.resolve_context_window(_openai_model("rits/mistralai/Mistral-Medium-3.5-128B"))
    assert window == cw.ContextWindow(262_144, "table")


def test_unknown_model_gets_the_default_window():
    from cuga.backend.cuga_graph.utils.token_counter import DEFAULT_CONTEXT_SIZE

    window = cw.resolve_context_window(_openai_model("no-such-model-anywhere"))
    assert window == cw.ContextWindow(DEFAULT_CONTEXT_SIZE, "default")


def test_lookup_bug_degrades_to_default_instead_of_raising():
    with patch.object(cw, "_resolve", side_effect=RuntimeError("boom")):
        window = cw.resolve_context_window(_openai_model("anything"))
    assert window.source == "default"


def test_llm_manager_pins_the_context_window_setting(monkeypatch):
    from cuga.backend.llm.models import LLMManager

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mgr = LLMManager()
    model = mgr._create_llm_instance(
        {"platform": "openai", "model_name": "gpt-4.1", "max_tokens": 1000, "context_window": 42_000}
    )
    assert cw.resolve_context_window(model) == cw.ContextWindow(42_000, "settings")
    assert model.profile["max_input_tokens"] == 42_000
