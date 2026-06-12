import pytest

from cuga.backend.browser_env.browser.gym_obs import obs_async
from cuga.backend.browser_env.page_understanding.extractor_utils import extract_async
from cuga.backend.tools_env.registry.utils import api_utils


class FailingSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def get(self, *_args, **_kwargs):
        raise RuntimeError("registry unavailable")


@pytest.mark.asyncio
async def test_get_apps_allows_missing_registry_for_webarena(monkeypatch):
    monkeypatch.setattr(api_utils.settings.advanced_features, "registry", True)
    monkeypatch.setattr(api_utils.tracker, "apps", [])
    monkeypatch.setattr(api_utils, "resolved_benchmark", lambda: "webarena")
    monkeypatch.setattr(api_utils.aiohttp, "ClientSession", FailingSession)

    assert await api_utils.get_apps() == []


@pytest.mark.asyncio
async def test_get_apps_still_raises_missing_registry_by_default(monkeypatch):
    monkeypatch.setattr(api_utils.settings.advanced_features, "registry", True)
    monkeypatch.setattr(api_utils.tracker, "apps", [])
    monkeypatch.setattr(api_utils, "resolved_benchmark", lambda: "default")
    monkeypatch.setattr(api_utils.aiohttp, "ClientSession", FailingSession)

    with pytest.raises(RuntimeError, match="registry unavailable"):
        await api_utils.get_apps()


def test_browser_obs_cleanup_treats_navigation_context_as_transient():
    error = Exception("Execution context was destroyed, most likely because of a navigation")

    assert obs_async._is_transient_frame_error(error)
    assert extract_async._is_transient_frame_error(error)
