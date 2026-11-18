"""Registry discovery uses a bounded timeout and does not re-probe a down registry every turn."""

from unittest.mock import patch

import aiohttp
import pytest

from cuga.backend.tools_env.registry.utils import api_utils


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class _FakeSession:
    """Stand-in for aiohttp.ClientSession; ``behavior`` is a payload, response, or exception."""

    calls = 0
    timeouts = []
    behavior = None

    def __init__(self, *args, timeout=None, **kwargs):
        type(self).timeouts.append(timeout)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, headers=None):
        type(self).calls += 1
        if isinstance(type(self).behavior, Exception):
            raise type(self).behavior
        if isinstance(type(self).behavior, _FakeResponse):
            return type(self).behavior
        return _FakeResponse(type(self).behavior)


@pytest.fixture(autouse=True)
def _reset_state():
    api_utils._last_registry_apps.clear()
    api_utils._registry_failed_at.clear()
    _FakeSession.calls = 0
    _FakeSession.timeouts = []
    _FakeSession.behavior = None
    with (
        patch.object(api_utils.settings.advanced_features, "registry", True, create=True),
        patch.object(api_utils.tracker, "apps", [], create=True),
        patch.object(api_utils.aiohttp, "ClientSession", _FakeSession),
    ):
        yield
    api_utils._last_registry_apps.clear()
    api_utils._registry_failed_at.clear()


@pytest.mark.unit
def test_registry_client_timeout_has_explicit_connect_timeout():
    timeout = api_utils.registry_client_timeout()
    assert isinstance(timeout, aiohttp.ClientTimeout)
    assert timeout.connect == api_utils.REGISTRY_CONNECT_TIMEOUT_SECONDS
    assert timeout.total is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_apps_passes_timeout_to_session():
    _FakeSession.behavior = [{"name": "a", "url": "http://a", "description": "d"}]
    apps = await api_utils.get_apps()
    assert [a.name for a in apps] == ["a"]
    assert _FakeSession.timeouts[0].connect == api_utils.REGISTRY_CONNECT_TIMEOUT_SECONDS
    assert _FakeSession.timeouts[0].total is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unavailable_registry_is_not_reprobed_during_cooldown():
    _FakeSession.behavior = ConnectionError("down")
    with pytest.raises(ConnectionError):
        await api_utils.get_apps()
    assert _FakeSession.calls == 1

    with pytest.raises(ConnectionError):
        await api_utils.get_apps()
    assert _FakeSession.calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_last_successful_apps_served_while_registry_is_down():
    _FakeSession.behavior = [{"name": "a", "url": "http://a", "description": "d"}]
    await api_utils.get_apps()

    _FakeSession.behavior = ConnectionError("down")
    apps = await api_utils.get_apps()
    assert [a.name for a in apps] == ["a"]
    calls_after_failure = _FakeSession.calls

    apps = await api_utils.get_apps()
    assert [a.name for a in apps] == ["a"]
    assert _FakeSession.calls == calls_after_failure


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registry_is_probed_again_after_cooldown():
    _FakeSession.behavior = ConnectionError("down")
    with pytest.raises(ConnectionError):
        await api_utils.get_apps()

    for url in list(api_utils._registry_failed_at):
        api_utils._registry_failed_at[url] -= api_utils.REGISTRY_FAILURE_COOLDOWN_SECONDS + 1

    _FakeSession.behavior = [{"name": "b", "url": "http://b", "description": "d"}]
    apps = await api_utils.get_apps()
    assert [a.name for a in apps] == ["b"]
    assert api_utils._registry_failed_at == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_slow_registry_response_succeeds_without_cooldown():
    _FakeSession.behavior = [{"name": "slow_app", "url": "http://slow", "description": "d"}]
    apps = await api_utils.get_apps()
    assert [a.name for a in apps] == ["slow_app"]
    assert api_utils._registry_failed_at == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_does_not_start_cooldown():
    _FakeSession.behavior = TimeoutError("read timed out")
    with pytest.raises(TimeoutError):
        await api_utils.get_apps()
    assert api_utils._registry_failed_at == {}

    _FakeSession.behavior = [{"name": "recovered", "url": "http://rec", "description": "d"}]
    apps = await api_utils.get_apps()
    assert [a.name for a in apps] == ["recovered"]
    assert _FakeSession.calls == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_200_response_does_not_start_cooldown():
    _FakeSession.behavior = _FakeResponse("Internal Server Error", status=500)
    with pytest.raises(Exception, match="500"):
        await api_utils.get_apps()
    assert api_utils._registry_failed_at == {}

    _FakeSession.behavior = [{"name": "ok", "url": "http://ok", "description": "d"}]
    apps = await api_utils.get_apps()
    assert [a.name for a in apps] == ["ok"]
    assert _FakeSession.calls == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_200_response_returns_last_known_apps_without_cooldown():
    _FakeSession.behavior = [{"name": "cached_app", "url": "http://c", "description": "d"}]
    await api_utils.get_apps()

    _FakeSession.behavior = _FakeResponse("Bad Gateway", status=502)
    apps = await api_utils.get_apps()
    assert [a.name for a in apps] == ["cached_app"]
    assert api_utils._registry_failed_at == {}

    _FakeSession.behavior = [{"name": "fresh_app", "url": "http://f", "description": "d"}]
    apps = await api_utils.get_apps()
    assert [a.name for a in apps] == ["fresh_app"]
    assert _FakeSession.calls == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_different_agent_ids_have_separate_cache_and_cooldown():
    _FakeSession.behavior = [{"name": "app_agent1", "url": "http://1", "description": "d"}]
    apps1 = await api_utils.get_apps(agent_id="agent-1")
    assert [a.name for a in apps1] == ["app_agent1"]

    _FakeSession.behavior = [{"name": "app_agent2", "url": "http://2", "description": "d"}]
    apps2 = await api_utils.get_apps(agent_id="agent-2")
    assert [a.name for a in apps2] == ["app_agent2"]

    # agent-1 encounters connection failure -> goes into cooldown
    _FakeSession.behavior = ConnectionError("agent-1 down")
    apps1_cached = await api_utils.get_apps(agent_id="agent-1")
    assert [a.name for a in apps1_cached] == ["app_agent1"]

    # agent-2 registry is probed normally and not in cooldown
    _FakeSession.behavior = [{"name": "app_agent2_v2", "url": "http://2", "description": "d"}]
    apps2_updated = await api_utils.get_apps(agent_id="agent-2")
    assert [a.name for a in apps2_updated] == ["app_agent2_v2"]

    # agent-1 is still in cooldown and serves cache without probing
    calls_before = _FakeSession.calls
    apps1_cooldown = await api_utils.get_apps(agent_id="agent-1")
    assert [a.name for a in apps1_cooldown] == ["app_agent1"]
    assert _FakeSession.calls == calls_before
