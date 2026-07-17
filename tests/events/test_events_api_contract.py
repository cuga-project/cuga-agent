"""HTTP contract tests for the events API — right payload in, right response out.

`test_events_studio_api.py` covers the four GET endpoints the Studio renders. This file covers the
rest of the surface: the `/invoke` gateway, the concierge, the subscription lifecycle
(pause/resume/delete/flow), the run log, the connect/identity/admin endpoints.

Offline by design. Activepieces is a `_FakeEngine` whose async methods return canned AP payloads, so
these run in the fast green gate with no stack and no creds. What they pin down is the *contract*:
status codes, response keys, and the isolation rules — a caller must never see, pause, or delete
another principal's subscription, and an endpoint must degrade to a clear 4xx/501 rather than a 500
when AP or a store is absent.

Two things worth stating, because both have already caused real bugs:

- **`{"ok": true}` is not proof.** `GET /subscriptions/{id}/flow` answers `ok: true` with
  `ap_flow: null` for a subscription pointing at a flow that no longer exists in AP. The dangling
  case is tested explicitly.
- **A missing store means 501, not 500.** Every endpoint backed by an optional dependency
  (`engine`, `identity`, `users`) is tested with that dependency set to `None`.

    .venv/bin/python -m pytest tests/events/test_events_api_contract.py -q
"""

import importlib.util
import os
import sys
import tempfile

_EV = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                   "..", "..", "src", "cuga", "backend", "events"))
if "events" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("events", os.path.join(_EV, "__init__.py"),
                                                   submodule_search_locations=[_EV])
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["events"] = _pkg
    _spec.loader.exec_module(_pkg)

import pytest                                     # noqa: E402
from fastapi import FastAPI                       # noqa: E402
from fastapi.testclient import TestClient         # noqa: E402
from events.app import register_events_routes     # noqa: E402
from events.subscriptions import Subscription, SubscriptionStore  # noqa: E402
from events.runtime import DEFAULT_SCOPE          # noqa: E402

MINE = DEFAULT_SCOPE                # whatever an unauthenticated local caller resolves to
THEIRS = "other/other/bob"          # a different principal — must be invisible to MINE
TENANT, _, ME = MINE.split("/")     # ("default", "default", "local") — don't hardcode the user


# ── doubles ───────────────────────────────────────────────────────────────────
class _Runtime:
    def get_agent(self, agent, scope=""):
        return object()

    async def run(self, agent, thread_id, worker_input, scope="", deliver_to=None):
        return "42"


class _FakeEngine:
    """The AP calls the API makes, and nothing else. `calls` records them so a test can assert that
    pausing a subscription actually disabled the flow in AP, not just flipped a row in sqlite."""

    def __init__(self, flow=None, runs=None, run=None, connections=None):
        self.flow, self.runs, self.run = flow, runs or [], run
        self.connections = connections or []
        self.calls = []

    async def set_flow_status(self, flow_id, enabled):
        self.calls.append(("set_flow_status", flow_id, enabled))

    async def delete_flow(self, flow_id):
        self.calls.append(("delete_flow", flow_id))

    async def get_flow(self, flow_id):
        self.calls.append(("get_flow", flow_id))
        return self.flow

    async def list_runs(self, limit=80):
        return self.runs

    async def get_run(self, run_id):
        return self.run

    async def list_connections(self, project_name=None):
        return self.connections

    async def ensure_secret_connection(self, ext, piece, token, project_name=None):
        self.calls.append(("ensure_secret_connection", ext, piece, token))


def _sub(sub_id="s1", tenant=MINE, **kw):
    d = dict(id=sub_id, mode="CRON", target_agent="papers", tenant=tenant,
             deliver_to=["telegram"], prompt="arxiv MoE", status="active",
             ap_flow_id="flow-1", flow_name="cron-papers", source_connector="cron")
    d.update(kw)
    return Subscription(**d)


def _client(subs=(), *, engine=None, concierge=None, identity=None, users=None,
            runtime=None, gateway_token=None, store=True):
    """A TestClient over a real (file-backed) store so reads work across TestClient's threadpool."""
    st = None
    if store:
        db = os.path.join(tempfile.mkdtemp(), "subs.db")
        w = SubscriptionStore(db)
        for s in subs:
            w.upsert(s)
        st = SubscriptionStore(db)                # fresh handle: cross-thread read
    app = FastAPI()
    register_events_routes(app, runtime=runtime or _Runtime(), store=st, concierge=concierge,
                           engine=engine, identity=identity, users=users,
                           gateway_token=gateway_token)
    return TestClient(app), st


@pytest.fixture(autouse=True)
def _no_footer():
    """Answers carry a ' — agent · via mcp' footer unless disabled. Tests assert on the answer."""
    os.environ["EVENTS_REPLY_METADATA"] = "0"
    yield
    os.environ.pop("EVENTS_REPLY_METADATA", None)


# ── POST /invoke — the gateway every armed AP flow calls back into ────────────
def test_invoke_rejects_missing_gateway_token():
    """/invoke runs an agent on a caller-supplied scope. With a token configured, an unsigned call
    is 401 — before the envelope is even parsed."""
    c, _ = _client(gateway_token="s3cret")
    r = c.post("/invoke", json={"agent": "pricebot", "text": "hi",
                               "source": {"type": "time", "name": "cron"},
                               "event": {"kind": "runonce"}})
    assert r.status_code == 401
    assert "X-Gateway-Token" in r.json()["error"]


