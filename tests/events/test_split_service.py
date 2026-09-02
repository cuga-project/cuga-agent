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
        self.calls.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": json.loads(request.content or b"{}"),
            }
        )
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
    assert call["headers"]["x-gateway-token"] == "t0k"  # the hop is authenticated
    assert call["body"]["query"] == "Report the IBM price"
    assert call["body"]["thread_id"] == "sub_1"  # KB/session ride on the thread


@pytest.mark.asyncio
async def test_run_retries_a_5xx_then_succeeds(patch_httpx):
    fake = patch_httpx(
        _FakeCuga((503, {"error": "starting"}), (200, {"status": "ok", "answer": "second try"}))
    )
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
    return HttpRuntime(agent_store=AgentStore(":memory:"), base_url="http://cuga.test", token="t0k", **kw)


def test_get_agent_resolves_a_sub_agent_from_cugas_roster(patch_httpx_sync):
    """THE SPLIT WEBHOOK BUG: ?agent=incident_triage 404'd because only this process's (empty)
    store was consulted. The roster belongs to whoever executes — ask CUGA."""
    patch_httpx_sync(
        _FakeCugaRoster(
            [
                {"name": "cuga", "description": "the supervisor"},
                {"name": "incident_triage", "description": "triage"},
            ]
        )
    )
    rt = _rt_empty_store()
    assert rt.get_agent("incident_triage", scope="default").name == "incident_triage"
    assert rt.get_agent("cuga", scope="default") is not None  # always addressable
    assert rt.get_agent("not_a_real_agent", scope="default") is None


def test_list_agents_reports_cugas_roster_not_a_guess(patch_httpx_sync):
    patch_httpx_sync(_FakeCugaRoster([{"name": "cuga"}, {"name": "pricebot"}, {"name": "geobot"}]))
    assert [a.name for a in _rt_empty_store().list_agents(scope="default")] == ["cuga", "pricebot", "geobot"]


def test_roster_falls_back_to_api_agents_then_to_the_supervisor(patch_httpx_sync):
    """An older CUGA has no /run/agents; and if nothing answers, 'cuga' still exists."""
    fake = patch_httpx_sync(_FakeCugaRoster([{"name": "legacy"}], path="/api/agents"))
    assert [a.name for a in _rt_empty_store().list_agents(scope="default")] == ["legacy"]
    assert fake.paths == ["/run/agents", "/api/agents"]  # tried the machine seam first

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
    for path in (
        "/api/events/status",
        "/api/events/subscriptions",
        "/api/events/channels",
        "/api/events/integrations",
        "/api/events/runs",
        "/api/events/agents",
    ):
        assert c.get(path).status_code == 200, path
    # and the HITL arming dialogue behaves identically to the mounted deployment
    out = c.post(
        "/api/concierge",
        json={"text": "/automate every 5 minutes send IBM stock price", "thread_id": "web:local"},
    ).json()
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
    register_events_routes(
        mounted, runtime=rt, store=store, concierge=Concierge(rt, store=store), gateway_token=""
    )
    mounted_paths = {r.path for r in mounted.routes}
    service_paths = {r.path for r in create_app().routes}
    assert mounted_paths - service_paths == set(), "the standalone service is missing routes"


def test_service_defaults_keep_studio_and_channels_in_one_scope(monkeypatch):
    """THE "armed but invisible" TRAP. These defaults used to be set by `cuga start … --events`;
    deleting that flag with combined mode dropped them, and the symptom was brutal to spot: a flow
    armed from Slack resolved to user `local` (unlinked sender → env fallback), while the Studio and
    every harness browse as `admin`. It armed, fired and delivered — and never appeared in Flows.
    """
    from cuga.backend.events import service

    for k in ("EVENTS_USER_ID", "EVENTS_WORKER_BACKEND", "EVENTS_SEED_AGENTS"):
        monkeypatch.delenv(k, raising=False)
    service._apply_defaults()
    assert os.environ["EVENTS_USER_ID"] == "admin"
    assert os.environ["EVENTS_WORKER_BACKEND"] == "http"


def test_service_defaults_never_override_real_config(monkeypatch):
    """setdefault, not assignment — a deployment's own env and .env must win."""
    from cuga.backend.events import service

    monkeypatch.setenv("EVENTS_USER_ID", "bofa-ops")
    service._apply_defaults()
    assert os.environ["EVENTS_USER_ID"] == "bofa-ops"


