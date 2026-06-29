"""Shared fixtures for the A2A integration tests.

These tests target the module that will live at ``cuga.backend.server.a2a``.
Until that module exists, every test using these fixtures will skip via
``pytest.importorskip``. No test in this directory binds a real port — we
drive the FastAPI app via ``httpx.ASGITransport`` so the suite stays
hermetic and CI-friendly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, List, Optional

import pytest


@dataclass
class FakeStreamEvent:
    """Stand-in for cuga's internal StreamEvent.

    The real adapter contract takes whatever CUGA already emits; we keep
    this fake shaped like a (name, data) pair so tests don't depend on
    importing internal types.
    """

    name: str
    data: Any = None
    final: bool = False


@dataclass
class ScriptedGraphRunner:
    """A GraphRunner stub the router can call.

    The real ``build_router`` accepts any callable matching the
    ``GraphRunner`` protocol: ``run(message, context_id) -> AsyncIterator[StreamEvent]``.
    We implement that here so router tests don't need a real graph.
    """

    events: List[FakeStreamEvent] = field(default_factory=list)
    on_run: Optional[Callable[[str, Optional[str]], None]] = None
    received: List[tuple[str, Optional[str]]] = field(default_factory=list)

    async def run(
        self, message: str, context_id: Optional[str] = None, approval: Optional[dict] = None
    ) -> AsyncIterator[FakeStreamEvent]:
        self.received.append((message, context_id))
        if self.on_run is not None:
            self.on_run(message, context_id)
        for ev in self.events:
            yield ev


@pytest.fixture
def fake_event_factory():
    """Factory the tests use to script a stream."""

    def _make(name: str, data: Any = None, final: bool = False) -> FakeStreamEvent:
        return FakeStreamEvent(name=name, data=data, final=final)

    return _make


@pytest.fixture
def scripted_runner(fake_event_factory):
    """A scripted runner that completes after one 'working' + one final event."""

    return ScriptedGraphRunner(
        events=[
            fake_event_factory("agent_thinking", {"text": "planning"}),
            fake_event_factory("final_answer", {"text": "the answer is 42"}, final=True),
        ]
    )


@pytest.fixture
def a2a_settings():
    """The minimal settings object the router needs.

    Mirrors the [a2a] block we'll add to settings.toml. Tests construct
    this in-line so we don't touch real settings during unit/integration runs.
    """

    return {
        "enabled": True,
        "path_prefix": "",
        "agent_name": "cuga-test",
        "agent_description": "Test instance of CUGA exposed over A2A.",
        "agent_version": "0.0.0-test",
        "agent_url": "http://test.local",
        "skill_ids": ["delegate_task"],
    }


@pytest.fixture
def app_with_a2a(scripted_runner, a2a_settings):
    """Build a fresh FastAPI app with only the A2A router mounted.

    Mounting only the A2A router (not the full CUGA server) keeps these
    tests fast and isolated. The real ``main.py`` integration is verified
    separately by ``test_main_unchanged_when_disabled``.
    """

    pytest.importorskip("cuga.backend.server.a2a")
    from fastapi import FastAPI

    from cuga.backend.server.a2a import build_router

    app = FastAPI()
    app.include_router(build_router(runner=scripted_runner, settings=a2a_settings))
    return app


@pytest.fixture
def app_a2a_disabled(a2a_settings):
    """A FastAPI app simulating ``a2a.enabled = False``: router never mounted."""

    from fastapi import FastAPI

    return FastAPI()


@pytest.fixture
async def asgi_client(app_with_a2a):
    """An httpx.AsyncClient bound to the in-process app via ASGITransport."""

    httpx = pytest.importorskip("httpx")
    transport = httpx.ASGITransport(app=app_with_a2a)
    async with httpx.AsyncClient(transport=transport, base_url="http://test.local") as client:
        yield client