def test_invoke_accepts_correct_gateway_token():
    c, _ = _client(gateway_token="s3cret")
    r = c.post("/invoke", headers={"X-Gateway-Token": "s3cret"},
               json={"agent": "pricebot", "text": "price of btc",
                     "source": {"type": "time", "name": "cron"},
                     "event": {"kind": "runonce"}})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_invoke_expiry_gate_ends_a_bounded_flow():
    """A "… for one hour" flow bakes expires_at + subscription_id into its /invoke body; AP
    schedules can't end themselves, so the FIRST tick past the deadline must delete the
    subscription and skip the agent — and a tick BEFORE the deadline must run normally."""
    import time
    sub = Subscription(id="pricebot-ttl001", mode="CRON", target_agent="pricebot",
                       ap_flow_id=None, thread_id="t", prompt="p")
    c, st = _client([sub], gateway_token="")
    tick = {"agent": "pricebot", "text": "price of btc",
            "source": {"type": "time", "name": "cron"},
            "event": {"kind": "runonce", "payload": {}},
            "subscription_id": "pricebot-ttl001"}
    # before the deadline → normal run
    r = c.post("/invoke", json={**tick, "expires_at": time.time() + 3600})
    assert r.status_code == 200 and r.json()["answer"] == "42"
    assert st.get("pricebot-ttl001") is not None
    # past the deadline → no agent run, subscription gone
    r = c.post("/invoke", json={**tick, "expires_at": time.time() - 5})
    b = r.json()
    assert r.status_code == 200 and b["ok"] is True and b.get("expired") is True
    assert "42" not in str(b.get("answer"))
    assert st.get("pricebot-ttl001") is None


def test_invoke_direct_agent_call_shape():
    """The channel-less direct call: source.type=time + event.kind=runonce. The response must carry
    meta.mcp — that is what the NOW suite asserts on to prove the agent reached a real tool."""
    c, _ = _client(gateway_token="")
    r = c.post("/invoke", json={"agent": "pricebot", "text": "what is bitcoin worth?",
                                "source": {"type": "time", "name": "cron"},
                                "event": {"kind": "runonce", "payload": {}}})
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] is True and b["agent"] == "pricebot" and b["answer"] == "42"
    assert b["trace_id"] and isinstance(b["meta"]["mcp"], list)


@pytest.mark.parametrize("body,why", [
    ({"agent": "x", "text": "hi", "source": {"type": "carrier-pigeon", "name": "n"},
      "event": {"kind": "runonce"}}, "source.type"),
    ({"agent": "x", "text": "hi", "source": {"type": "time", "name": "cron"},
      "event": {"kind": "telepathy"}}, "event.kind"),
    ({"agent": "x", "text": "   ", "source": {"type": "channel", "name": "slack"},
      "event": {"kind": "message"}}, "empty text"),
])
def test_invoke_rejects_invalid_envelope(body, why):
    """A malformed envelope is a 400 naming the problem — not a 500, and never a silent no-op."""
    c, _ = _client(gateway_token="")
    r = c.post("/invoke", json=body)
    assert r.status_code == 400, r.text
    assert why in r.json()["error"], r.json()["error"]


# ── POST /api/concierge ───────────────────────────────────────────────────────
def test_concierge_dry_run_plans_without_side_effects():
    """?dry_run=1 runs the deterministic planner: a plan comes back, no flow is armed, and no
    concierge instance is needed."""
    c, st = _client(concierge=None)
    r = c.post("/api/concierge?dry_run=1",
               json={"text": "every day at 9am send me the price of bitcoin"})
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] is True and b["dry_run"] is True
    assert b["decision"]["mode"] == "CRON"
    assert st.as_dicts(scope=MINE) == []          # nothing armed


def test_concierge_live_without_instance_is_501_with_the_plan():
    """No concierge configured → 501 (not 500), and it still hands back the plan and says how to
    get it: ?dry_run=1."""
    c, _ = _client(concierge=None)
    r = c.post("/api/concierge", json={"text": "send me bitcoin every morning"})
    assert r.status_code == 501
    b = r.json()
    assert b["ok"] is False and "dry_run" in b["reason"] and b["plan"]["decision"]["mode"]


def test_concierge_live_returns_reply():
    class _Concierge:
        async def run(self, thread_id, text, principal):
            return "Cron flow set up."

    c, _ = _client(concierge=_Concierge())
    r = c.post("/api/concierge", json={"text": "every day at 9am send me bitcoin",
                                       "thread_id": "web:local"})
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] is True and b["reply"] == "Cron flow set up." and b["scope"] == MINE


class _ArmingConcierge:
    """A concierge that arms one flow, the way the real one does — via the store."""

    def __init__(self, store, ap_flow_id="flow-1"):
        self.store, self.ap_flow_id = store, ap_flow_id

    async def run(self, thread_id, text, principal):
        self.store.upsert(_sub("armed-1", tenant=principal.scope, ap_flow_id=self.ap_flow_id))
        return "Armed a cron flow."


