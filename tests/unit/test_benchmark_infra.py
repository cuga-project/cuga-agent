import asyncio

import pytest

from cuga.backend.browser_env.browser.gym_obs import obs_async
from cuga.backend.browser_env.page_understanding.extractor_utils import extract_async
from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.utils.event_porcessors.action_agent_event_processor import (
    ActionAgentEventProcessor,
)
from cuga.backend.tools_env.registry.utils import api_utils


class FailingSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def get(self, *_args, **_kwargs):
        raise asyncio.TimeoutError("registry unavailable")


class BrokenResponse:
    status = 500

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def text(self):
        return "registry error"


class BrokenResponseSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def get(self, *_args, **_kwargs):
        return BrokenResponse()


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

    with pytest.raises(asyncio.TimeoutError, match="registry unavailable"):
        await api_utils.get_apps()


@pytest.mark.asyncio
async def test_get_apps_raises_broken_registry_response_for_webarena(monkeypatch):
    monkeypatch.setattr(api_utils.settings.advanced_features, "registry", True)
    monkeypatch.setattr(api_utils.tracker, "apps", [])
    monkeypatch.setattr(api_utils, "resolved_benchmark", lambda: "webarena")
    monkeypatch.setattr(api_utils.aiohttp, "ClientSession", BrokenResponseSession)

    with pytest.raises(Exception, match="Request failed with status 500"):
        await api_utils.get_apps()


def test_browser_obs_cleanup_treats_navigation_context_as_transient():
    error = Exception("Execution context was destroyed, most likely because of a navigation")

    assert obs_async._is_transient_frame_error(error)
    assert extract_async._is_transient_frame_error(error)


def test_webmcp_feedback_updates_tracker():
    tracker = ActivityTracker()
    tracker.reset("unit-test")
    processor = ActionAgentEventProcessor(page=None, tool_handlers={})

    processor.collect_feedback("webmcp_call", "", {}, error_message="", message="zip code 15213")
    processor.collect_feedback("observe_page", "", {}, error_message="", message="Full page observation requested.")
    processor.collect_feedback("webmcp_call", "", {}, error_message="tool failed")

    assert tracker.webmcp_calls == 1
    assert tracker.observe_page_calls == 1
    assert tracker.webmcp_tool_result_visible is True

    tracker.reset("unit-test")

    assert tracker.webmcp_calls == 0
    assert tracker.observe_page_calls == 0
    assert tracker.webmcp_tool_result_visible is False
