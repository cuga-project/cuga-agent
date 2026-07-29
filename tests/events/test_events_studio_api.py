"""Studio read-endpoint tests (FastAPI TestClient) — the dumb UI's data contract.

Needs a venv with fastapi (``.venv`` or ``.venv-events``), not plain python3:
    .venv-events/bin/python -m pytest tests/events/test_events_studio_api.py
    .venv-events/bin/python tests/events/test_events_studio_api.py

Verifies the four GET endpoints the Studio tabs render + the status gate. Uses a real
SubscriptionStore (file-backed so it's readable across TestClient's threadpool) and engine=None
(→ integrations report ap_not_configured, still 200 — never a 500).
"""

import importlib.util
import os
import sys
import tempfile
import pytest

pytestmark = pytest.mark.unit

_EV = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                   "..", "..", "src", "cuga", "backend", "events"))
_spec = importlib.util.spec_from_file_location("events", os.path.join(_EV, "__init__.py"),
                                               submodule_search_locations=[_EV])
_pkg = importlib.util.module_from_spec(_spec)
sys.modules["events"] = _pkg
_spec.loader.exec_module(_pkg)

from fastapi import FastAPI                       # noqa: E402
from fastapi.testclient import TestClient         # noqa: E402
from events.app import register_events_routes     # noqa: E402
from events.subscriptions import SubscriptionStore, Subscription  # noqa: E402
from events.runtime import DEFAULT_SCOPE          # noqa: E402


def _client():
    db = os.path.join(tempfile.mkdtemp(), "subs.db")
    store = SubscriptionStore(db)
    store.upsert(Subscription(id="s1", mode="CRON", target_agent="papers", tenant=DEFAULT_SCOPE,
                              deliver_to=["telegram"], prompt="arxiv MoE", status="active"))
    store2 = SubscriptionStore(db)                 # a fresh handle (cross-thread read)
    app = FastAPI()
    register_events_routes(app, runtime=object(), store=store2, concierge=None, engine=None)
    return TestClient(app)


def test_status_gate_and_backends():
    r = _client().get("/api/events/status")
    assert r.status_code == 200
    b = r.json()
    assert b["enabled"] and b["worker_backend"] == "cuga" and b["concierge_backend"] == "react"
    assert "cuga" in b["backends"] and "features" in b


def test_channels_endpoint():
    r = _client().get("/api/events/channels")
    assert r.status_code == 200
    names = {c["name"] for c in r.json()["channels"]}
    assert {"web", "telegram", "discord"} <= names


def test_integrations_endpoint_no_ap():
    r = _client().get("/api/events/integrations")
    assert r.status_code == 200                     # never 500 even with engine=None
    for i in r.json()["integrations"]:
        assert i["status"] == "ap_not_configured"


def test_examples_endpoint():
    r = _client().get("/api/events/examples")
    assert r.status_code == 200 and len(r.json()["examples"]) >= 7


def test_subscriptions_endpoint_scoped():
    r = _client().get("/api/events/subscriptions")
    assert r.status_code == 200
    subs = r.json()["subscriptions"]
    assert len(subs) == 1 and subs[0]["target_agent"] == "papers" and subs[0]["mode"] == "CRON"


def test_invoke_direct_channel_delivery():
    """/invoke with deliver=True + a DIRECT channel source (Slack) sends the answer via the
    channel's direct adapter (delivery.send_direct) — NOT the capture sink. This is the receiving
    end of a scheduled/direct flow whose sink lives outside AP."""
    from events import delivery

    sent = []

    async def _fake_send_direct(channel, target, text, locus=""):
        sent.append((channel, target, text, locus))
        return True, "ok"

    class _Runtime:
        def get_agent(self, agent, scope=""):
            return object()                          # agent exists

        async def run(self, agent, thread_id, worker_input, scope="", deliver_to=None):
            return "the market brief"

    _orig = delivery.send_direct
    delivery.send_direct = _fake_send_direct
    os.environ["EVENTS_REPLY_METADATA"] = "0"        # deterministic answer (no footer)
    try:
        app = FastAPI()
        register_events_routes(app, runtime=_Runtime(), store=None, concierge=None, engine=None)
        c = TestClient(app)
        r = c.post("/invoke", json={"agent": "pricebot", "deliver": True, "text": "brief",
                                    "source": {"type": "channel", "name": "slack",
                                               "thread_id": "gw:slack:C1"},
                                    "event": {"kind": "tick", "payload": {}}})
        assert r.status_code == 200 and r.json()["ok"] is True
        # a FIRE (kind=tick) is labeled with the flow-fired header; no locus in this thread id
        assert len(sent) == 1 and sent[0][:2] == ("slack", "C1"), sent
        assert sent[0][2].startswith("⚡ flow fired · cron tick") and "the market brief" in sent[0][2]
        assert sent[0][3] == ""
    finally:
        delivery.send_direct = _orig
        os.environ.pop("EVENTS_REPLY_METADATA", None)


