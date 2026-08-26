"""Integration test for the Manage-UI publish path with a wxO provider config.

``create_llm_from_config`` (models.py) is the function manage_routes calls after
a config publish/draft-save. This pins that a "wxo" provider config flows
through to the same ``_create_llm_instance`` wxo branch exercised in
``tests/unit/test_llm_wxo_provider.py`` — in particular that the UI's
``base_url`` field lands on ChatWxO's ``instance_url``, and that a missing key
surfaces as the wxo branch's own ``ValueError`` (callers of
``create_llm_from_config`` are documented to catch ``ValueError`` and fall back
to env/TOML settings).

Uses vault-mode settings (``force_env=False``) so the config's own
``provider``/``model`` are honored — in local mode ``create_llm_from_config``
overrides both from ``settings.agent.code.model`` (see
``tests/integration/test_llm_config_publish.py`` for that path).
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cuga.backend.llm.models import LLMManager, set_current_llm_override

REMOTE_INSTANCE_URL = "https://api.dl.watson-orchestrate.ibm.com/instances/abc-123"


class FakeChatWxO:
    """Minimal stand-in for ChatWxO that records constructor kwargs.

    See ``tests/unit/test_llm_wxo_provider.py`` for the rationale (a bare
    ``MagicMock`` would misleadingly satisfy attribute checks that
    ``_update_model_parameters`` performs on the returned model).
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


def _vault_settings_module_stub():
    """Vault-mode settings so create_llm_from_config honors llm_cfg's own provider.

    Mirrors tests/integration/test_llm_config_publish.py's stub: models.py
    binds `settings` at import, so patch both `cuga.config.settings` and
    `cuga.backend.llm.models.settings`.
    """
    return SimpleNamespace(
        secrets=SimpleNamespace(mode="vault", force_env=False),
        agent=SimpleNamespace(code=SimpleNamespace(model={"platform": "wxo", "max_tokens": 16000})),
        connections=SimpleNamespace(ssl_ca_bundle=None, llm_http_timeout=None),
    )


@contextmanager
def vault_mode_settings():
    stub = _vault_settings_module_stub()
    with (
        patch("cuga.config.settings", stub),
        patch("cuga.backend.llm.models.settings", stub),
    ):
        yield


@pytest.fixture(autouse=True)
def reset_llm_state():
    mgr = LLMManager()
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)
    FakeChatWxO.instances.clear()
    yield
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)
    FakeChatWxO.instances.clear()


class TestCreateLlmFromConfigWxo:
    def test_base_url_lands_on_instance_url(self, monkeypatch):
        from cuga.backend.llm.models import create_llm_from_config

        monkeypatch.setenv("WXO_API_KEY", "test-key")
        with vault_mode_settings():
            with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
                with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                    create_llm_from_config(
                        {
                            "provider": "wxo",
                            "model": "watsonx/openai/gpt-oss-120b",
                            "base_url": REMOTE_INSTANCE_URL,
                            "api_key": "test-key",
                        }
                    )

        assert len(FakeChatWxO.instances) == 1
        kwargs = FakeChatWxO.instances[0].kwargs
        assert kwargs["instance_url"] == REMOTE_INSTANCE_URL
        assert kwargs["model"] == "watsonx/openai/gpt-oss-120b"

    def test_missing_key_raises_value_error(self, monkeypatch):
        from cuga.backend.llm.models import create_llm_from_config

        monkeypatch.delenv("WXO_API_KEY", raising=False)
        with vault_mode_settings():
            with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
                with patch("cuga.backend.llm.models._get_chat_wxo", return_value=FakeChatWxO):
                    with pytest.raises(ValueError, match="WXO_API_KEY"):
                        create_llm_from_config(
                            {
                                "provider": "wxo",
                                "model": "watsonx/openai/gpt-oss-120b",
                                "base_url": REMOTE_INSTANCE_URL,
                            }
                        )
