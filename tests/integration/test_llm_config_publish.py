"""
Integration tests for LLM config publish flow.

Tests that:
1. Calling the manage publish path with an llm config in vault mode correctly
   sets app_state.current_llm (config-driven model).
2. Building the agent graph uses create_llm_from_config when llm_config is set.
3. Invoking the model with a bad API key surfaces an auth/connection error.

These tests do NOT require an external server — they call the internal functions
directly using pytest-asyncio.
"""

import pytest
from unittest.mock import patch, MagicMock

from cuga.backend.llm.models import (
    LLMManager,
    _ModelSettingsWrap,
    set_current_llm_override,
)


BAD_KEY = "sk-bad-key-000000000000"

VAULT_MODE_CONFIG = {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "api_key": BAD_KEY,
    "base_url": "",
    "temperature": 0.1,
    "disable_ssl": False,
    "auth_type": "api_key",
    "auth_header_name": "Authorization",
}


@pytest.fixture(autouse=True)
def reset_llm_state():
    mgr = LLMManager()
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)
    yield
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_state():
    """Minimal app_state stub required by _apply_published_config."""
    state = MagicMock()
    state.tools_include_by_app = None
    state.policy_system = None
    return state


def _vault_settings_stub():
    """Return a mock settings.secrets that looks like vault mode with force_env=False."""
    s = MagicMock()
    s.mode = "vault"
    s.force_env = False
    return s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPublishSetsLLMOverride:
    """_apply_published_config sets app_state.current_llm in vault/local mode."""

    @pytest.mark.asyncio
    async def test_vault_mode_sets_current_llm(self):
        """Publishing with vault mode creates LLM and stores it on app_state.current_llm."""
        from cuga.backend.server.manage_routes import _apply_published_config

        config = {"llm": VAULT_MODE_CONFIG}
        app_state = _make_app_state()

        with patch("cuga.backend.server.manage_routes.logger"):
            with patch("cuga.config.settings") as mock_settings:
                mock_settings.secrets = _vault_settings_stub()
                mock_settings.agent.code.model.get = MagicMock(return_value=16000)
                with patch("cuga.backend.llm.models.settings", mock_settings):
                    await _apply_published_config(app_state, config)

        assert getattr(app_state, "current_llm", None) is not None
        llm = app_state.current_llm
        assert (
            getattr(llm, "model_name", None) == "gpt-4o-mini" or getattr(llm, "model", None) == "gpt-4o-mini"
        )

    @pytest.mark.asyncio
    async def test_apply_published_config_vault_mode(self):
        """_apply_published_config in vault mode (force_env=False) sets app_state.current_llm."""
        from cuga.backend.server.manage_routes import _apply_published_config

        config = {"llm": VAULT_MODE_CONFIG}
        app_state = _make_app_state()

        fake_secrets = _vault_settings_stub()
        with patch("cuga.config.settings") as mock_cfg:
            mock_cfg.secrets = fake_secrets
            with patch("cuga.backend.server.manage_routes.logger"):
                import cuga.config as cfg_mod

                real_secrets = getattr(cfg_mod.settings, "secrets", None)
                try:
                    cfg_mod.settings.secrets = fake_secrets
                    await _apply_published_config(app_state, config)
                finally:
                    cfg_mod.settings.secrets = real_secrets

        assert getattr(app_state, "current_llm", None) is not None, (
            "current_llm should be set after publish in vault mode"
        )
        llm = app_state.current_llm
        assert (
            getattr(llm, "model_name", None) == "gpt-4o-mini" or getattr(llm, "model", None) == "gpt-4o-mini"
        )


