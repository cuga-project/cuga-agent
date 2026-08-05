"""The split: eventing as its own service, CUGA reached over HTTP via /run.

The contract that matters is that NOTHING on the wire changes — /invoke and /api/events/* are the
same in both topologies, so every harness and every armed subscription keeps working. These tests
pin that, plus the /run hop's own behaviour (auth, retries, error surfacing).
"""
import json
import os

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.events.agent_store import AgentStore
from cuga.backend.events.runtime import AgentSpec, HttpRuntime, make_runtime


class _FakeCuga:
    """Stands in for the CUGA service's POST /run."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append({"url": str(request.url),
                           "headers": dict(request.headers),
                           "body": json.loads(request.content or b"{}")})
        status, payload = self.replies.pop(0) if self.replies else (200, {"status": "ok", "answer": "hi"})
        return httpx.Response(status, json=payload)


@pytest.fixture(autouse=True)
def _isolate_loopback_port(monkeypatch):
    """create_app() repoints EVENTS_CUGA_PORT at itself — by design (the loopback /invoke belongs
    to the eventing service, not CUGA). But os.environ is process-global, so without this the
    setting LEAKED into every later test in the session: they went on POSTing to :8100, and if
    anything was actually listening there (a developer running `make run-events` alongside the
    suite) the run hung on real network calls. Registering the var with monkeypatch first means
    pytest restores it at teardown.
    """
    monkeypatch.setenv("EVENTS_CUGA_PORT", os.environ.get("EVENTS_CUGA_PORT", "7860"))


@pytest.fixture
def patch_httpx(monkeypatch):
    def _install(fake):
        real = httpx.AsyncClient

        def factory(*a, **kw):
            kw["transport"] = httpx.MockTransport(fake.handler)
            return real(*a, **kw)

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return fake

    return _install


def _rt(**kw):
    store = AgentStore(":memory:")
    store.upsert("default", AgentSpec(name="cuga", prompt="c", integrations=[]))
    return HttpRuntime(agent_store=store, base_url="http://cuga.test", token="t0k", **kw)


@pytest.mark.asyncio
async def test_run_calls_cuga_and_returns_the_answer(patch_httpx):
    fake = patch_httpx(_FakeCuga((200, {"status": "ok", "answer": "IBM is $237.10"})))
    out = await _rt().run("cuga", "sub_1", "Report the IBM price", scope="default")
    assert out == "IBM is $237.10"
    call = fake.calls[0]
    assert call["url"].endswith("/run")
    assert call["headers"]["x-gateway-token"] == "t0k"          # the hop is authenticated
    assert call["body"]["query"] == "Report the IBM price"
    assert call["body"]["thread_id"] == "sub_1"                  # KB/session ride on the thread


@pytest.mark.asyncio
async def test_run_retries_a_5xx_then_succeeds(patch_httpx):
    fake = patch_httpx(_FakeCuga((503, {"error": "starting"}),
                                 (200, {"status": "ok", "answer": "second try"})))
    assert await _rt().run("cuga", "t", "x", scope="default") == "second try"
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_run_does_not_retry_a_4xx(patch_httpx):
    """A bad token or malformed body is our fault — retrying just multiplies the failure."""
    fake = patch_httpx(_FakeCuga((401, {"error": "bad token"})))
    with pytest.raises(RuntimeError, match="401"):
        await _rt().run("cuga", "t", "x", scope="default")
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_run_surfaces_an_agent_error_rather_than_an_empty_answer(patch_httpx):
    patch_httpx(_FakeCuga((200, {"status": "error", "answer": "", "error": "tool exploded"})))
    with pytest.raises(RuntimeError, match="tool exploded"):
        await _rt().run("cuga", "t", "x", scope="default")


class _FakeCugaRoster:
    """Stands in for CUGA's GET /run/agents (and the older /api/agents fallback)."""

    def __init__(self, agents, *, path="/run/agents", status=200):
        self.agents, self.path, self.status = agents, path, status
        self.paths = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path != self.path:
            return httpx.Response(404, json={"error": "no such route"})
        return httpx.Response(self.status, json={"ok": True, "agents": self.agents})


@pytest.fixture
def patch_httpx_sync(monkeypatch):
    """The roster read is a SYNC httpx.get (it happens inside get_agent/list_agents)."""

    def _install(fake):
        def _get(url, **kw):
            req = httpx.Request("GET", url, headers=kw.get("headers") or {})
            return fake.handler(req)

        monkeypatch.setattr(httpx, "get", _get)
        return fake

    return _install


def _rt_empty_store(**kw):
    """No local agents — the split's real shape: the roster lives on the CUGA side."""
    return HttpRuntime(agent_store=AgentStore(":memory:"), base_url="http://cuga.test",
                       token="t0k", **kw)


def test_get_agent_resolves_a_sub_agent_from_cugas_roster(patch_httpx_sync):
    """THE SPLIT WEBHOOK BUG: ?agent=incident_triage 404'd because only this process's (empty)
    store was consulted. The roster belongs to whoever executes — ask CUGA."""
    patch_httpx_sync(_FakeCugaRoster([{"name": "cuga", "description": "the supervisor"},
                                      {"name": "incident_triage", "description": "triage"}]))
    rt = _rt_empty_store()
    assert rt.get_agent("incident_triage", scope="default").name == "incident_triage"
    assert rt.get_agent("cuga", scope="default") is not None       # always addressable
    assert rt.get_agent("not_a_real_agent", scope="default") is None