def test_concierge_flow_param_returns_the_armed_ap_flow():
    """`?flow=1` answers "what did that sentence actually build?" without a second round trip.

    The digest is the question people ask — which pieces, in what order — not AP's raw bookkeeping.
    """
    ap_flow = {"id": "flow-1", "status": "ENABLED", "version": {"trigger": {
        "settings": {"pieceName": "@activepieces/piece-schedule",
                     "triggerName": "cron_expression",
                     "input": {"cronExpression": "0 9 * * *"}},
        "nextAction": {"name": "step_1", "displayName": "Invoke CUGA", "type": "PIECE",
                       "settings": {"pieceName": "@activepieces/piece-http",
                                    "actionName": "send_request"},
                       "nextAction": {"name": "step_2", "displayName": "telegram · send",
                                      "type": "PIECE",
                                      "settings": {"pieceName": "@activepieces/piece-telegram-bot",
                                                   "actionName": "send_text_message",
                                                   "input": {"text": "{{step_1.body.answer}}"}}}}}}}
    st = SubscriptionStore(os.path.join(tempfile.mkdtemp(), "s.db"))
    app = FastAPI()
    register_events_routes(app, runtime=_Runtime(), store=st, concierge=_ArmingConcierge(st),
                           engine=_FakeEngine(flow=ap_flow))
    c = TestClient(app)

    b = c.post("/api/concierge?flow=1", json={"text": "every day at 9am send bitcoin"}).json()
    assert b["ok"] is True and b["reply"] == "Armed a cron flow."
    (f,) = b["flows"]
    assert f["subscription_id"] == "armed-1" and f["mode"] == "CRON" and f["exists_in_ap"] is True
    assert f["ap_flow_id"] == "flow-1" and "dedup_key" in f
    d = f["flow"]
    assert d["status"] == "ENABLED"
    assert d["trigger"]["piece"] == "@activepieces/piece-schedule"
    assert d["trigger"]["input"]["cronExpression"] == "0 9 * * *"
    # the whole chain, in order — and the seam that carries the answer into the sink
    assert [s["name"] for s in d["steps"]] == ["step_1", "step_2"]
    assert d["steps"][0]["piece"] == "@activepieces/piece-http"
    assert d["steps"][1]["text"] == "{{step_1.body.answer}}"


def test_concierge_flow_full_returns_the_raw_ap_flow():
    ap_flow = {"id": "flow-1", "status": "ENABLED", "version": {"trigger": {"settings": {}}}}
    st = SubscriptionStore(os.path.join(tempfile.mkdtemp(), "s.db"))
    app = FastAPI()
    register_events_routes(app, runtime=_Runtime(), store=st, concierge=_ArmingConcierge(st),
                           engine=_FakeEngine(flow=ap_flow))
    b = TestClient(app).post("/api/concierge?flow=full",
                             json={"text": "every day at 9am send bitcoin"}).json()
    assert b["flows"][0]["flow"] == ap_flow          # verbatim, not the digest


def test_concierge_flow_param_reports_a_dangling_flow():
    """The subscription names an ap_flow_id, but AP has no such flow. `exists_in_ap` must say so —
    otherwise a caller infers "armed" from a non-empty ap_flow_id, which is the original bug."""
    st = SubscriptionStore(os.path.join(tempfile.mkdtemp(), "s.db"))
    app = FastAPI()
    register_events_routes(app, runtime=_Runtime(), store=st, concierge=_ArmingConcierge(st),
                           engine=_FakeEngine(flow=None))          # AP has no such flow
    b = TestClient(app).post("/api/concierge?flow=1", json={"text": "arm it"}).json()
    (f,) = b["flows"]
    assert f["ap_flow_id"] == "flow-1"      # it still claims one
    assert f["exists_in_ap"] is False       # …but it does not exist
    assert "flow" not in f


def test_concierge_flow_param_survives_ap_being_down():
    class _Down(_FakeEngine):
        async def get_flow(self, flow_id):
            raise RuntimeError("connection refused")

    st = SubscriptionStore(os.path.join(tempfile.mkdtemp(), "s.db"))
    app = FastAPI()
    register_events_routes(app, runtime=_Runtime(), store=st, concierge=_ArmingConcierge(st),
                           engine=_Down())
    r = TestClient(app).post("/api/concierge?flow=1", json={"text": "arm it"})
    assert r.status_code == 200                     # the arm is not lost because AP is unreachable
    (f,) = r.json()["flows"]
    assert f["exists_in_ap"] is False and "connection refused" in f["flow_error"]


def test_concierge_flow_param_is_empty_when_nothing_was_armed():
    """A plain question arms nothing, so there is nothing to show. An empty list is the honest
    answer — and so is the case where an existing flow was REUSED rather than created."""
    class _Chatty:
        async def run(self, *a, **k):
            return "Bitcoin is about $63,964 USD."

    c, _ = _client(concierge=_Chatty(), engine=_FakeEngine())
    b = c.post("/api/concierge?flow=1", json={"text": "what is bitcoin worth?"}).json()
    assert b["ok"] is True and b["flows"] == []


