"""``auth: {type: oauth2_agent}`` — the agent logs in to AppWorld apps itself.

With ``oauth2`` the registry fetches passwords and logs in for the agent. With
``oauth2_agent`` it must never do that: it only keeps the token returned by the
agent's own ``/auth/token`` call and attaches it to later calls of that app.
Without such a token the call goes out bare, so the app's own 401 tells the
agent to log in.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cuga.backend.tools_env.registry.config.config_loader import Auth
from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
from cuga.backend.tools_env.registry.registry import api_registry as api_registry_module
from cuga.backend.tools_env.registry.registry.api_registry import ApiRegistry
from cuga.backend.tools_env.registry.registry.authentication.agent_login_auth_manager import (
    AgentLoginAuthManager,
)
from cuga.backend.tools_env.registry.registry.authentication.base_auth_manager import REFRESH_AFTER_SECONDS
from cuga.config import settings

pytestmark = pytest.mark.unit

AGENT = Auth(type="oauth2_agent")
AUTO = Auth(type="oauth2")


class _Text:
    def __init__(self, text):
        self.text = text


def _registry(auth_by_app):
    manager = MCPManager(config={})
    manager.auth_config = dict(auth_by_app)
    manager.call_tool = AsyncMock(return_value=[_Text('{"ok": true}')])
    return manager, ApiRegistry(client=manager)


def _sent_headers(manager):
    return manager.call_tool.await_args.kwargs["headers"]


class _NoLoginAllowed:
    """Stands in for AppWorldAuthManager; any use means the registry tried to log in."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("AppWorldAuthManager must not be created for oauth2_agent apps")


# ── AgentLoginAuthManager ─────────────────────────────────────────────────────


def test_manager_without_login_has_no_token():
    assert AgentLoginAuthManager().get_access_token("spotify") is None


def test_manager_returns_the_captured_token():
    m = AgentLoginAuthManager()
    m._store("spotify", "tok-1")
    assert m.get_access_token("spotify") == "tok-1"


def test_stale_token_is_kept_not_refreshed():
    m = AgentLoginAuthManager()
    m._store("spotify", "tok-1")
    m._token_times["spotify"] -= REFRESH_AFTER_SECONDS + 1
    assert m.get_access_token("spotify") == "tok-1"
    assert m.get_stored_tokens() == {"spotify": "tok-1"}


# ── Registry: calls ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_without_agent_login_sends_no_token(monkeypatch):
    monkeypatch.setattr(api_registry_module, "AppWorldAuthManager", _NoLoginAllowed)
    manager, registry = _registry({"spotify": AGENT})
    await registry.call_function("spotify", "spotify_show_playlist_library", {}, auth_config=AGENT)
    assert "Authorization" not in _sent_headers(manager)
    assert isinstance(registry.auth_manager, AgentLoginAuthManager)


@pytest.mark.asyncio
async def test_captured_token_is_attached_to_later_calls(monkeypatch):
    monkeypatch.setattr(api_registry_module, "AppWorldAuthManager", _NoLoginAllowed)
    manager, registry = _registry({"spotify": AGENT})
    assert registry.store_captured_token("spotify", "tok-1") is True
    await registry.call_function("spotify", "spotify_show_playlist_library", {}, auth_config=AGENT)
    assert _sent_headers(manager)["Authorization"] == "Bearer tok-1"


@pytest.mark.asyncio
async def test_agent_login_call_is_captured_end_to_end(monkeypatch):
    """The agent calls the app's /auth/token API itself; the next call carries its token."""
    monkeypatch.setattr(api_registry_module, "AppWorldAuthManager", _NoLoginAllowed)
    monkeypatch.setattr(settings.advanced_features, "benchmark", "appworld")
    manager, registry = _registry({"spotify": AGENT})
    registry.show_apis_for_app = AsyncMock(
        return_value={"spotify_login_auth_token_post": {"path": "/spotify/auth/token"}}
    )
    manager.call_tool = AsyncMock(return_value=[_Text(json.dumps({"access_token": "tok-login"}))])
    await registry.call_function(
        "spotify", "spotify_login_auth_token_post", {"username": "u", "password": "p"}
    )

    manager.call_tool = AsyncMock(return_value=[_Text('{"ok": true}')])
    await registry.call_function("spotify", "spotify_show_playlist_library", {}, auth_config=AGENT)
    assert _sent_headers(manager)["Authorization"] == "Bearer tok-login"