def test_invoke_push_flow_delivers_to_direct_channel():
    """REGRESSION — the "flow ran green but nothing showed in Slack" bug. A PUSH flow's /invoke has
    an INTEGRATION source (gmail) — NOT a channel — and a SCOPE-PREFIXED thread_id that carries the
    sink (``…::gw:slack:<id>``). Delivery must still resolve the direct sink from the thread_id origin
    and send there. This guards against re-adding a ``source.type=="channel"`` gate OR a
    prefix-blind target parser (the two compounding causes of the original bug)."""
    from events import delivery

    sent = []

    async def _fake_send_direct(channel, target, text, locus=""):
        sent.append((channel, target, text, locus))
        return True, "ok"

    class _Runtime:
        def get_agent(self, agent, scope=""):
            return object()

        async def run(self, agent, thread_id, worker_input, scope="", deliver_to=None):
            return "email summary"

    _orig = delivery.send_direct
    delivery.send_direct = _fake_send_direct
    os.environ["EVENTS_REPLY_METADATA"] = "0"
    try:
        app = FastAPI()
        register_events_routes(app, runtime=_Runtime(), store=None, concierge=None, engine=None)
        c = TestClient(app)
        r = c.post("/invoke", json={
            "agent": "mailbot", "deliver": True, "text": "summarize",
            "source": {"type": "integration", "name": "gmail",
                       "thread_id": "default/default/admin::gw:slack:C0BEYJ9NATB#1699.9"},
            "event": {"kind": "new_email", "payload": {"subject": "Hi"}}})
        assert r.status_code == 200 and r.json()["ok"] is True
        # delivered to the SLACK sink parsed from the scope-prefixed thread_id — not 'gmail', not
        # dropped — labeled as a fire, and INTO the #locus thread (the arming thread's ts)
        assert len(sent) == 1 and sent[0][:2] == ("slack", "C0BEYJ9NATB"), sent
        assert sent[0][2].startswith("⚡ flow fired · gmail/new_email") and "email summary" in sent[0][2]
        assert sent[0][3] == "1699.9"
    finally:
        delivery.send_direct = _orig
        os.environ.pop("EVENTS_REPLY_METADATA", None)


def test_schedule_flow_direct_sink_has_no_ap_send_step():
    """A scheduled flow delivering to a DIRECT channel appends NO AP send step; instead the
    /invoke body's source is the channel (deliver=True) so CUGA sends the answer itself."""
    import asyncio
    from events.ap_engine import APEngine
    posted = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"id": "flow-direct-1"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append(("POST", url, json))
            return _Resp()

        async def delete(self, url, headers=None):
            return _Resp()

    eng = APEngine.__new__(APEngine)
    eng.base, eng.project_id, eng.gateway_token = "http://ap", "proj", "tok"
    eng.invoke_url = "http://cuga/invoke"

    async def _auth(c):
        return {}

    async def _pv(c, piece):
        return "1.0.0"

    async def _post_op(c, fid, op, hdrs):
        posted.append(("OP", op.get("type"), op))

    async def _ff(c, hdrs, name, pid):
        return None

    eng._auth, eng._piece_version, eng._post_op, eng.find_flow_by_name = _auth, _pv, _post_op, _ff

    import httpx as _httpx
    _orig = _httpx.AsyncClient
    _httpx.AsyncClient = lambda *a, **k: _Client()
    try:
        fid = asyncio.run(
            eng.create_schedule_flow(name="ea:x", agent="pricebot", thread_id="gw:slack:C1",
                                     prompt="brief", interval_seconds=3600, scope="t1",
                                     deliver_direct_channel="slack", deliver_direct_target="C1"))
    finally:
        _httpx.AsyncClient = _orig
    assert fid == "flow-direct-1"
    types = [o[1] for o in posted if o[0] == "OP"]
    # exactly trigger + http invoke + publish — NO channel send op
    assert types == ["UPDATE_TRIGGER", "ADD_ACTION", "LOCK_AND_PUBLISH"], types
    http_op = next(o[2] for o in posted if o[0] == "OP" and o[1] == "ADD_ACTION")
    body = http_op["request"]["action"]["settings"]["input"]["body"]["data"]
    assert body["deliver"] is True                                   # CUGA delivers
    assert body["source"] == {"type": "channel", "name": "slack", "thread_id": "gw:slack:C1"}


