"""Tests for the watsonx Orchestrate (wxO) LLM provider branch in LLMManager.

``ChatWxO`` (from the optional ``ibm-watsonx-orchestrate-sdk`` extra) is a
``ChatOpenAI`` subclass that routes calls through an Orchestrate tenant's model
gateway, exchanging an API key for a JWT it refreshes on every request. It is
loaded lazily via ``_get_chat_wxo`` (mirroring ``_get_reasoning_chat_litellm``)
so importing ``cuga.backend.llm.models`` never requires the SDK — it is an
optional extra gated on Python >= 3.11.

These tests lock: model-name/instance-url resolution, the client wiring in
``_create_llm_instance`` (param names, key resolution, SSL, caching), the
local-dev vs remote API-key requirement, and the ``resolve_llm_api_key_ref``
secret hint.
"""

import os
from unittest.mock import patch

import pytest

from cuga.backend.llm.models import LLMManager, set_current_llm_override
from cuga.backend.utils.consts import LOCAL_ORCHESTRATE_URL

BASE_MODEL_SETTINGS = {
    "platform": "wxo",
    "max_tokens": 100,
    "temperature": 0.1,
}

WXO_DEFAULT_MODEL_NAME = "watsonx/openai/gpt-oss-120b"
REMOTE_INSTANCE_URL = "https://api.dl.watson-orchestrate.ibm.com/instances/abc-123"


