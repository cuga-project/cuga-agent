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