# ── CUGA IS THE DOOR: channel adapters call /run, not the eventing layer ──────────────────────
def test_channel_adapter_targets_cugas_run(monkeypatch):
    """Every channel utterance goes to CUGA. The adapters hold the bot tokens (they own the
    sockets) but make NO routing decision — /run decides chat-vs-arming."""
    import asyncio
    from cuga.backend.events import cuga_door

    seen = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "status": "ok", "answer": "42"}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            seen.update(url=url, headers=headers or {}, body=json or {})
            return _Resp()

    monkeypatch.setenv("CUGA_URL", "http://cuga.test")
    monkeypatch.setenv("GATEWAY_TOKEN", "tok")
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    out = asyncio.run(cuga_door.ask("hi", channel="slack", native_id="C1", user="U9", locus="17.5"))
    assert out == "42"
    assert seen["url"] == "http://cuga.test/run"  # CUGA, not the eventing service
    assert seen["headers"]["X-Gateway-Token"] == "tok"  # the hop is authenticated
    # thread_id carries the delivery address AND the per-topic memory locus
    assert seen["body"]["thread_id"] == "gw:slack:C1#17.5"
    assert seen["body"]["channel"] == {
        "name": "slack",
        "native_id": "C1",
        "user": "U9",
        "thread_id": "gw:slack:C1#17.5",
    }


def test_channel_adapter_survives_cuga_being_down(monkeypatch):
    """A down CUGA must not kill the channel loop — one bad turn, not a dead bot."""
    import asyncio
    from cuga.backend.events import cuga_door

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            raise ConnectionError("refused")

    monkeypatch.setenv("CUGA_URL", "http://cuga.test")
    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    assert asyncio.run(cuga_door.ask("hi", channel="telegram", native_id="42")) == ""


def test_cuga_door_prefers_CUGA_URL_over_the_invoke_port(monkeypatch):
    """EVENTS_CUGA_PORT means 'where /invoke lives' = the eventing service itself. Reading it here
    would point the door back at ourselves and loop."""
    from cuga.backend.events import cuga_door

    monkeypatch.setenv("EVENTS_CUGA_PORT", "8100")
    monkeypatch.delenv("CUGA_URL", raising=False)
    assert "8100" not in cuga_door.cuga_url()
    monkeypatch.setenv("CUGA_URL", "https://cuga-core.example/")
    assert cuga_door.cuga_url() == "https://cuga-core.example"


def test_slash_matcher_tolerates_a_leading_mention():
    """Slack/Discord normally strip "<@bot>" before we see the text — but that depends on a bot-id
    lookup succeeding. If it ever doesn't, "<@U123> /automate …" must STILL be recognised as arming;
    handing it to the plain agent is the silent-failure trap (it tries to implement the schedule)."""
    from cuga.backend.server.events_bridge import slash_verb

    for armed in ("/automate x", "  /schedule y", "<@U123> /automate x", "<@U1> <@U2> /poll z"):
        assert slash_verb(armed), armed
    for chat in ("what is /automate?", "hello", "tell me about /cron jobs"):
        assert not slash_verb(chat), chat
    # the trailing word boundary the old `\b` gave us: a longer word is NOT the verb
    for chat in ("/automated x", "/automate1 x", "/nope x"):
        assert not slash_verb(chat), chat
    assert slash_verb("/automate?") == "automate"  # …but punctuation still ends it


# ── CUGA MUST STAND ALONE: eventing absent, off, or down ──────────────────────────────────────
def test_cuga_is_standalone_when_no_eventing_service_is_configured(monkeypatch):
    """CUGA deployed by itself, with no eventing service anywhere: EVENTS_API_URL unset. The
    forward is DISABLED, so /run and /stream behave exactly as upstream CUGA does — even a slash
    verb is just text for the agent. Vanilla CUGA is a supported configuration, not a broken one."""
    from cuga.backend.server import events_bridge as eb

    monkeypatch.delenv("EVENTS_API_URL", raising=False)
    for q in ("/automate every 5 mins check ibm", "hello", "yes"):
        assert eb.forwards_to_events(q, "t1") is False, q


def test_forward_is_enabled_only_by_events_api_url(monkeypatch):
    from cuga.backend.server import events_bridge as eb

    monkeypatch.setenv("EVENTS_API_URL", "http://events.test")
    assert eb.forwards_to_events("/automate x", "t1") is True
    assert eb.forwards_to_events("what is the weather?", "t1") is False  # chat never forwards