def test_callback_schedule_targets_the_job_endpoint_without_an_agent_envelope():
    import asyncio
    from events.ap_engine import APEngine

    posted = []

    class _Resp:
        status_code = 200
        text = '{"id":"flow-retention-1"}'

        def json(self):
            return {"id": "flow-retention-1"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append(("POST", url, json))
            return _Resp()

        async def delete(self, url, headers=None):
            return _Resp()

    eng = APEngine.__new__(APEngine)
    eng.base, eng.project_id, eng.gateway_token = "http://ap", "proj", "tok"

    async def _auth(c):
        return {}

    async def _pv(c, piece):
        return "1.0.0"

    async def _post_op(c, fid, op, hdrs):
        posted.append(("OP", op.get("type"), op))

    async def _ff(c, hdrs, name, pid):
        return None

    async def _valid(c, fid, hdrs):
        return None

    eng._auth = _auth
    eng._piece_version = _pv
    eng._post_op = _post_op
    eng.find_flow_by_name = _ff
    eng._assert_steps_valid = _valid

    import httpx as _httpx

    original = _httpx.AsyncClient
    _httpx.AsyncClient = lambda *a, **k: _Client()
    try:
        flow_id = asyncio.run(
            eng.create_callback_schedule_flow(
                name="memory-retention",
                callback_url="http://cuga/api/internal/memory/automations/a1/runs",
                body={"automation_id": "a1"},
                cron="0 2 * * 0",
            )
        )
    finally:
        _httpx.AsyncClient = original

    assert flow_id == "flow-retention-1"
    operations = [item for item in posted if item[0] == "OP"]
    assert [item[1] for item in operations] == [
        "UPDATE_TRIGGER",
        "ADD_ACTION",
        "LOCK_AND_PUBLISH",
    ]
    http_input = operations[1][2]["request"]["action"]["settings"]["input"]
    assert http_input["url"].endswith("/api/internal/memory/automations/a1/runs")
    assert http_input["headers"] == {"X-Gateway-Token": "tok"}
    assert http_input["body"]["data"] == {"automation_id": "a1"}


def test_box_direct_new_files_filter():
    """box_direct.new_files_since: files only (no folders), created strictly after `since`."""
    import asyncio
    from events import box_direct

    entries = [
        {"type": "file", "id": "1", "name": "old.pdf", "created_at": "2026-07-01T00:00:00-07:00"},
        {"type": "file", "id": "2", "name": "new.pdf", "created_at": "2026-07-06T09:00:00-07:00"},
        {"type": "folder", "id": "3", "name": "sub", "created_at": "2026-07-06T10:00:00-07:00"},
    ]

    class _Resp:
        status_code = 200

        def json(self):
            return {"entries": entries}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            return _Resp()

    import httpx as _httpx
    _orig = _httpx.AsyncClient
    _httpx.AsyncClient = lambda *a, **k: _Client()
    os.environ["BOX_DEV_TOKEN"] = "t"
    try:
        got = asyncio.run(box_direct.new_files_since("0", "2026-07-05T00:00:00-07:00"))
        assert [f["id"] for f in got] == ["2"], got        # new.pdf only; folder excluded, old excluded
        allf = asyncio.run(box_direct.new_files_since("0", None))
        assert [f["id"] for f in allf] == ["1", "2"]        # since=None → all files, still no folder
    finally:
        _httpx.AsyncClient = _orig
        os.environ.pop("BOX_DEV_TOKEN", None)


def test_box_poll_endpoint_dispatches_new_files():
    """POST /api/events/box/poll (gateway-token) lists new files and fires the watcher per file
    through /invoke, returning the newest created_at as the next baseline. AP-free path."""
    import httpx as _httpx
    from events import box_direct

    posted = []

    async def _fake_new(folder, since, tok=None):
        return [{"id": "9", "name": "resume.pdf", "created_at": "2026-07-06T11:00:00-07:00"}]

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "answer": "SKIP — not a fit"}

    class _Client:                       # intercepts the internal POST /invoke
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append(json)
            return _Resp()

    async def _fake_fetch(file_id, name="", tok=None):
        return {"kind": "text", "text": "Jane Doe — 8 years of Python.", "truncated": False,
                "bytes": 28}

    # The watermark file is REAL and module-level: without this the test reads whatever the last live
    # poll left on disk (so `newest` comes back as that date, not the fixture's) and then WRITES to it.
    # A test that mutates the developer's state is worse than a test that fails.
    _orig_new, _orig_cli = box_direct.new_files_since, _httpx.AsyncClient
    _orig_since, _orig_fetch = box_direct._SINCE_FILE, box_direct.fetch_content
    box_direct._SINCE_FILE = os.path.join(tempfile.mkdtemp(), "box_since.json")
    box_direct.new_files_since = _fake_new
    box_direct.fetch_content = _fake_fetch
    _httpx.AsyncClient = lambda *a, **k: _Client()
    try:
        app = FastAPI()
        register_events_routes(app, runtime=object(), store=None, concierge=None, engine=None,
                               gateway_token="gw-tok")
        c = TestClient(app)
        assert c.post("/api/events/box/poll", json={"folder_id": "0"}).status_code == 401  # no token
        r = c.post("/api/events/box/poll", headers={"X-Gateway-Token": "gw-tok"},
                   json={"folder_id": "0", "agent": "resume_judge", "deliver_to": "slack"})
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["ok"] and [f["id"] for f in b["processed"]] == ["9"]
        assert b["newest"] == "2026-07-06T11:00:00-07:00"
        # one /invoke fired for the file, with a direct-Slack sink + the file payload
        assert len(posted) == 1 and posted[0]["agent"] == "resume_judge"
        assert posted[0]["deliver"] is True and posted[0]["source"]["name"] == "slack"
        assert posted[0]["event"]["payload"]["file_id"] == "9"
        # THE DOWNLOAD STEP: the agent is handed the file's CONTENT, not just its name. Asked to judge
        # a resume it cannot read, an LLM will invent one — so this is a correctness check, not polish.
        assert "Jane Doe — 8 years of Python." in posted[0]["text"]
        assert posted[0]["event"]["payload"]["content_kind"] == "text"
    finally:
        box_direct.new_files_since = _orig_new
        box_direct.fetch_content = _orig_fetch
        box_direct._SINCE_FILE = _orig_since
        _httpx.AsyncClient = _orig_cli


