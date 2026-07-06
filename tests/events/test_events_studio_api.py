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

    async def _fake_send_direct(channel, target, text):
        sent.append((channel, target, text))
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
        assert sent == [("slack", "C1", "the market brief")], sent
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

    _orig_new, _orig_cli = box_direct.new_files_since, _httpx.AsyncClient
    box_direct.new_files_since = _fake_new
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
    finally:
        box_direct.new_files_since = _orig_new
        _httpx.AsyncClient = _orig_cli


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in fns:
        try:
            fn(); print(f"PASS  {name}"); passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