@pytest.mark.asyncio
async def test_cuga_degrades_gracefully_when_the_eventing_service_is_down(monkeypatch):
    """Configured but unreachable — the realistic outage. Chat must be unaffected (it never calls
    out), and an arming attempt must come back as an honest sentence, not a stack trace or a hang."""
    from cuga.backend.server import events_bridge as eb

    class _Down:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            raise ConnectionError("connection refused")

    monkeypatch.setenv("EVENTS_API_URL", "http://events.test")
    monkeypatch.setattr(httpx, "AsyncClient", _Down)
    reply = await eb.forward_slash_to_events("/automate x", "t1", {})
    assert "Nothing was armed" in reply and "events.test" in reply


@pytest.mark.asyncio
async def test_an_open_dialogue_closes_when_the_flow_arms(monkeypatch):
    """The multi-turn gate must not latch. Once the eventing service says `armed`, the thread stops
    being routed there — otherwise ordinary chat in that thread would be hijacked forever."""
    from cuga.backend.server import events_bridge as eb

    states = iter(["confirm", "armed"])

    class _Resp:
        status_code = 200
        content = b"{}"

        @staticmethod
        def json():
            return {"ok": True, "reply": "ok", "state": next(states)}

    class _C:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return _Resp()

    monkeypatch.setenv("EVENTS_API_URL", "http://events.test")
    monkeypatch.setattr(httpx, "AsyncClient", _C)
    eb._events_open_threads.discard("t-gate")

    await eb.forward_slash_to_events("/automate x", "t-gate", {})  # → confirm
    assert eb.forwards_to_events("yes", "t-gate") is True  # bare follow-up routes
    await eb.forward_slash_to_events("yes", "t-gate", {})  # → armed
    assert eb.forwards_to_events("what is 2+2?", "t-gate") is False  # gate released


def test_flow_reuse_never_crosses_tenants():
    """ISOLATION. find_by_dedup_key used to search every tenant, so arming could answer "REUSING
    existing flow (subscription X)" where X belonged to somebody else — invisible in the caller's
    Flows list, undeletable by them, delivering to the other tenant's channel. Seen live: a flow
    owned by default/default/local was handed to default/default/admin, which had zero flows and
    was told one was reused."""
    from cuga.backend.events.subscriptions import Subscription, SubscriptionStore

    store = SubscriptionStore(":memory:")
    key = "cuga|time|5m|||owner"
    store.upsert(
        Subscription(
            id="cuga-aaaaaa",
            mode="cron",
            target_agent="cuga",
            tenant="default/default/local",
            deliver_to=["slack"],
            thread_id="gw:slack:C1",
            prompt="p",
            dedup_key=key,
        )
    )

    assert store.find_by_dedup_key(key, scope="default/default/local").id == "cuga-aaaaaa"
    assert store.find_by_dedup_key(key, scope="default/default/admin") is None  # the leak
    assert store.find_by_dedup_key(key) is not None  # unscoped callers keep old behaviour


def test_slack_endpoint_answers_a_browser_instead_of_a_bare_405(monkeypatch):
    """A GET on the Slack events URL is a HUMAN checking "is this the right URL?". A bare 405 reads
    as a broken endpoint and sends people debugging the wrong thing — it cost an afternoon once.
    Slack itself only POSTs, so this changes nothing about delivery."""
    monkeypatch.setenv("EVENTS_DB", ":memory:")
    monkeypatch.setenv("GATEWAY_TOKEN", "")
    from cuga.backend.events.service import create_app

    c = TestClient(create_app())
    r = c.get("/api/events/slack/events")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "POST" in body["note"]
    assert "cuga-events-svc" in body["wrong_host_hint"]  # names the actual trap

    # and the real path is untouched: Slack's handshake still echoes the challenge
    r2 = c.post("/api/events/slack/events", json={"type": "url_verification", "challenge": "probe-1"})
    assert r2.status_code == 200 and "probe-1" in r2.text