def test_discord_direct_send_and_delivery():
    """discord_direct.send_message posts with a Bot token; delivery.send_direct routes 'discord'."""
    import asyncio
    import httpx as _httpx
    from events import discord_direct, delivery

    sent = {}

    class _Resp:
        status_code = 200

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent.update({"url": url, "auth": (headers or {}).get("Authorization"), "content": (json or {}).get("content")})
            return _Resp()

    orig = _httpx.AsyncClient
    _httpx.AsyncClient = lambda *a, **k: _C()
    os.environ["DISCORD_BOT_TOKEN"] = "abc"
    try:
        asyncio.run(discord_direct.send_message("C123", "hello"))
        assert sent["url"].endswith("/channels/C123/messages")
        assert sent["auth"] == "Bot abc" and sent["content"] == "hello"
        # delivery.send_direct dispatches 'discord' to discord_direct
        ok, why = asyncio.run(delivery.send_direct("discord", "C999", "yo"))
        assert ok and sent["url"].endswith("/channels/C999/messages")
    finally:
        _httpx.AsyncClient = orig
        os.environ.pop("DISCORD_BOT_TOKEN", None)


def test_arm_discord_direct_backend():
    """POST /api/events/admin/channels/discord/arm → direct backend (no AP flow), given a bot token."""
    os.environ["DISCORD_BOT_TOKEN"] = "abc"
    try:
        app = FastAPI()
        register_events_routes(app, runtime=object(), store=None, concierge=None, engine=None)
        r = TestClient(app).post("/api/events/admin/channels/discord/arm",
                                 headers={"x-user-id": "admin"}, json={})
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["ok"] and b["backend"] == "direct" and "ap_flow_id" not in b
    finally:
        os.environ.pop("DISCORD_BOT_TOKEN", None)