def _self_signed_cert_pem() -> bytes:
    """A throwaway self-signed cert, valid enough for SSLContext.load_verify_locations."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cuga-test-ca")])
    epoch = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(epoch)
        .not_valid_after(epoch + datetime.timedelta(days=36500))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


class FakeChatWxO:
    """Minimal stand-in for ChatWxO that records constructor kwargs.

    A bare ``MagicMock`` would satisfy ``hasattr(model, 'model_kwargs')``
    misleadingly for callers that inspect the returned model (e.g.
    ``_update_model_parameters``), so use a small real object instead.
    """

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.model_kwargs = {}
        self.model_name = kwargs.get("model", "")
        self.temperature = kwargs.get("temperature")
        self.max_tokens = kwargs.get("max_tokens")
        self.profile = None
        FakeChatWxO.instances.append(self)


@pytest.fixture(autouse=True)
def reset_llm_state():
    mgr = LLMManager()
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)
    FakeChatWxO.instances.clear()
    for key in ("WXO_API_KEY", "WXO_INSTANCE_URL", "MODEL_NAME"):
        os.environ.pop(key, None)
    yield
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)
    FakeChatWxO.instances.clear()
    for key in ("WXO_API_KEY", "WXO_INSTANCE_URL", "MODEL_NAME"):
        os.environ.pop(key, None)


class TestWxoModelName:
    def test_default_model_name(self):
        mgr = LLMManager()
        assert mgr._get_model_name({}, "wxo") == WXO_DEFAULT_MODEL_NAME

    def test_toml_model_name_wins_over_default(self):
        mgr = LLMManager()
        assert mgr._get_model_name({"model_name": "groq/openai/gpt-oss-120b"}, "wxo") == (
            "groq/openai/gpt-oss-120b"
        )

    def test_config_model_wins_over_toml(self):
        mgr = LLMManager()
        settings = {"model": "groq/openai/gpt-oss-120b", "model_name": "watsonx/openai/gpt-oss-120b"}
        assert mgr._get_model_name(settings, "wxo") == "groq/openai/gpt-oss-120b"

    def test_env_model_name_wins_over_toml(self, monkeypatch):
        monkeypatch.setenv("MODEL_NAME", "groq/openai/gpt-oss-120b")
        mgr = LLMManager()
        assert mgr._get_model_name({"model_name": "watsonx/openai/gpt-oss-120b"}, "wxo") == (
            "groq/openai/gpt-oss-120b"
        )


class TestWxoBaseUrl:
    def test_default_is_local_orchestrate_url(self):
        mgr = LLMManager()
        assert mgr._get_base_url({}, "wxo") == LOCAL_ORCHESTRATE_URL

    def test_toml_instance_url_wins_over_default(self):
        mgr = LLMManager()
        assert mgr._get_base_url({"instance_url": REMOTE_INSTANCE_URL}, "wxo") == REMOTE_INSTANCE_URL

    def test_toml_base_url_alias(self):
        mgr = LLMManager()
        assert mgr._get_base_url({"base_url": REMOTE_INSTANCE_URL}, "wxo") == REMOTE_INSTANCE_URL

    def test_toml_url_alias(self):
        mgr = LLMManager()
        assert mgr._get_base_url({"url": REMOTE_INSTANCE_URL}, "wxo") == REMOTE_INSTANCE_URL

    def test_env_instance_url_wins_over_toml(self, monkeypatch):
        monkeypatch.setenv("WXO_INSTANCE_URL", REMOTE_INSTANCE_URL)
        mgr = LLMManager()
        assert mgr._get_base_url({"instance_url": "https://ignored/instances/x"}, "wxo") == (
            REMOTE_INSTANCE_URL
        )

    def test_env_instance_url_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("WXO_INSTANCE_URL", f"  {REMOTE_INSTANCE_URL}  ")
        mgr = LLMManager()
        assert mgr._get_base_url({}, "wxo") == REMOTE_INSTANCE_URL

    def test_toml_url_strips_whitespace(self):
        mgr = LLMManager()
        assert mgr._get_base_url({"instance_url": f" {REMOTE_INSTANCE_URL} "}, "wxo") == (REMOTE_INSTANCE_URL)


class TestWxoCreateInstance:
    def test_client_receives_expected_param_names(self, monkeypatch):
        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {**BASE_MODEL_SETTINGS, "instance_url": REMOTE_INSTANCE_URL, "timeout": 200}
                )

        assert len(FakeChatWxO.instances) == 1
        kwargs = FakeChatWxO.instances[0].kwargs
        assert kwargs["model"] == WXO_DEFAULT_MODEL_NAME
        assert kwargs["instance_url"] == REMOTE_INSTANCE_URL
        assert kwargs["api_key"] == "test-key"
        assert kwargs["max_tokens"] == 100
        assert kwargs["timeout"] == 200.0
        # ChatWxO manages its own OpenAI-side wiring — never pass these directly.
        assert "openai_api_key" not in kwargs
        assert "openai_api_base" not in kwargs
        assert "model_name" not in kwargs

    def test_key_from_settings_ref_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("WXO_API_KEY", "env-key")
        with patch(
            "cuga.backend.llm.models.resolve_secret",
            side_effect=lambda ref: "ref-key" if ref == "MY_WXO_REF" else None,
        ):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {
                        **BASE_MODEL_SETTINGS,
                        "instance_url": REMOTE_INSTANCE_URL,
                        "api_key": "MY_WXO_REF",
                    }
                )

        assert FakeChatWxO.instances[0].kwargs["api_key"] == "ref-key"

    def test_temperature_present_for_non_reasoning_model(self, monkeypatch):
        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {**BASE_MODEL_SETTINGS, "instance_url": REMOTE_INSTANCE_URL, "temperature": 0.3}
                )

        assert FakeChatWxO.instances[0].kwargs["temperature"] == 0.3

    def test_temperature_dropped_for_reasoning_model(self, monkeypatch):
        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {
                        **BASE_MODEL_SETTINGS,
                        "instance_url": REMOTE_INSTANCE_URL,
                        "model": "watsonx/openai/gpt-5",
                    }
                )

        assert "temperature" not in FakeChatWxO.instances[0].kwargs

    def test_optional_sampling_forwarded_only_when_set(self, monkeypatch):
        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {
                        **BASE_MODEL_SETTINGS,
                        "instance_url": REMOTE_INSTANCE_URL,
                        "top_p": 0.9,
                    }
                )

        kwargs = FakeChatWxO.instances[0].kwargs
        assert kwargs["top_p"] == 0.9
        assert "frequency_penalty" not in kwargs
        assert "presence_penalty" not in kwargs
        assert "stop" not in kwargs

    def test_default_headers_never_passed(self, monkeypatch):
        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                mgr._create_llm_instance({**BASE_MODEL_SETTINGS, "instance_url": REMOTE_INSTANCE_URL})

        assert "default_headers" not in FakeChatWxO.instances[0].kwargs

    def test_missing_api_key_on_remote_instance_raises(self, monkeypatch):
        monkeypatch.delenv("WXO_API_KEY", raising=False)
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                with pytest.raises(ValueError, match="WXO_API_KEY"):
                    mgr._create_llm_instance({**BASE_MODEL_SETTINGS, "instance_url": REMOTE_INSTANCE_URL})

    @pytest.mark.parametrize(
        "local_url",
        [
            "http://localhost:4321",
            "http://127.0.0.1:4321",
            "http://[::1]:4321",
            "http://0.0.0.0:4321",
        ],
    )
    def test_missing_api_key_on_local_instance_does_not_raise(self, monkeypatch, local_url):
        monkeypatch.delenv("WXO_API_KEY", raising=False)
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                mgr._create_llm_instance({**BASE_MODEL_SETTINGS, "instance_url": local_url})

        assert FakeChatWxO.instances[0].kwargs["api_key"] is None

    def test_ssl_verify_default_true_no_async_client_override(self, monkeypatch):
        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                mgr._create_llm_instance({**BASE_MODEL_SETTINGS, "instance_url": REMOTE_INSTANCE_URL})

        kwargs = FakeChatWxO.instances[0].kwargs
        assert "verify" not in kwargs
        assert "http_async_client" not in kwargs

    def test_ssl_disabled_passes_verify_false_and_async_client(self, monkeypatch):
        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {**BASE_MODEL_SETTINGS, "instance_url": REMOTE_INSTANCE_URL, "disable_ssl": True}
                )

        kwargs = FakeChatWxO.instances[0].kwargs
        assert kwargs["verify"] is False
        assert kwargs["http_async_client"] is not None

    def test_custom_ca_bundle_passes_verify_path_and_async_client(self, monkeypatch, tmp_path):
        # httpx.AsyncClient(verify=<path>) eagerly loads the file into an SSLContext,
        # so the bundle must be a real, parseable certificate.
        ca_bundle = tmp_path / "ca.pem"
        ca_bundle.write_bytes(_self_signed_cert_pem())
        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {
                        **BASE_MODEL_SETTINGS,
                        "instance_url": REMOTE_INSTANCE_URL,
                        "ssl_ca_bundle": str(ca_bundle),
                    }
                )

        kwargs = FakeChatWxO.instances[0].kwargs
        assert kwargs["verify"] == str(ca_bundle)
        assert kwargs["http_async_client"] is not None

    def test_sdk_missing_raises_actionable_import_error(self, monkeypatch):
        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=None):
                mgr = LLMManager()
                with pytest.raises(ImportError, match=r"cuga\[wxo\]"):
                    mgr._create_llm_instance({**BASE_MODEL_SETTINGS, "instance_url": REMOTE_INSTANCE_URL})


class TestWxoCaching:
    def test_same_settings_reuse_cached_instance(self, monkeypatch):
        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                model_settings = {**BASE_MODEL_SETTINGS, "instance_url": REMOTE_INSTANCE_URL}
                first = mgr.get_model(model_settings)
                second = mgr.get_model(model_settings)

        assert len(FakeChatWxO.instances) == 1
        assert first is second

    def test_different_instance_url_creates_distinct_instances(self, monkeypatch):
        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                mgr.get_model({**BASE_MODEL_SETTINGS, "instance_url": REMOTE_INSTANCE_URL})
                mgr.get_model(
                    {
                        **BASE_MODEL_SETTINGS,
                        "instance_url": "https://api.dl.watson-orchestrate.ibm.com/instances/other",
                    }
                )

        assert len(FakeChatWxO.instances) == 2


class TestWxoContextProfile:
    @pytest.mark.parametrize(
        "model_name,expected",
        [
            ("watsonx/openai/gpt-oss-120b", 131072),
            ("groq/openai/gpt-oss-120b", 131072),
            ("watsonx/meta-llama/llama-3.3-70b-instruct", 128000),
        ],
    )
    def test_known_model_sets_context_profile(self, monkeypatch, model_name, expected):
        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {**BASE_MODEL_SETTINGS, "instance_url": REMOTE_INSTANCE_URL, "model": model_name}
                )

        llm = FakeChatWxO.instances[0]
        assert llm.profile["max_input_tokens"] == expected

    def test_unknown_model_falls_back_to_default_context_size(self, monkeypatch):
        from cuga.backend.cuga_graph.utils.token_counter import DEFAULT_CONTEXT_SIZE

        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {
                        **BASE_MODEL_SETTINGS,
                        "instance_url": REMOTE_INSTANCE_URL,
                        "model": "watsonx/ibm/granite-3-8b-instruct",
                    }
                )

        llm = FakeChatWxO.instances[0]
        assert llm.profile["max_input_tokens"] == DEFAULT_CONTEXT_SIZE


class TestWxoSecretHint:
    def test_env_var_present_resolves_to_wxo_key(self, monkeypatch):
        from cuga.backend.secrets.seed import resolve_llm_api_key_ref

        monkeypatch.delenv("MODEL_NAME", raising=False)
        monkeypatch.setenv("WXO_API_KEY", "test-key")
        assert resolve_llm_api_key_ref() == "db://wxo-api-key"

    def test_default_wxo_model_name_hint_collides_with_watsonx(self, monkeypatch):
        """Pins existing behavior: the default wxO model id embeds "watsonx/" as
        a provider prefix, and the hint matcher's longest-substring-wins rule
        picks "watsonx" over "wxo" for that string. This is a pre-existing
        property of the substring-hint heuristic (shared by other providers
        whose model ids embed another provider's name, e.g. groq's default
        "openai/gpt-oss-20b"), not something introduced by the wxo platform.
        Real wxO setups still resolve correctly because WXO_API_KEY, once set,
        is found directly by resolve_llm_api_key_ref's second pass over actual
        env vars — this hint pass only runs when MODEL_NAME is also exported.
        """
        from cuga.backend.secrets.seed import resolve_llm_api_key_ref

        monkeypatch.delenv("WXO_API_KEY", raising=False)
        monkeypatch.setenv("MODEL_NAME", WXO_DEFAULT_MODEL_NAME)
        assert resolve_llm_api_key_ref() == "db://watsonx-api-key"


class TestWxoUnsupportedPlatform:
    def test_typo_platform_still_raises(self):
        mgr = LLMManager()
        with pytest.raises(ValueError, match="Unsupported platform"):
            mgr._create_llm_instance(
                {**BASE_MODEL_SETTINGS, "platform": "wxo-typo", "model_name": "some-model"}
            )
