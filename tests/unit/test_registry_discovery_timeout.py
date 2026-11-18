"""Registry discovery uses a bounded timeout and does not re-probe a down registry every turn."""

from unittest.mock import patch

import aiohttp
import pytest

from cuga.backend.tools_env.registry.utils import api_utils


class _FakeResponse:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return ""


class _FakeSession:
    """Stand-in for aiohttp.ClientSession; ``behavior`` is a payload or an exception."""

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
    assert timeout.total == api_utils.REGISTRY_REQUEST_TIMEOUT_SECONDS
    assert timeout.connect < timeout.total


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_apps_passes_timeout_to_session():
    _FakeSession.behavior = [{"name": "a", "url": "http://a", "description": "d"}]
    apps = await api_utils.get_apps()
    assert [a.name for a in apps] == ["a"]
    assert _FakeSession.timeouts[0].connect == api_utils.REGISTRY_CONNECT_TIMEOUT_SECONDS


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