def test_inbound_webhook_triages_and_delivers():
    """POST /api/events/hook/{name} → renders the payload → fires an agent via /invoke → returns the
    triage. With deliver_to+target it also delivers to a direct channel. Direct, no AP."""
    import httpx as _httpx
    posted = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "answer": "HighCPU on checkout-api — P1 — restart pods"}

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append(json)
            return _Resp()

    _orig = _httpx.AsyncClient
    _httpx.AsyncClient = lambda *a, **k: _C()
    os.environ["GATEWAY_TOKEN"] = "gw"
    try:
        app = FastAPI()
        register_events_routes(app, runtime=object(), store=None, concierge=None, engine=None,
                               gateway_token="gw")
        c = TestClient(app)
        r = c.post("/api/events/hook/monitoring?agent=incident_triage&deliver_to=slack&target=C1",
                   json={"alert": "HighCPU", "service": "checkout-api", "value": "97%"})
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["ok"] and b["webhook"] == "monitoring" and "P1" in (b["answer"] or "")
        assert b["routed"] is False                          # PINNED mode
        # the internal /invoke got the payload as text + the direct-channel sink
        inv = posted[0]
        assert inv["agent"] == "incident_triage" and inv["deliver"] is True
        assert inv["source"]["name"] == "slack" and "HighCPU" in inv["text"]
        assert inv["event"]["payload"]["service"] == "checkout-api"
    finally:
        _httpx.AsyncClient = _orig
        os.environ.pop("GATEWAY_TOKEN", None)


def test_inbound_webhook_routed_uses_the_concierge():
    """?route=1 makes the webhook pick the agent the way chat does: it calls /invoke with
    agent='concierge' (the runtime router) instead of a pinned agent name — so the caller needs to
    know nothing about the agent catalog. The concierge's chosen agent is echoed back as `agent`."""
    import httpx as _httpx
    posted = []

    class _Resp:
        status_code = 200

        def json(self):
            # /invoke's shape when agent='concierge' routed to a worker: meta.agent is the pick
            return {"ok": True, "answer": "Payment dispute — P1 — open the case",
                    "meta": {"agent": "incident_triage"}}

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append(json)
            return _Resp()

    _orig = _httpx.AsyncClient
    _httpx.AsyncClient = lambda *a, **k: _C()
    os.environ["GATEWAY_TOKEN"] = "gw"
    try:
        app = FastAPI()
        register_events_routes(app, runtime=object(), store=None, concierge=None, engine=None,
                               gateway_token="gw")
        c = TestClient(app)
        r = c.post("/api/events/hook/stripe?route=1&deliver_to=slack&target=C1",
                   json={"type": "charge.dispute.created", "amount": 48000, "reason": "fraudulent"})
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["ok"] and b["routed"] is True
        # the router (not a pinned agent) handled it, and the CHOSEN agent is surfaced to the caller
        assert b["agent"] == "incident_triage"               # from meta, the concierge's pick
        inv = posted[0]
        assert inv["agent"] == "concierge"                   # routed through the chat brain
        assert inv["deliver"] is True and inv["source"]["name"] == "slack"
        # the routed directive must FORCE delegation (self-answering leaves meta agent-less and
        # the caller sees agent='concierge' — the live flake this wording fixed)
        assert "call the one best-suited pre-built agent tool" in inv["text"].lower()
        assert "do not answer it yourself" in inv["text"].lower()
        assert inv["event"]["payload"]["reason"] == "fraudulent"
    finally:
        _httpx.AsyncClient = _orig
        os.environ.pop("GATEWAY_TOKEN", None)