def test_concierge_without_flow_param_makes_no_ap_call():
    """The default path must not pay an AP round trip. Guards against making `?flow=1` the default."""
    eng = _FakeEngine(flow={"id": "flow-1"})
    st = SubscriptionStore(os.path.join(tempfile.mkdtemp(), "s.db"))
    app = FastAPI()
    register_events_routes(app, runtime=_Runtime(), store=st, concierge=_ArmingConcierge(st),
                           engine=eng)
    b = TestClient(app).post("/api/concierge", json={"text": "arm it"}).json()
    assert "flows" not in b
    assert [c for c in eng.calls if c[0] == "get_flow"] == []


def test_concierge_error_is_500_with_trace_id():
    class _Boom:
        async def run(self, *a, **k):
            raise RuntimeError("AP unreachable")

    c, _ = _client(concierge=_Boom())
    r = c.post("/api/concierge", json={"text": "arm something"})
    assert r.status_code == 500
    assert r.json()["error"] == "AP unreachable" and r.json()["trace_id"]


# ── subscription lifecycle ────────────────────────────────────────────────────
def test_pause_disables_the_ap_flow_not_just_the_row():
    eng = _FakeEngine()
    c, st = _client([_sub()], engine=eng)
    r = c.post("/api/events/subscriptions/s1/pause")
    assert r.status_code == 200 and r.json() == {"ok": True, "id": "s1", "status": "paused"}
    assert st.get("s1").status == "paused"
    assert ("set_flow_status", "flow-1", False) in eng.calls


def test_resume_reenables_the_ap_flow():
    eng = _FakeEngine()
    c, st = _client([_sub(status="paused")], engine=eng)
    r = c.post("/api/events/subscriptions/s1/resume")
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert st.get("s1").status == "active"
    assert ("set_flow_status", "flow-1", True) in eng.calls


def test_delete_removes_the_ap_flow_and_the_row():
    eng = _FakeEngine()
    c, st = _client([_sub()], engine=eng)
    r = c.delete("/api/events/subscriptions/s1")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert st.get("s1") is None
    assert ("delete_flow", "flow-1") in eng.calls


@pytest.mark.parametrize("method,path", [
    ("post", "/api/events/subscriptions/nope/pause"),
    ("post", "/api/events/subscriptions/nope/resume"),
    ("delete", "/api/events/subscriptions/nope"),
    ("get", "/api/events/subscriptions/nope/flow"),
])
def test_unknown_subscription_is_404(method, path):
    c, _ = _client([_sub()])
    assert getattr(c, method)(path).status_code == 404


@pytest.mark.parametrize("method,path", [
    ("post", "/api/events/subscriptions/s1/pause"),
    ("post", "/api/events/subscriptions/s1/resume"),
    ("delete", "/api/events/subscriptions/s1"),
    ("get", "/api/events/subscriptions/s1/flow"),
])
def test_another_principals_subscription_is_404_not_403(method, path):
    """Isolation. 404 rather than 403 — a 403 would confirm the id exists, which leaks across
    tenants. The row must survive an attempted delete."""
    eng = _FakeEngine()
    c, st = _client([_sub(tenant=THEIRS)], engine=eng)
    assert getattr(c, method)(path).status_code == 404
    assert st.get("s1") is not None                # not deleted
    assert eng.calls == []                         # AP never touched


def test_subscription_flow_returns_the_live_ap_flow():
    eng = _FakeEngine(flow={"id": "flow-1", "version": {"trigger": {"name": "trigger"}}})
    c, _ = _client([_sub()], engine=eng)
    b = c.get("/api/events/subscriptions/s1/flow").json()
    assert b["ok"] is True
    assert b["subscription"]["target_agent"] == "papers"    # the CUGA model, as a dict
    assert b["ap_flow"]["id"] == "flow-1"                   # the live AP flow


def test_subscription_flow_reports_a_dangling_flow_as_ap_flow_null():
    """The trap: `ok` is true either way. A subscription can point at an ap_flow_id that no longer
    exists in AP — the watcher can never fire, yet the concierge reports it armed. `ap_flow: null`
    is the ONLY signal, which is why flow_alive() in the live harnesses checks that key and not the
    truthiness of the response."""
    c, _ = _client([_sub()], engine=_FakeEngine(flow=None))
    b = c.get("/api/events/subscriptions/s1/flow").json()
    assert b["ok"] is True and b["ap_flow"] is None
    assert b["subscription"]["ap_flow_id"] == "flow-1"      # it still *claims* a flow


def test_subscription_flow_without_ap_configured():
    c, _ = _client([_sub()], engine=None)
    b = c.get("/api/events/subscriptions/s1/flow").json()
    assert b["ok"] is True and b["ap_flow"] is None