class TestDynamicAgentGraphPicksUpLLMConfig:
    """DynamicAgentGraph.build_graph uses create_llm_from_config when llm_config is set."""

    @pytest.mark.asyncio
    async def test_build_graph_uses_create_llm_from_config(self):
        """When llm_config is set, build_graph calls create_llm_from_config with it."""
        from unittest.mock import AsyncMock
        from cuga.backend.cuga_graph.graph import DynamicAgentGraph
        from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import ToolProviderInterface

        mock_tp = MagicMock(spec=ToolProviderInterface)
        mock_tp.initialize = AsyncMock()
        mock_tp.get_apps = AsyncMock(return_value=[])

        captured = {}

        def spy_create_llm(cfg):
            captured["llm_cfg"] = dict(cfg) if cfg else {}
            return MagicMock()

        with patch("cuga.backend.cuga_graph.graph.create_llm_from_config", side_effect=spy_create_llm):
            agent = DynamicAgentGraph(
                None,
                tool_provider=mock_tp,
                llm_config=VAULT_MODE_CONFIG,
            )
            await agent.build_graph()

        cfg = captured.get("llm_cfg", {})
        assert cfg.get("provider") == "openai" or cfg.get("platform") == "openai"
        assert cfg.get("model") == "gpt-4o-mini"
        assert cfg.get("api_key") == BAD_KEY


class TestBadApiKeyRaisesOnInvoke:
    """End-to-end: bad API key in vault mode config raises auth error on LLM invocation."""

    def test_openai_bad_key_override_raises_auth_error(self):
        """
        Simulate the full path:
          1. Set override (as _apply_published_config would in vault mode)
          2. Call get_model (as call_model node does at invocation time)
          3. invoke() → expect auth error
        """
        set_current_llm_override(
            {
                "platform": "openai",
                "model": "gpt-4o-mini",
                "api_key": BAD_KEY,
                "url": None,
                "temperature": 0.1,
                "disable_ssl": False,
                "auth_type": "api_key",
                "auth_header_name": "Authorization",
            }
        )

        base_model_settings = _ModelSettingsWrap(
            {
                "platform": "openai",
                "model": "gpt-4o-mini",
                "max_tokens": 100,
                "temperature": 0.1,
            }
        )

        mgr = LLMManager()
        model = mgr.get_model(base_model_settings)

        with pytest.raises(Exception) as exc_info:
            model.invoke([{"role": "user", "content": "ping"}])

        error_msg = str(exc_info.value).lower()
        assert any(
            kw in error_msg for kw in ("auth", "api key", "invalid", "401", "403", "incorrect", "connection")
        ), f"Expected auth/connection error, got: {exc_info.value}"

    @pytest.mark.asyncio
    async def test_openai_bad_key_override_raises_auth_error_async(self):
        """Same as above but async — matches call_model ainvoke path."""
        set_current_llm_override(
            {
                "platform": "openai",
                "model": "gpt-4o-mini",
                "api_key": BAD_KEY,
                "url": None,
            }
        )

        base_model_settings = _ModelSettingsWrap(
            {
                "platform": "openai",
                "model": "gpt-4o-mini",
                "max_tokens": 100,
                "temperature": 0.1,
            }
        )

        mgr = LLMManager()
        model = mgr.get_model(base_model_settings)

        with pytest.raises(Exception) as exc_info:
            await model.ainvoke([{"role": "user", "content": "ping"}])

        error_msg = str(exc_info.value).lower()
        assert any(
            kw in error_msg for kw in ("auth", "api key", "invalid", "401", "403", "incorrect", "connection")
        ), f"Expected auth/connection error, got: {exc_info.value}"

    def test_groq_bad_key_override_raises_auth_error(self):
        """Groq provider with bad key in vault mode raises auth error."""
        set_current_llm_override(
            {
                "platform": "groq",
                "model": "llama3-8b-8192",
                "api_key": "gsk_bad_key_000000000000",
                "url": None,
            }
        )

        base_model_settings = _ModelSettingsWrap(
            {
                "platform": "groq",
                "model": "llama3-8b-8192",
                "max_tokens": 100,
                "temperature": 0.1,
            }
        )

        mgr = LLMManager()
        model = mgr.get_model(base_model_settings)

        with pytest.raises(Exception) as exc_info:
            model.invoke([{"role": "user", "content": "ping"}])

        error_msg = str(exc_info.value).lower()
        assert any(
            kw in error_msg
            for kw in ("auth", "api key", "invalid", "401", "403", "incorrect", "connection", "groq")
        ), f"Expected auth/connection error, got: {exc_info.value}"