def test_webhook_key_gate():
    """When EVENTS_WEBHOOK_KEY is set, the webhook requires a matching ?key= (else 401)."""
    os.environ["EVENTS_WEBHOOK_KEY"] = "s3cr3t"
    try:
        app = FastAPI()
        register_events_routes(app, runtime=object(), store=None, concierge=None, engine=None)
        c = TestClient(app)
        assert c.post("/api/events/hook/x", json={}).status_code == 401            # no key
        # wrong key also 401; correct key passes the gate (then fails downstream w/o a live server — fine)
        assert c.post("/api/events/hook/x?key=nope", json={}).status_code == 401
    finally:
        os.environ.pop("EVENTS_WEBHOOK_KEY", None)


def test_setup_guides_connection_status_and_scope():
    """GET /api/events/setup-guides returns, per connector: cred present + USER/TENANT scope, AND the
    live connection status (connected + connection_scope) — the 'am I connected' the Studio renders."""
    app = FastAPI()
    register_events_routes(app, runtime=object(), store=None, concierge=None, engine=None)
    r = TestClient(app).get("/api/events/setup-guides", headers={"x-user-id": "admin"})
    assert r.status_code == 200, r.text            # regresses the KeyError('app') we hit
    guides = {g["label"]: g for g in r.json()["guides"]}
    # every connector carries the connection fields the UI needs
    for g in r.json()["guides"]:
        assert "connected" in g and "connection_scope" in g and "conn_status" in g
        for c in g.get("creds", []):
            assert c["scope"] in ("user", "tenant") and "present" in c
    # channels are TENANT connections; integrations are USER connections
    assert guides["Slack"]["connection_scope"] == "tenant"
    assert guides["GitHub"]["connection_scope"] == "user"
    assert guides["Gmail"]["connection_scope"] == "user"


def test_channels_report_direct_vs_ap_backend():
    """The channels endpoint tells the UI HOW each channel talks to the world (ADR-0008):
    Slack/Discord are direct backends, Telegram is AP."""
    r = _client().get("/api/events/channels")
    chans = {c["name"]: c for c in r.json()["channels"]}
    assert chans["slack"]["backend"] == "direct"
    assert chans["discord"]["backend"] == "direct"
    assert chans["telegram"]["backend"] == "ap"
    # all channels are live now — no stale "Phase 3" markers
    assert all(c["live"] for c in chans.values())


class _FakeRuntime:
    """Minimal AgentRuntime for the agent-CRUD endpoints — a dict-backed store."""
    def __init__(self):
        self._store = {}

    def upsert_agent(self, spec, *, scope="default/default"):
        self._store[(scope, spec.name)] = spec
        return spec.name

    def get_agent(self, name, *, scope="default/default"):
        return self._store.get((scope, name))

    def list_agents(self, *, scope="default/default"):
        return [s for (sc, _), s in self._store.items() if sc == scope]


def _agent_client(runtime):
    app = FastAPI()
    register_events_routes(app, runtime=runtime, store=None, concierge=None, engine=None)
    return TestClient(app)


def test_mcp_servers_endpoint():
    r = _agent_client(_FakeRuntime()).get("/api/events/mcp-servers")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()["servers"]}
    assert "cuga-web" in names and "cuga-finance" in names