# ── POST /subscriptions/{id}/run — the debug trigger ──────────────────────────
class _RunEngine(_FakeEngine):
    """An AP that produces a new, finished run the moment the flow is triggered."""

    _MISSING = object()          # so flow=None can mean "AP has no such flow" (the dangling case)

    def __init__(self, *, flow=_MISSING, fail_trigger=False, finish=True, answer="42"):
        super().__init__(flow=({"id": "flow-1"} if flow is _RunEngine._MISSING else flow))
        self.fail_trigger, self.finish, self.answer = fail_trigger, finish, answer
        self._runs: list[dict] = []

    async def trigger_flow(self, flow_id, payload=None, headers=None):
        # `headers` mirrors the real engine: /run reproduces the provider's delivery headers
        # (github's X-GitHub-Event) so the piece can identify the event.
        self.calls.append(("trigger_flow", flow_id, payload, headers))
        if self.fail_trigger:
            return False, "HTTP 404 flow not found"
        self._runs.append({"id": "new-run", "flowId": flow_id,
                           "status": "SUCCEEDED" if self.finish else "RUNNING"})
        return True, "HTTP 200"

    async def list_runs(self, limit=60):
        return self._runs

    async def get_run(self, run_id):
        return {"id": run_id, "flowId": "flow-1", "status": "SUCCEEDED",
                "startTime": "2026-07-09T09:00:00Z", "finishTime": "2026-07-09T09:00:05Z",
                "steps": {"trigger": {"output": {"tick": True}},
                          "step_1": {"status": "SUCCEEDED",
                                     "output": {"body": {"answer": self.answer}}}}}


def test_debug_run_fires_the_flow_and_returns_the_answer():
    eng = _RunEngine(answer="Bitcoin is $63,924 USD.")
    c, _ = _client([_sub()], engine=eng)
    b = c.post("/api/events/subscriptions/s1/run?timeout=5").json()
    assert b["ok"] is True and b["triggered"] is True and b["debug"] is True
    assert b["answer"] == "Bitcoin is $63,924 USD."
    assert b["run"]["status"] == "SUCCEEDED" and b["error"] is None
    # headers=None for a non-github trigger; a github sub sends X-GitHub-Event (see
    # test_debug_run_sends_the_github_delivery_header)
    assert ("trigger_flow", "flow-1", {}, None) in eng.calls
    # the caller must be told this was not a dry run
    assert "real" in b["warning"]


def test_debug_run_sends_the_github_delivery_header():
    """A GitHub repo webhook carries EVERY subscribed event type, so the piece disambiguates on the
    X-GitHub-Event header. /run must reproduce it from the subscription's registry row — without it
    the piece emits nothing at all (AP accepts the trigger, no run is ever created). Proven live on
    new_release / new_commit before this was wired."""
    eng = _RunEngine(answer="ok")
    sub = _sub(mode="PUSH", source_connector="github", event="new_release")
    c, _ = _client([sub], engine=eng)
    c.post("/api/events/subscriptions/s1/run?timeout=5", json={"action": "created"})
    call = next(x for x in eng.calls if x[0] == "trigger_flow")
    assert call[3] == {"X-GitHub-Event": "release"}, call
    # new_commit is delivered under GitHub's `push` event, not a `commit` event
    eng2 = _RunEngine(answer="ok")
    c2, _ = _client([_sub(source_connector="github", event="new_commit")], engine=eng2)
    c2.post("/api/events/subscriptions/s1/run?timeout=5", json={})
    assert next(x for x in eng2.calls if x[0] == "trigger_flow")[3] == {"X-GitHub-Event": "push"}
    # a non-github trigger sends no delivery header
    eng3 = _RunEngine(answer="ok")
    c3, _ = _client([_sub(source_connector="cron")], engine=eng3)
    c3.post("/api/events/subscriptions/s1/run?timeout=5", json={})
    assert next(x for x in eng3.calls if x[0] == "trigger_flow")[3] is None


def test_debug_run_wait_0_returns_immediately_without_an_answer():
    eng = _RunEngine()
    c, _ = _client([_sub()], engine=eng)
    b = c.post("/api/events/subscriptions/s1/run?wait=0").json()
    assert b["triggered"] is True and "answer" not in b
    assert [x for x in eng.calls if x[0] == "trigger_flow"]


def test_debug_run_reports_a_timeout_rather_than_a_failure():
    """AP accepted the trigger but nothing finished. That is not the same as 'it failed'."""
    eng = _RunEngine(finish=False)
    c, _ = _client([_sub()], engine=eng)
    b = c.post("/api/events/subscriptions/s1/run?timeout=1").json()
    assert b["ok"] is True and b["triggered"] is True and b["timed_out"] is True
    assert "no run finished within 1s" in b["note"]
    assert b["run"]["status"] == "RUNNING"          # we saw it start


def test_debug_run_refuses_a_dangling_subscription():
    """A subscription naming a flow AP does not have would 'trigger' happily and run nothing."""
    eng = _RunEngine(flow=None)
    c, _ = _client([_sub()], engine=eng)
    r = c.post("/api/events/subscriptions/s1/run")
    assert r.status_code == 409 and "DANGLING" in r.json()["error"]
    assert [x for x in eng.calls if x[0] == "trigger_flow"] == []   # never fired


def test_debug_run_surfaces_an_ap_refusal_as_502():
    c, _ = _client([_sub()], engine=_RunEngine(fail_trigger=True))
    r = c.post("/api/events/subscriptions/s1/run")
    assert r.status_code == 502 and "Activepieces refused" in r.json()["error"]


def test_debug_run_requires_the_gateway_token():
    """It runs an agent with the caller's credentials and posts to a real channel. Its gate must not
    be weaker than /invoke's."""
    c, _ = _client([_sub()], engine=_RunEngine(), gateway_token="s3cret")
    assert c.post("/api/events/subscriptions/s1/run").status_code == 401
    r = c.post("/api/events/subscriptions/s1/run?timeout=5",
               headers={"X-Gateway-Token": "s3cret"})
    assert r.status_code == 200 and r.json()["triggered"] is True