def test_capture_for_a_non_agent_app_without_manager_is_unchanged():
    _, registry = _registry({"spotify": AUTO})
    assert registry.store_captured_token("spotify", "tok-1") is False
    assert registry.auth_manager is None


@pytest.mark.asyncio
async def test_oauth2_still_logs_in_for_the_agent(monkeypatch):
    class FakeAppWorldAuth:
        def __init__(self, *args, **kwargs):
            self.logins = []

        def get_access_token(self, app):
            self.logins.append(app)
            return f"{app}-auto"

        def get_stored_tokens(self):
            return {}

    monkeypatch.setattr(api_registry_module, "AppWorldAuthManager", FakeAppWorldAuth)
    manager, registry = _registry({"spotify": AUTO})
    await registry.call_function("spotify", "spotify_show_playlist_library", {}, auth_config=AUTO)
    assert _sent_headers(manager)["Authorization"] == "Bearer spotify-auto"
    assert registry.auth_manager.logins == ["spotify"]


# ── Registry: pre-login and reset ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_authenticate_apps_never_logs_in_agent_apps(monkeypatch):
    monkeypatch.setattr(api_registry_module, "AppWorldAuthManager", _NoLoginAllowed)
    manager, registry = _registry({"spotify": AGENT, "gmail": AGENT})
    manager.get_app_names = lambda: ["spotify", "gmail"]
    result = await registry.auth_apps([])
    assert result == {"authenticated": {"spotify": "agent_login", "gmail": "agent_login"}}
    assert registry.auth_manager is None


@pytest.mark.asyncio
async def test_reset_clears_agent_tokens(monkeypatch):
    from cuga.backend.tools_env.registry.registry import api_registry_server as srv

    _, registry = _registry({"spotify": AGENT})
    registry.store_captured_token("spotify", "tok-1")
    manager = registry.auth_manager
    monkeypatch.setattr(srv, "registry", registry, raising=False)  # set at server startup
    monkeypatch.setattr(srv, "rejected_call_guard", SimpleNamespace(reset=lambda: None))
    await srv.reset()
    assert registry.auth_manager is None
    assert manager.get_stored_token("spotify") is None


@pytest.mark.asyncio
async def test_route_stores_login_token_only_in_the_calling_agents_registry(monkeypatch):
    """In database mode a non-default agent's /auth/token response must not reach the
    default registry, or later default-agent calls would carry that agent's token."""
    import json as _json

    from cuga.backend.tools_env.registry.registry import api_registry_server as srv
    from cuga.backend.tools_env.registry.registry.rejected_call_guard import RejectedCallGuard
    from cuga.config import settings

    class FakeText:
        def __init__(self, text):
            self.text = text

    class FakeReg:
        def __init__(self):
            self.stored = {}

        async def show_apis_for_app(self, app_name):
            return {"login": {"secure": False, "method": "POST", "path": "/spotify/auth/token"}}

        async def call_function(self, **kwargs):
            return [FakeText(_json.dumps({"access_token": "tok-other", "token_type": "Bearer"}))]

        def store_captured_token(self, app_name, token):
            self.stored[app_name] = token
            return True

    default_reg, other_reg = FakeReg(), FakeReg()
    manager = SimpleNamespace(auth_config={})

    async def registry_for(agent_id, retry_on_empty=False):
        assert agent_id == "other-agent"
        return manager, other_reg

    monkeypatch.setattr(srv, "database_mode", True)
    monkeypatch.setattr(srv, "registry", default_reg, raising=False)
    monkeypatch.setattr(srv, "mcp_manager", manager, raising=False)
    monkeypatch.setattr(srv, "_get_or_create_registry", registry_for)
    monkeypatch.setattr(srv, "rejected_call_guard", RejectedCallGuard())
    monkeypatch.setattr(settings.advanced_features, "benchmark", "appworld")

    request = srv.FunctionCallRequest(app_name="spotify", function_name="login", args={})
    await srv.call_mcp_function(request, agent_id="other-agent")

    assert other_reg.stored == {"spotify": "tok-other"}
    assert default_reg.stored == {}
