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


def test_failed_endpoint_is_not_retried_during_the_cooldown():
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
    assert len(http.calls) == calls_after_first, "failed endpoints have a short retry cooldown"


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


def test_pre_instantiated_model_honors_the_context_window_setting():
    from langchain_openai import ChatOpenAI
    from cuga.backend.llm.models import LLMManager

    manager = LLMManager()
    model = ChatOpenAI(model="gateway-alias", api_key="test")
    with (
        patch.object(manager, "_pre_instantiated_model", None),
        patch("cuga.backend.llm.models.is_mock_llm_enabled", return_value=False),
    ):
        manager.set_llm(model)
        returned = manager.get_model({"max_tokens": 1_000, "context_window": 8_192})
    assert returned is model
    assert cw.resolve_context_window(returned) == cw.ContextWindow(8_192, "settings")
    assert returned.profile["max_input_tokens"] == 8_192


@pytest.mark.parametrize("header", ["RITS_API_KEY", "Authorization", "authorization"])
def test_discovery_uses_configured_auth_headers_instead_of_dummy_credentials(header):
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model="gateway-alias",
        api_key="dummy",  # pragma: allowlist secret -- mirrors the gateway's placeholder key
        base_url="https://gw.example/v1",
        default_headers={header: "test-custom-credential"},
    )

    def get(url, **kwargs):
        headers = {k.lower(): v for k, v in kwargs["headers"].items()}
        assert headers[header.lower()] == "test-custom-credential"
        return _Response(
            {"data": [{"model_name": "gateway-alias", "model_info": {"max_input_tokens": 8_192}}]}
        )

    with patch("httpx.get", side_effect=get):
        assert cw.resolve_context_window(model) == cw.ContextWindow(8_192, "litellm-proxy")
    assert "test-custom-credential" not in repr(cw._resolved)
    assert "test-custom-credential" not in repr(cw._responses)


@pytest.mark.parametrize("custom_header", [False, True])
def test_discovery_caches_are_separate_for_each_credential(custom_header):
    def get(url, **kwargs):
        headers = kwargs["headers"]
        credential = headers.get("RITS_API_KEY") if custom_header else headers.get("Authorization")
        size = 32_000 if "first" in credential else 8_000
        return _Response({"data": [{"model_name": "alias", "model_info": {"max_input_tokens": size}}]})

    with patch("httpx.get", side_effect=get) as http:
        for credential, expected in [("first", 32_000), ("second", 8_000)]:
            model = _openai_model("alias", "https://gw.example/v1", "dummy" if custom_header else credential)
            if custom_header:
                model.default_headers = {"RITS_API_KEY": credential}
            assert cw.resolve_context_window(model).tokens == expected
    assert http.call_count == 2


@pytest.mark.parametrize(
    "initial",
    [
        _Response(None, 503),
        _Response({"data": []}),
        _Response({"data": [{"model_name": "deepseek/deepseek-v3", "model_info": {}}]}),
    ],
)
def test_failed_discovery_and_fallback_resolution_recover_after_cooldown(initial):
    model = _openai_model("deepseek/deepseek-v3", "https://gw.example/v1")
    with (
        patch.object(cw.time, "monotonic", return_value=100),
        patch("httpx.get", return_value=initial),
    ):
        assert cw.resolve_context_window(model).source == "litellm-map"
    recovered = {"data": [{"model_name": "deepseek/deepseek-v3", "model_info": {"max_input_tokens": 8_000}}]}
    with (
        patch.object(cw.time, "monotonic", return_value=100 + cw.DISCOVERY_RETRY_SECONDS + 1),
        patch("httpx.get", return_value=_Response(recovered)) as http,
    ):
        assert cw.resolve_context_window(model) == cw.ContextWindow(8_000, "litellm-proxy")
    http.assert_called_once()


@pytest.mark.parametrize(
    "config, expected", [({"disable_ssl": True}, False), ({"ssl_ca_bundle": "/test/ca.pem"}, "/test/ca.pem")]
)
def test_discovery_uses_the_effective_per_model_tls_setting(config, expected):
    from cuga.backend.llm.models import LLMManager

    model = _openai_model("alias", "https://gw.example/v1")
    LLMManager()._configure_context_window(model, config)
    with patch("httpx.get", return_value=_Response({"data": []})) as http:
        cw.resolve_context_window(model)
    assert all(call.kwargs["verify"] == expected for call in http.call_args_list)


def test_discovery_honors_global_toml_ca_bundle(monkeypatch):
    for key in ("CUGA_DISABLE_SSL", "OPENAI_SSL_VERIFY", "CUGA_SSL_CA_BUNDLE"):
        monkeypatch.delenv(key, raising=False)
    with patch("cuga.config.settings.connections.ssl_ca_bundle", "/test/global-ca.pem", create=True):
        assert cw._ssl_verify() == "/test/global-ca.pem"


def test_tls_configuration_is_part_of_the_cache_identity():
    model = _openai_model("alias", "https://gw.example/v1")
    with patch("httpx.get", return_value=_Response({"data": []})) as http:
        cw.set_context_window_tls(model, True)
        cw.resolve_context_window(model)
        cw.set_context_window_tls(model, False)
        cw.resolve_context_window(model)
    assert http.call_count == 4


def test_optional_tls_metadata_does_not_break_immutable_adapters():
    cw.set_context_window_tls(object(), True)


def test_exact_map_entry_still_precedes_the_curated_table():
    with patch.object(cw, "litellm_map_tokens", return_value=1_000_000):
        assert cw.resolve_context_window(_openai_model("claude-sonnet-4-6")) == cw.ContextWindow(
            1_000_000, "litellm-map"
        )


def test_bundled_map_lookup_does_not_import_litellm():
    import os
    import subprocess
    import sys

    script = (
        "import sys; from cuga.backend.llm.context_window import litellm_map_tokens; "
        "assert litellm_map_tokens('gpt-4.1') == 1047576; "
        "assert 'litellm' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", script], check=True, env=os.environ.copy(), timeout=20)