def test_debug_run_can_be_disabled_by_env():
    os.environ["EVENTS_DEBUG_RUN"] = "0"
    try:
        c, _ = _client([_sub()], engine=_RunEngine())
        r = c.post("/api/events/subscriptions/s1/run")
        assert r.status_code == 403 and "EVENTS_DEBUG_RUN=0" in r.json()["error"]
    finally:
        os.environ.pop("EVENTS_DEBUG_RUN", None)


def test_debug_run_another_principals_subscription_is_404():
    eng = _RunEngine()
    c, _ = _client([_sub(tenant=THEIRS)], engine=eng)
    assert c.post("/api/events/subscriptions/s1/run").status_code == 404
    assert eng.calls == []


def test_debug_run_without_ap_is_501():
    c, _ = _client([_sub()], engine=None)
    assert c.post("/api/events/subscriptions/s1/run").status_code == 501


def test_debug_run_on_a_subscription_with_no_flow_is_409():
    c, _ = _client([_sub(ap_flow_id=None)], engine=_RunEngine())
    r = c.post("/api/events/subscriptions/s1/run")
    assert r.status_code == 409 and "no AP flow" in r.json()["error"]


def test_flows_console_serves_html():
    c, _ = _client()
    r = c.get("/api/events/flows/console")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")


# ── run log ───────────────────────────────────────────────────────────────────
def test_runs_join_ap_runs_to_their_subscription():
    eng = _FakeEngine(runs=[{"id": "r1", "flowId": "flow-1", "status": "SUCCEEDED",
                             "startTime": "2026-07-09T00:00:00Z", "finishTime": None}])
    c, _ = _client([_sub()], engine=eng)
    runs = c.get("/api/events/runs").json()["runs"]
    assert len(runs) == 1
    r = runs[0]
    assert (r["id"], r["status"], r["agent"], r["mode"]) == ("r1", "SUCCEEDED", "papers", "CRON")
    assert r["channel"] == "telegram" and r["subscription_id"] == "s1"


def test_runs_hides_runs_for_flows_the_caller_does_not_own():
    eng = _FakeEngine(runs=[{"id": "r1", "flowId": "flow-1", "status": "SUCCEEDED"},
                            {"id": "r2", "flowId": "someone-elses", "status": "SUCCEEDED"}])
    c, _ = _client([_sub()], engine=eng)
    assert [r["id"] for r in c.get("/api/events/runs").json()["runs"]] == ["r1"]


def test_runs_without_ap_is_empty_not_an_error():
    c, _ = _client([_sub()], engine=None)
    r = c.get("/api/events/runs")
    assert r.status_code == 200 and r.json()["runs"] == []


def test_run_detail_extracts_the_agents_answer_and_trigger_payload():
    """The Studio's run view. The answer is buried at steps.<name>.output.body.answer — the shape
    /invoke returns — and the trigger's output is the raw event that fired the flow."""
    eng = _FakeEngine(run={
        "id": "r1", "flowId": "flow-1", "status": "SUCCEEDED",
        "startTime": "2026-07-09T00:00:00Z", "finishTime": "2026-07-09T00:00:04Z",
        "steps": {"trigger": {"output": {"subject": "Q3 numbers", "from": "boss@corp.com"}},
                  "step_1": {"status": "SUCCEEDED",
                             "output": {"body": {"answer": "Your boss sent Q3 numbers."}}}}})
    c, _ = _client([_sub()], engine=eng)
    b = c.get("/api/events/runs/r1").json()
    assert b["ok"] is True and b["run"]["status"] == "SUCCEEDED"
    assert b["answer"] == "Your boss sent Q3 numbers."
    assert b["trigger_payload"]["from"] == "boss@corp.com"
    assert b["error"] is None


def test_run_detail_surfaces_a_failed_steps_error():
    eng = _FakeEngine(run={"id": "r1", "flowId": "flow-1", "status": "FAILED",
                           "steps": {"step_1": {"status": "FAILED",
                                                "errorMessage": "401 Bad credentials"}}})
    c, _ = _client([_sub()], engine=eng)
    b = c.get("/api/events/runs/r1").json()
    assert b["answer"] is None and b["error"] == "401 Bad credentials"


def test_run_detail_for_another_principals_flow_is_404():
    eng = _FakeEngine(run={"id": "r1", "flowId": "not-mine", "status": "SUCCEEDED", "steps": {}})
    c, _ = _client([_sub()], engine=eng)
    assert c.get("/api/events/runs/r1").status_code == 404


def test_run_detail_unknown_run_is_404():
    c, _ = _client([_sub()], engine=_FakeEngine(run=None))
    assert c.get("/api/events/runs/ghost").status_code == 404


# ── connect: pasting a token ──────────────────────────────────────────────────
def test_connect_token_unknown_app_is_404():
    c, _ = _client(engine=_FakeEngine())
    r = c.post("/api/events/connect/myspace/token", json={"token": "t"})
    assert r.status_code == 404 and "unknown app" in r.json()["error"]