def test_list_agents_reports_cugas_roster_not_a_guess(patch_httpx_sync):
    patch_httpx_sync(_FakeCugaRoster([{"name": "cuga"}, {"name": "pricebot"}, {"name": "geobot"}]))
    assert [a.name for a in _rt_empty_store().list_agents(scope="default")] == [
        "cuga", "pricebot", "geobot"]


def test_roster_falls_back_to_api_agents_then_to_the_supervisor(patch_httpx_sync):
    """An older CUGA has no /run/agents; and if nothing answers, 'cuga' still exists."""
    fake = patch_httpx_sync(_FakeCugaRoster([{"name": "legacy"}], path="/api/agents"))
    assert [a.name for a in _rt_empty_store().list_agents(scope="default")] == ["legacy"]
    assert fake.paths == ["/run/agents", "/api/agents"]            # tried the machine seam first

    patch_httpx_sync(_FakeCugaRoster([], path="/nowhere"))
    assert [a.name for a in _rt_empty_store().list_agents(scope="default")] == ["cuga"]


def test_roster_is_cached_not_re_read_per_call(patch_httpx_sync):
    fake = patch_httpx_sync(_FakeCugaRoster([{"name": "cuga"}, {"name": "pricebot"}]))
    rt = _rt_empty_store()
    for _ in range(5):
        rt.get_agent("pricebot", scope="default")
    assert fake.paths == ["/run/agents"]


def test_cugas_roster_beats_a_stale_local_row(patch_httpx_sync):
    """A leftover row in ~/.cuga/events.db from an earlier run used to mask the live roster —
    the service reported 1 agent while CUGA was serving 9. CUGA wins."""
    patch_httpx_sync(_FakeCugaRoster([{"name": "cuga"}, {"name": "pricebot"}]))
    store = AgentStore(":memory:")
    store.upsert("default", AgentSpec(name="Digital Sales Agent", prompt="stale", integrations=[]))
    rt = HttpRuntime(agent_store=store, base_url="http://cuga.test", token="t0k")
    assert [a.name for a in rt.list_agents(scope="default")] == ["cuga", "pricebot"]


def test_local_store_is_the_fallback_when_cuga_is_unreachable(patch_httpx_sync):
    patch_httpx_sync(_FakeCugaRoster([], path="/nowhere"))
    store = AgentStore(":memory:")
    store.upsert("default", AgentSpec(name="offline_agent", prompt="p", integrations=[]))
    rt = HttpRuntime(agent_store=store, base_url="http://cuga.test", token="t0k")
    assert [a.name for a in rt.list_agents(scope="default")] == ["offline_agent"]


@pytest.mark.asyncio
async def test_run_carries_the_pinned_agent_across_the_hop(patch_httpx, patch_httpx_sync):
    """In-process runtimes know which agent was asked for; over HTTP it must be said out loud or a
    pinned specialist silently degrades to generic routing."""
    patch_httpx_sync(_FakeCugaRoster([{"name": "cuga"}, {"name": "incident_triage"}]))
    fake = patch_httpx(_FakeCuga((200, {"status": "ok", "answer": "P1"})))
    rt = _rt_empty_store()
    await rt.run("incident_triage", "hook:monitoring", "triage this", scope="default")
    assert fake.calls[0]["body"]["agent"] == "incident_triage"


def test_make_runtime_http_backend():
    rt = make_runtime("http", agent_store=AgentStore(":memory:"), cuga_url="http://x.test")
    assert isinstance(rt, HttpRuntime)


def test_standalone_service_serves_the_same_wire_contract(monkeypatch):
    """The split must be invisible to callers: same routes, same shapes."""
    monkeypatch.setenv("EVENTS_DB", ":memory:")
    monkeypatch.setenv("GATEWAY_TOKEN", "")
    from cuga.backend.events.service import create_app

    c = TestClient(create_app())
    assert c.get("/health").json()["service"] == "events"
    for path in ("/api/events/status", "/api/events/subscriptions", "/api/events/channels",
                 "/api/events/integrations", "/api/events/runs", "/api/events/agents"):
        assert c.get(path).status_code == 200, path
    # and the HITL arming dialogue behaves identically to the mounted deployment
    out = c.post("/api/concierge",
                 json={"text": "/automate every 5 minutes send IBM stock price",
                       "thread_id": "web:local"}).json()
    assert out["state"] == "confirm" and "IBM" in out["summary"]["prompt"]


def test_events_routes_are_identical_in_both_topologies(monkeypatch):
    """A route added to the mounted app but missing from the service (or vice-versa) would break
    one deployment silently — compare the actual route tables."""
    monkeypatch.setenv("EVENTS_DB", ":memory:")
    from cuga.backend.events.app import register_events_routes
    from cuga.backend.events.concierge import Concierge
    from cuga.backend.events.service import create_app
    from cuga.backend.events.subscriptions import SubscriptionStore

    store = SubscriptionStore(":memory:")
    agents = AgentStore(":memory:")
    rt = make_runtime("http", agent_store=agents)
    mounted = FastAPI()
    register_events_routes(mounted, runtime=rt, store=store,
                           concierge=Concierge(rt, store=store), gateway_token="")
    mounted_paths = {r.path for r in mounted.routes}
    service_paths = {r.path for r in create_app().routes}
    assert mounted_paths - service_paths == set(), "the standalone service is missing routes"
