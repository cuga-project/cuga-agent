"""Registry app discovery uses a bounded timeout and a failure cooldown."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aiohttp
import pytest

from cuga.backend.tools_env.registry.utils import api_utils
from cuga.backend.tools_env.registry.utils.types import AppDefinition


@pytest.fixture(autouse=True)
def _reset_state():
    api_utils._registry_apps_retry_after.clear()
    api_utils._last_registry_apps.clear()
    yield
    api_utils._registry_apps_retry_after.clear()
    api_utils._last_registry_apps.clear()


class _FakeResponse:
    status = 200

    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


def _session_factory(get_side_effect, timeouts):
    def factory(*args, timeout=None, **kwargs):
        timeouts.append(timeout)
        session = MagicMock()
        session.get = MagicMock(side_effect=get_side_effect)
        cm = MagicMock()

        async def _enter(_self=None):
            return session

        async def _exit(*exc):
            return False

        cm.__aenter__ = _enter
        cm.__aexit__ = _exit
        return cm

    return factory


def _patch_env(tracker_apps=()):
    return (
        patch.object(
            api_utils, "settings", SimpleNamespace(advanced_features=SimpleNamespace(registry=True))
        ),
        patch.object(api_utils, "tracker", SimpleNamespace(apps=list(tracker_apps))),
        patch.object(api_utils, "get_registry_base_url", return_value="http://registry"),
    )


@pytest.mark.unit
def test_registry_client_timeout_is_bounded():
    timeout = api_utils.get_registry_client_timeout()
    assert timeout.connect == api_utils.REGISTRY_CONNECT_TIMEOUT_SECONDS
    assert timeout.total == api_utils.REGISTRY_REQUEST_TIMEOUT_SECONDS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_apps_passes_timeout_and_caches_last_success():
    timeouts = []
    calls = []

    def ok(*args, **kwargs):
        calls.append(1)
        return _FakeResponse([{"name": "a", "url": "http://a"}])

    p1, p2, p3 = _patch_env()
    with p1, p2, p3, patch.object(aiohttp, "ClientSession", _session_factory(ok, timeouts)):
        apps = await api_utils.get_apps()
    assert [a.name for a in apps] == ["a"]
    assert timeouts[0] is not None and timeouts[0].connect == api_utils.REGISTRY_CONNECT_TIMEOUT_SECONDS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failure_cooldown_skips_probe_and_reuses_last_apps():
    timeouts = []
    state = {"fail": False, "calls": 0}

    def get(*args, **kwargs):
        state["calls"] += 1
        if state["fail"]:
            raise aiohttp.ClientConnectionError("down")
        return _FakeResponse([{"name": "a", "url": "http://a"}])

    p1, p2, p3 = _patch_env()
    with p1, p2, p3, patch.object(aiohttp, "ClientSession", _session_factory(get, timeouts)):
        await api_utils.get_apps()
        state["fail"] = True
        first = await api_utils.get_apps()
        calls_after_failure = state["calls"]
        second = await api_utils.get_apps()

    assert [a.name for a in first] == ["a"]
    assert [a.name for a in second] == ["a"]
    assert state["calls"] == calls_after_failure  # no new probe during cooldown


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cooldown_without_cached_apps_raises_without_probing():
    state = {"calls": 0}

    def get(*args, **kwargs):
        state["calls"] += 1
        raise aiohttp.ClientConnectionError("down")

    p1, p2, p3 = _patch_env()
    with p1, p2, p3, patch.object(aiohttp, "ClientSession", _session_factory(get, [])):
        with pytest.raises(aiohttp.ClientConnectionError):
            await api_utils.get_apps()
        with pytest.raises(RuntimeError):
            await api_utils.get_apps()
    assert state["calls"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cooldown_expires_and_registry_is_probed_again():
    state = {"calls": 0}

    def get(*args, **kwargs):
        state["calls"] += 1
        return _FakeResponse([{"name": "a", "url": "http://a"}])

    external = AppDefinition(name="ext", url="http://ext")
    p1, p2, p3 = _patch_env([external])
    with p1, p2, p3, patch.object(aiohttp, "ClientSession", _session_factory(get, [])):
        api_utils._registry_apps_retry_after["http://registry/applications"] = 0.0
        apps = await api_utils.get_apps()
    assert state["calls"] == 1
    assert [a.name for a in apps] == ["a", "ext"]