# ── /run is a MACHINE seam: it must never be the one unauthenticated door ─────────────────────
def _cuga_client():
    """A TestClient over the REAL run router, mounted on a bare app.

    Deliberately NOT `main.app`: whether main mounts these routes is decided at import time by
    run_api_enabled(), and CI has no .env — so on CI the routes would be absent and every
    assertion below would see 404 instead of the 401 it is checking for. Mounting the router
    directly tests the request-time gate, which is the thing under test. The mount-time gate has
    its own test (test_run_api_is_not_mounted_without_configuration).
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cuga.backend.server import run_routes as rr

    async def _fake_stream(*a, **kw):  # pragma: no cover — auth rejects before this runs
        if False:
            yield None

    app = FastAPI()
    app.include_router(rr.build_run_router(event_stream=_fake_stream, default_user_id="tester"))
    return TestClient(app, raise_server_exceptions=False), rr


def test_run_returns_401_without_the_token(monkeypatch):
    """The regression Sami found: the gate read `if token and header != token`, so with NO token
    configured — vanilla CUGA, which sets neither CUGA_RUN_TOKEN nor GATEWAY_TOKEN — the condition
    was false and the check vanished. Anyone who could reach the port could execute the agent, even
    with CUGA auth enabled and /stream gated behind require_chat_access.

    Fails closed now: no token configured and no explicit dev flag → 401, before any agent runs.
    """
    c, rr = _cuga_client()
    for var in ("CUGA_RUN_TOKEN", "GATEWAY_TOKEN", rr.RUN_DEV_UNAUTH_ENV):
        monkeypatch.delenv(var, raising=False)

    r = c.post("/run", json={"query": "hello"})
    assert r.status_code == 401, r.text
    assert "CUGA_RUN_TOKEN" in r.json()["error"]

    assert c.get("/run/agents").status_code == 401  # the roster seam is guarded the same way


def test_run_rejects_a_wrong_token(monkeypatch):
    c, rr = _cuga_client()
    monkeypatch.delenv(rr.RUN_DEV_UNAUTH_ENV, raising=False)
    monkeypatch.setenv("CUGA_RUN_TOKEN", "s3cret")

    assert c.post("/run", json={"query": "hi"}, headers={"X-Gateway-Token": "wrong"}).status_code == 401
    assert c.get("/run/agents", headers={"X-Gateway-Token": "wrong"}).status_code == 401


def test_run_accepts_the_right_token(monkeypatch):
    """Past the gate. /run itself may still fail for lack of a live agent — the assertion is only
    that authentication is no longer the thing stopping it."""
    c, rr = _cuga_client()
    monkeypatch.delenv(rr.RUN_DEV_UNAUTH_ENV, raising=False)
    monkeypatch.setenv("CUGA_RUN_TOKEN", "s3cret")

    assert c.get("/run/agents", headers={"X-Gateway-Token": "s3cret"}).status_code == 200


def test_a_non_ascii_token_header_is_a_401_not_a_500(monkeypatch):
    """hmac.compare_digest raises TypeError on non-ASCII `str`, and this header is attacker
    supplied — comparing bytes keeps a hostile header a 401 rather than a stack trace."""
    c, rr = _cuga_client()
    monkeypatch.delenv(rr.RUN_DEV_UNAUTH_ENV, raising=False)
    monkeypatch.setenv("CUGA_RUN_TOKEN", "s3cret")

    # Headers travel as latin-1, so a str with a non-ASCII char cannot even be sent by the client.
    # The reachable hostile input is a latin-1-encodable byte, which starlette decodes back to a
    # non-ASCII str — exactly what compare_digest refuses to take.
    r = c.get("/run/agents", headers={"X-Gateway-Token": "sécret".encode("latin-1")})
    assert r.status_code == 401


def test_the_dev_flag_is_the_only_way_to_run_unauthenticated(monkeypatch):
    """An explicit, verbose opt-out — so a deployment cannot end up open by forgetting to set
    something. It is logged at boot (warn_if_run_is_unauthenticated)."""
    c, rr = _cuga_client()
    for var in ("CUGA_RUN_TOKEN", "GATEWAY_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(rr.RUN_DEV_UNAUTH_ENV, "1")

    assert c.get("/run/agents").status_code == 200


def test_run_api_is_not_mounted_without_configuration(monkeypatch):
    """Vanilla CUGA has no /run at all — a 404, not a 401.

    /run executes an agent and requires a shared secret, so with nothing configured every call
    would 401; mounting it then serves only to advertise an endpoint nobody can use. Mirrors A2A's
    `if settings.a2a.enabled`. Anything that means to use the seam sets one of these.
    """
    from cuga.backend.server import run_routes as rr

    for var in ("CUGA_RUN_TOKEN", "GATEWAY_TOKEN", "CUGA_SUPERVISOR_ROSTER", rr.RUN_DEV_UNAUTH_ENV):
        monkeypatch.delenv(var, raising=False)
    assert rr.run_api_enabled() is False

    for var, val in (
        ("CUGA_RUN_TOKEN", "s3cret"),
        ("GATEWAY_TOKEN", "s3cret"),
        ("CUGA_SUPERVISOR_ROSTER", "/tmp/roster.yaml"),
        (rr.RUN_DEV_UNAUTH_ENV, "1"),
    ):
        monkeypatch.setenv(var, val)
        assert rr.run_api_enabled() is True, var
        monkeypatch.delenv(var, raising=False)


# ── /run accepts a LOGGED-IN USER too, not only the shared secret ────────────────────────────────
# Review (#602): "JWT and auth middleware is not wired for the run endpoint — can we do the same
# that is in other endpoints?" It could not simply adopt `Depends(require_chat_access)`, because
# /run has two kinds of caller: the eventing service, which holds a shared secret and has no login
# session, and a person or the UI, which has a JWT and should not be handed a machine credential.
# So both are accepted, and the JWT half goes through the SAME dependency /stream uses.


def _auth_patch(monkeypatch, rr, *, user=None, raises=None):
    """Stand in for the auth backend `_jwt_denial` imports at call time.

    Only `require_chat_access` is stubbed, because that is all `_jwt_denial` calls — including for
    "is authentication on?", which the dependency answers by returning None."""
    import sys
    import types

    mod = types.ModuleType("cuga.backend.server.auth.dependencies")

    async def require_chat_access(request):
        if raises is not None:
            raise raises
        return user

    mod.require_chat_access = require_chat_access
    monkeypatch.setitem(sys.modules, "cuga.backend.server.auth.dependencies", mod)


def test_run_accepts_an_authenticated_user_without_the_shared_secret(monkeypatch):
    """The point of the change: a logged-in caller reaches /run on the same terms as /stream."""
    c, rr = _cuga_client()
    monkeypatch.delenv(rr.RUN_DEV_UNAUTH_ENV, raising=False)
    monkeypatch.setenv("CUGA_RUN_TOKEN", "s3cret")
    _auth_patch(monkeypatch, rr, user=object())

    assert c.get("/run/agents").status_code != 401


def test_an_authenticated_user_lacking_the_role_gets_403_not_401(monkeypatch):
    """`require_chat_access` raises 403 for a user without a chat role. Reporting that as "missing
    token" would send someone hunting for a credential they were never supposed to need."""
    from fastapi import HTTPException

    c, rr = _cuga_client()
    monkeypatch.delenv(rr.RUN_DEV_UNAUTH_ENV, raising=False)
    monkeypatch.setenv("CUGA_RUN_TOKEN", "s3cret")
    _auth_patch(monkeypatch, rr, raises=HTTPException(status_code=403, detail="Access denied"))

    r = c.get("/run/agents")
    assert r.status_code == 403, r.text