def test_connect_token_missing_token_is_400():
    c, _ = _client(engine=_FakeEngine())
    r = c.post("/api/events/connect/telegram/token", json={})
    assert r.status_code == 400 and "missing 'token'" in r.json()["error"]


@pytest.mark.parametrize("app", ["box", "gmail", "github"])
def test_connect_token_refuses_a_pasted_token_for_an_oauth_app(app):
    """AP's OAuth2 connection schema wants the authorization *code* and does the exchange itself, so
    a pre-obtained access token can never work. Say so plainly instead of forwarding it to AP and
    surfacing a cryptic validation error."""
    c, _ = _client(engine=_FakeEngine())
    r = c.post(f"/api/events/connect/{app}/token", json={"token": "dev_token"})
    assert r.status_code == 400
    e = r.json()["error"]
    assert "OAuth connector" in e and f"/api/events/connect/{app}" in e


def test_connect_token_creates_a_secret_connection():
    """telegram, not github: `piece-telegram-bot` really does accept SECRET_TEXT."""
    eng = _FakeEngine()
    c, _ = _client(engine=eng)
    r = c.post("/api/events/connect/telegram/token", json={"token": "8123:AAH"})
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] is True and b["app"] == "telegram" and b["connection"]
    made = [x for x in eng.calls if x[0] == "ensure_secret_connection"]
    assert len(made) == 1 and made[0][3] == "8123:AAH"


def test_connect_token_refuses_a_pasted_pat_for_github():
    """REGRESSION. `@activepieces/piece-github` accepts only OAUTH2/CUSTOM_AUTH. A PAT stored as
    SECRET_TEXT is accepted by AP's store and then unusable — the piece runs with no credential and
    GitHub answers `401 Bad credentials`, indistinguishable from an under-scoped token. GitHub is an
    OAuth app; refuse the paste at the door rather than fail at flow-publish time."""
    c, _ = _client(engine=_FakeEngine())
    r = c.post("/api/events/connect/github/token", json={"token": "ghp_xxx"})
    assert r.status_code == 400
    e = r.json()["error"]
    assert "OAuth connector" in e and "/api/events/connect/github" in e


def test_connect_token_without_ap_is_501():
    c, _ = _client(engine=None)
    r = c.post("/api/events/connect/telegram/token", json={"token": "8123:AAH"})
    assert r.status_code == 501 and "AP not configured" in r.json()["error"]


def test_connect_token_ap_failure_is_500_not_a_crash():
    class _Broken(_FakeEngine):
        async def ensure_secret_connection(self, *a, **k):
            raise RuntimeError("connection_name_already_exists")

    c, _ = _client(engine=_Broken())
    r = c.post("/api/events/connect/telegram/token", json={"token": "8123:AAH"})
    assert r.status_code == 500 and "already_exists" in r.json()["error"]


# ── profile / connections / linking ───────────────────────────────────────────
def test_connections_lists_only_the_callers_own():
    """AP connection externalIds embed the owner: `ea::<tenant>::<user>::<app>`. Another user's
    connection in the same AP project must not appear."""
    eng = _FakeEngine(connections=[{"externalId": f"ea::{TENANT}::{ME}::github"},
                                   {"externalId": f"ea::{TENANT}::bob::github"}])
    c, _ = _client(engine=eng)
    b = c.get("/api/events/connections").json()
    assert [x["externalId"] for x in b["connections"]] == [f"ea::{TENANT}::{ME}::github"]


def test_connections_without_ap_is_empty():
    c, _ = _client(engine=None)
    assert c.get("/api/events/connections").json()["connections"] == []


def test_me_returns_profile_with_roles_and_links():
    from events.identity import IdentityMap
    from events.users import UserStore

    users = UserStore(":memory:")
    users.add(ME, email="a@corp.com", roles=["admin"], tenant=TENANT)
    c, _ = _client(users=users, identity=IdentityMap(":memory:"), engine=None)
    b = c.get("/api/events/me").json()
    assert b["user_id"] == ME and b["email"] == "a@corp.com" and b["roles"] == ["admin"]
    assert b["linked_channels"] == [] and b["connections"] == []


def test_me_degrades_when_ap_is_down():
    """list_connections raising must not 500 the profile page."""
    class _Down(_FakeEngine):
        async def list_connections(self, project_name=None):
            raise RuntimeError("connection refused")

    c, _ = _client(engine=_Down())
    r = c.get("/api/events/me")
    assert r.status_code == 200 and r.json()["connections"] == []


def test_link_channel_issues_a_redeemable_token():
    from events.identity import IdentityMap

    idm = IdentityMap(":memory:")
    c, _ = _client(identity=idm)
    b = c.post("/api/events/link/telegram").json()
    assert b["ok"] is True and b["channel"] == "telegram" and b["token"]
    assert "/start" in b["how"] or "t.me" in b["how"]
    assert idm.redeem_token(b["token"], "tg-12345") is not None      # the token really works


def test_link_channel_without_identity_map_is_501():
    c, _ = _client(identity=None)
    r = c.post("/api/events/link/telegram")
    assert r.status_code == 501 and "identity map" in r.json()["error"]