def test_agent_create_list_and_update():
    rt = _FakeRuntime()
    c = _agent_client(rt)
    # create
    body = {"name": "digestbot", "backend": "cuga", "prompt": "post a digest",
            "mcp_servers": ["cuga-web"], "channels": ["web", "slack"],
            "integrations": [{"app": "github", "ownership": "per-user"}], "access": ["builder"]}
    r = c.post("/api/events/agents", json=body, headers={"x-user-id": "admin"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    # it shows up in the list
    names = {a["name"] for a in c.get("/api/events/agents").json()["agents"]}
    assert "digestbot" in names
    # update via PUT
    body2 = dict(body, prompt="post a BETTER digest")
    r = c.put("/api/events/agents/digestbot", json=body2, headers={"x-user-id": "admin"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert rt.get_agent("digestbot", scope="default/default").prompt == "post a BETTER digest"


def test_agents_carry_example_utterances():
    """Each agent row includes up to 3 example utterances (from the catalog) so the Agents tab can
    render clickable 'Try' chips."""
    rt = _FakeRuntime()
    from events.runtime import AgentSpec
    rt.upsert_agent(AgentSpec(name="pricebot", backend="cuga"), scope="default/default")
    agents = {a["name"]: a for a in _agent_client(rt).get("/api/events/agents").json()["agents"]}
    assert "examples" in agents["pricebot"]
    assert isinstance(agents["pricebot"]["examples"], list)
    assert len(agents["pricebot"]["examples"]) >= 1        # catalog has pricebot utterances


def test_agent_create_validation_rejects_bad_input():
    c = _agent_client(_FakeRuntime())
    # unknown mcp server
    r = c.post("/api/events/agents", json={"name": "x", "mcp_servers": ["not-a-server"]},
               headers={"x-user-id": "admin"})
    assert r.status_code == 400
    # whitespace in name
    r = c.post("/api/events/agents", json={"name": "bad name"}, headers={"x-user-id": "admin"})
    assert r.status_code == 400
    # PUT to a non-existent agent → 404
    r = c.put("/api/events/agents/ghost", json={"name": "ghost"}, headers={"x-user-id": "admin"})
    assert r.status_code == 404


def test_agent_triggers_survive_the_save_round_trip():
    """Trigger-grain declarations must round-trip through the editor's save path. Both the Studio
    form and _agent_spec_from_body used to keep only {app, ownership} — so EDITING an agent silently
    widened it from 2 declared GitHub triggers to all 14. Aliases canonicalize on the way in
    ('pull_request' is the legacy name for new_pr) and duplicates collapse."""
    rt = _FakeRuntime()
    c = _agent_client(rt)
    body = {"name": "pr_bot", "backend": "cuga",
            "integrations": [
                {"app": "github", "ownership": "per-user",
                 "triggers": ["new_pr", "pull_request", "new_review_request"]},
                {"app": "gmail", "ownership": "per-user"}]}
    r = c.post("/api/events/agents", json=body, headers={"x-user-id": "admin"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    a = {x["name"]: x for x in c.get("/api/events/agents").json()["agents"]}["pr_bot"]
    gh = next(i for i in a["integrations"] if i["app"] == "github")
    gm = next(i for i in a["integrations"] if i["app"] == "gmail")
    assert gh["triggers"] == ["new_pr", "new_review_request"]   # canonical, deduped, order kept
    assert "triggers" not in gm                                 # no declaration = all triggers


def test_agent_create_rejects_an_unknown_trigger():
    """A typo'd trigger is a 400 naming the known events — not a silently-stored declaration the
    concierge can never match."""
    c = _agent_client(_FakeRuntime())
    r = c.post("/api/events/agents",
               json={"name": "x", "integrations": [
                   {"app": "github", "ownership": "per-user", "triggers": ["new_prr"]}]},
               headers={"x-user-id": "admin"})
    assert r.status_code == 400
    assert "new_prr" in r.json()["error"] and "new_pr" in r.json()["error"]


def test_slack_mention_gate():
    """EVENTS_SLACK_CHAT=mention: a channel message reaches CHAT only when it @mentions the bot
    (mention stripped from the text); DMs always pass; default mode passes everything unchanged."""
    import asyncio

    from events import slack_direct as sd
    ch = {"type": "message", "text": "hello world", "channel": "C1", "user": "U1"}
    at = {"type": "message", "text": "<@UBOT> what's our policy?", "channel": "C1", "user": "U1"}
    dm = {"type": "message", "text": "hi", "channel": "D1", "channel_type": "im", "user": "U1"}
    os.environ["SLACK_BOT_USER_ID"] = "UBOT"
    try:
        os.environ["EVENTS_SLACK_CHAT"] = "mention"
        assert asyncio.run(sd.mention_gate(ch)) == (False, "hello world")
        ok, cleaned = asyncio.run(sd.mention_gate(at))
        assert ok and cleaned == "what's our policy?"
        assert asyncio.run(sd.mention_gate(dm))[0] is True          # 1:1 im always through
        # a reply in a thread the BOT rooted (a trigger's delivery) passes without a mention…
        reply = {"type": "message", "text": "yes, escalate it", "channel": "C1", "user": "U1",
                 "thread_ts": "1700.1", "parent_user_id": "UBOT"}
        assert asyncio.run(sd.mention_gate(reply)) == (True, "yes, escalate it")
        # …but a reply in a HUMAN-rooted thread the bot never joined stays gated
        # (SLACK_BOT_TOKEN may exist in .env-loaded envs; kill it so the API fallback is inert)
        os.environ["SLACK_BOT_TOKEN"] = ""
        assert asyncio.run(sd.mention_gate(dict(reply, parent_user_id="U9")))[0] is False
        # …and a follow-up in a thread the bot has ANSWERED IN passes without a mention:
        # "@bot weather in NY?" → bot replies in-thread → "what about NYC?" must reach chat
        sd.remember_thread("C1", "1700.1")
        ok, t2 = asyncio.run(sd.mention_gate(dict(reply, parent_user_id="U9",
                                                  text="what about NYC?")))
        assert ok and t2 == "what about NYC?"
        os.environ.pop("SLACK_BOT_TOKEN", None)
        os.environ["EVENTS_SLACK_CHAT"] = "all"
        assert asyncio.run(sd.mention_gate(ch)) == (True, "hello world")   # default: unchanged
    finally:
        os.environ.pop("EVENTS_SLACK_CHAT", None)
        os.environ.pop("SLACK_BOT_USER_ID", None)


def test_triggers_endpoint_serves_the_registry():
    """GET /api/events/triggers is the registry, verbatim — the Studio's trigger picker and the
    slides deck both render it, so it must agree with triggers.py exactly."""
    from events import triggers as tr
    r = _agent_client(_FakeRuntime()).get("/api/events/triggers")
    assert r.status_code == 200
    d = r.json()
    assert {a["app"] for a in d["apps"]} == set(tr.apps())
    assert d["total"] == len(tr.rows()) and sorted(d["kinds"]) == sorted(tr.event_kinds())
    gh = next(a for a in d["apps"] if a["app"] == "github")
    assert len(gh["triggers"]) == len(tr.events_for("github"))
    assert gh["triggers"][0]["default"] is True                 # the app default leads its group
    row = next(t for t in gh["triggers"] if t["event"] == "new_pr")
    assert row["backend"] == "ap" and row["fire"] == "synth"
    assert row["slots"][0]["name"] == "repo" and row["slots"][0]["required"] is True


def test_integrations_box_direct_backend_reports_connected():
    """With EVENTS_BOX_BACKEND=direct + a token, Box reads 'connected' even though there's no AP
    connection — the direct-backend override (else the UI would show a live token as disconnected)."""
    old_be = os.environ.get("EVENTS_BOX_BACKEND")
    old_tok = os.environ.get("BOX_DEV_TOKEN")
    os.environ["EVENTS_BOX_BACKEND"] = "direct"
    os.environ["BOX_DEV_TOKEN"] = "dev-token-xyz"
    try:
        rows = {i["name"]: i for i in _client().get("/api/events/integrations").json()["integrations"]}
        assert rows["box"]["status"] == "connected" and rows["box"]["connected"] is True
        assert rows["box"]["backend"] == "direct"
    finally:
        os.environ.pop("BOX_DEV_TOKEN", None) if old_tok is None else os.environ.__setitem__("BOX_DEV_TOKEN", old_tok)
        os.environ.pop("EVENTS_BOX_BACKEND", None) if old_be is None else os.environ.__setitem__("EVENTS_BOX_BACKEND", old_be)


def test_integrations_github_env_token_does_not_fake_connected():
    """A .env GITHUB_TOKEN must NOT make github look connected. GitHub is OAuth (piece-github accepts
    only OAUTH2), so a PAT in .env doesn't auto-connect anything — the status stays 'not_connected'
    until the user consents. The old 'auto_connect_pending' heuristic was a lie once github went OAuth."""
    class _Engine:
        base = "http://ap"
        project_grain = "tenant"

        async def list_connections(self, project_name=None):
            return []                                   # no github connection yet

    old = os.environ.get("GITHUB_TOKEN")
    os.environ["GITHUB_TOKEN"] = "ghp_test_value"
    try:
        app = FastAPI()
        register_events_routes(app, runtime=object(), store=None, concierge=None, engine=_Engine())
        rows = {i["name"]: i for i in TestClient(app).get("/api/events/integrations").json()["integrations"]}
        assert rows["github"]["status"] == "not_connected", rows["github"]
        assert rows["github"]["auth"] == "oauth"        # connect via consent, not a pasted token
    finally:
        os.environ.pop("GITHUB_TOKEN", None) if old is None else os.environ.__setitem__("GITHUB_TOKEN", old)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