def test_auth_disabled_still_requires_the_token(monkeypatch):
    """THE REGRESSION GUARD. With authentication off, `require_chat_access` returns None — it means
    "nobody is logged in and that is fine here", NOT "anyone may execute an agent". Treating that as
    success would reopen the hole the fail-closed gate was written to close.

    Stubbing the dependency to return None is exactly what the real one does with auth disabled, so
    this exercises the mechanism rather than a flag."""
    c, rr = _cuga_client()
    monkeypatch.delenv(rr.RUN_DEV_UNAUTH_ENV, raising=False)
    monkeypatch.setenv("CUGA_RUN_TOKEN", "s3cret")
    _auth_patch(monkeypatch, rr, user=None)

    assert c.get("/run/agents").status_code == 401
    # ...and the machine caller still gets in with the secret
    assert c.get("/run/agents", headers={"X-Gateway-Token": "s3cret"}).status_code != 401


def test_a_broken_auth_backend_is_a_denial_not_an_admission(monkeypatch):
    """An exception from the auth stack must not fall through to "authorised"."""
    c, rr = _cuga_client()
    monkeypatch.delenv(rr.RUN_DEV_UNAUTH_ENV, raising=False)
    monkeypatch.setenv("CUGA_RUN_TOKEN", "s3cret")
    _auth_patch(monkeypatch, rr, raises=RuntimeError("auth backend is down"))

    assert c.get("/run/agents").status_code == 401