# ── admin ─────────────────────────────────────────────────────────────────────
def test_admin_users_requires_the_admin_role():
    from events.users import UserStore

    users = UserStore(":memory:")
    users.add(ME, roles=["user"], tenant=TENANT)              # the caller — deliberately NOT admin
    c, _ = _client(users=users)
    assert c.get("/api/events/admin/users").status_code == 403
    assert c.post("/api/events/admin/users", json={"user_id": "eve"}).status_code == 403
    assert users.get("eve", TENANT) is None                   # and eve was not created


def test_admin_can_list_and_add_users():
    from events.users import UserStore

    users = UserStore(":memory:")
    users.add(ME, roles=["admin"], tenant=TENANT)
    c, _ = _client(users=users)
    r = c.post("/api/events/admin/users",
               json={"user_id": "bob", "email": "b@corp.com", "roles": ["user"]})
    assert r.status_code == 200 and r.json()["user"]["user_id"] == "bob"
    ids = {u["user_id"] for u in c.get("/api/events/admin/users").json()["users"]}
    assert {ME, "bob"} <= ids


def test_admin_add_user_without_user_id_is_400():
    from events.users import UserStore

    users = UserStore(":memory:")
    users.add(ME, roles=["admin"], tenant=TENANT)
    c, _ = _client(users=users)
    r = c.post("/api/events/admin/users", json={"email": "nobody@corp.com"})
    assert r.status_code == 400 and "missing user_id" in r.json()["error"]


def test_admin_add_user_without_a_user_store_is_501():
    c, _ = _client(users=None)
    assert c.post("/api/events/admin/users", json={"user_id": "bob"}).status_code == 501


# ── the spec must stay golden ─────────────────────────────────────────────────
def test_api_spec_is_golden():
    """`events_docs/api/api_spec.html` is generated from the endpoint table in `scripts/gen_api_spec.py`,
    which is itself checked against the routes registered in `events/app.py`.

    This fails in two situations, both of which mean the spec is lying:
      1. a route exists in the code with no entry in the table (or vice versa);
      2. the table changed but the checked-in HTML wasn't regenerated.

    Fix: `python scripts/gen_api_spec.py`. Describe the new route while you're there — an endpoint
    with no caller, no payload and no failure modes is not documented, it is merely listed.
    """
    import subprocess

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    p = subprocess.run([sys.executable, os.path.join(root, "scripts/gen_api_spec.py"), "--check"],
                       capture_output=True, text=True, cwd=root)
    assert p.returncode == 0, (p.stdout + p.stderr)


def test_examples_board_matches_the_catalog():
    """`events_docs/api/examples.html` is generated from `events/catalog.py` — the same source that
    powers the Studio Examples tab. Adding an agent + a catalog example but forgetting to regenerate
    the board leaves the public doc showing fewer flows than the product actually does. This is exactly
    the drift that hid 8 agents (the cuga-apps set) from the board.

    Fix: `python scripts/gen_examples.py`.
    """
    import subprocess

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    p = subprocess.run([sys.executable, os.path.join(root, "scripts/gen_examples.py"), "--check"],
                       capture_output=True, text=True, cwd=root)
    assert p.returncode == 0, (p.stdout + p.stderr)


def test_slides_deck_matches_the_registry():
    """`events_docs/slides.html` (the event-driven-agents deck) is generated from triggers.py +
    catalog.py. A trigger or example added without regenerating means the story we present disagrees
    with what the product does. Fix: `python scripts/gen_slides.py` (or `make slides`)."""
    import subprocess

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    p = subprocess.run([sys.executable, os.path.join(root, "scripts/gen_slides.py"), "--check"],
                       capture_output=True, text=True, cwd=root)
    assert p.returncode == 0, (p.stdout + p.stderr)


def test_every_trigger_appears_in_its_setup_guide():
    """Every registry trigger must be named (as `event`) in its app's events_docs/setup/<APP>.md —
    the setup guides are where a tester learns which scope/intent/subscription each trigger needs
    (Slack scopes, Discord privileged intents, the one-credential-covers-all note for gmail/github/
    box). A trigger added without its permissions documented is a support ticket waiting."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "events_docs" / "setup"
    from events import triggers as tr
    missing = []
    for t in tr.rows():
        doc = root / f"{t.app.upper()}.md"
        if not doc.exists() or f"`{t.event}`" not in doc.read_text():
            missing.append(f"{t.app}/{t.event} → setup/{doc.name}")
    assert not missing, "triggers not documented in their setup guide:\n  " + "\n  ".join(missing)


def test_every_route_appears_in_the_api_reference():
    """`events_docs/api/api.html` is what we hand people. A route added without a doc row is a route nobody
    outside this repo can discover. Matching is on the path with `{param}`/`<param>` normalised away,
    so renaming a path parameter doesn't spuriously fail."""
    import html as _html
    import re

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    src = open(os.path.join(root, "src/cuga/backend/events/app.py")).read()
    doc = _html.unescape(open(os.path.join(root, "events_docs/api/api.html")).read())

    def norm(s):
        return re.sub(r"\{[^}/]+\}|<[a-z_]+>", "*", s)

    routes = {norm(p) for _, p in re.findall(r'@app\.(get|post|delete|put)\("([^"]+)"\)', src)}
    undocumented = sorted(r for r in routes if r not in norm(doc))
    assert not undocumented, (
        f"{len(undocumented)} route(s) missing from events_docs/api/api.html: {undocumented}")
