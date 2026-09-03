"""Build an agent from nothing — no roster, no seed, no `cuga_*` server.

WHY THIS FILE EXISTS. Every other agent in the suite comes from a shipped roster (pricebot,
geobot, pr_reviewer …) or the seed set, and all of them use the built-in `cuga_*` MCP servers. A
suite made only of those can pass while the product is quietly unusable by anyone whose agents are
not ours — it would not have caught the MCP allow-list, which rejected any server outside the
built-in seven and so made "bring your own" impossible.

So everything here is deliberately foreign: an invented agent name, an invented MCP server, an
invented tool. If this file passes, somebody starting from an empty deployment can build a working
agent, which is the property the roster-based tests cannot demonstrate.

Uses the REAL AgentStore rather than a fake, so persistence is exercised too.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

# `app.py` uses relative imports, so it has to be loaded AS the `events` package rather than flat
# on sys.path — same shim as test_events_studio_api.py.
_EV = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend", "events"))
if "events" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "events", os.path.join(_EV, "__init__.py"), submodule_search_locations=[_EV]
    )
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["events"] = _pkg
    _spec.loader.exec_module(_pkg)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from events.agent_store import AgentStore  # noqa: E402
from events.app import register_events_routes  # noqa: E402
from events.runtime import AgentStoreRuntime  # noqa: E402

pytestmark = pytest.mark.unit

ADMIN = {"x-user-id": "admin"}


def _client(db: str = ":memory:"):
    """A real store behind the real routes — the closest thing to a fresh deployment."""
    runtime = AgentStoreRuntime(agent_store=AgentStore(db))
    app = FastAPI()
    register_events_routes(app, runtime=runtime, store=None, concierge=None, engine=None)
    return TestClient(app), runtime


# ---------------------------------------------------------------- creation
def test_an_empty_deployment_can_build_a_working_agent():
    """Nothing seeded. Create one agent that shares nothing with our rosters, and get it back."""
    c, _ = _client()

    assert c.get("/api/events/agents", headers=ADMIN).json()["agents"] == []  # genuinely empty

    body = {
        "name": "invoice_triage",  # not in any roster
        "backend": "cuga",
        "prompt": "Read an invoice and decide whether it needs a human.",
        "mcp_servers": ["acme_ledger"],  # a server this codebase has never heard of
        "channels": ["slack"],
    }
    r = c.post("/api/events/agents", json=body, headers=ADMIN)
    assert r.status_code in (200, 201), r.text

    agents = c.get("/api/events/agents", headers=ADMIN).json()["agents"]
    assert [a["name"] for a in agents] == ["invoice_triage"]
    got = agents[0]
    assert got["mcp_servers"] == ["acme_ledger"]  # NOT rewritten, NOT rejected
    assert got["prompt"].startswith("Read an invoice")
    assert got["channels"] == ["slack"]


def test_the_agent_survives_a_restart():
    """A Studio-created agent must outlive the process — it exists only in the store, unlike a
    roster agent which self-heals from its YAML on every boot. That asymmetry is exactly why this
    is worth asserting: the roster hides the bug."""
    import tempfile

    db = os.path.join(tempfile.mkdtemp(), "agents.db")
    c1, _ = _client(db)
    c1.post(
        "/api/events/agents",
        json={"name": "invoice_triage", "prompt": "p", "mcp_servers": ["acme_ledger"]},
        headers=ADMIN,
    )

    c2, _ = _client(db)  # new process, same database
    agents = c2.get("/api/events/agents", headers=ADMIN).json()["agents"]
    assert [a["name"] for a in agents] == ["invoice_triage"]
    assert agents[0]["mcp_servers"] == ["acme_ledger"]


def test_editing_it_keeps_what_was_not_edited():
    """PUT is how the Studio saves an edit. Widening or dropping fields on save is a real bug class
    here — an edit once silently widened an agent's declared triggers to all of them."""
    c, _ = _client()
    c.post(
        "/api/events/agents",
        json={
            "name": "invoice_triage",
            "prompt": "original",
            "mcp_servers": ["acme_ledger"],
            "channels": ["slack"],
        },
        headers=ADMIN,
    )
    r = c.put(
        "/api/events/agents/invoice_triage",
        json={
            "name": "invoice_triage",
            "prompt": "revised",
            "mcp_servers": ["acme_ledger", "acme_ocr"],
            "channels": ["slack"],
        },
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text

    got = c.get("/api/events/agents", headers=ADMIN).json()["agents"][0]
    assert got["prompt"] == "revised"
    assert got["mcp_servers"] == ["acme_ledger", "acme_ocr"]
    assert got["channels"] == ["slack"]  # untouched by the edit, still there


def test_editing_an_agent_that_does_not_exist_is_a_404():
    c, _ = _client()
    r = c.put("/api/events/agents/ghost", json={"name": "ghost"}, headers=ADMIN)
    assert r.status_code == 404


# ---------------------------------------------------------------- guardrails
@pytest.mark.parametrize(
    "body,why",
    [
        ({"name": "bad name"}, "whitespace in the name"),
        ({"name": ""}, "empty name"),
        ({"name": "x", "backend": "wat"}, "unknown backend"),
        ({"name": "x", "channels": ["carrier-pigeon"]}, "unknown channel"),
    ],
)
def test_bad_input_is_still_refused(body, why):
    """Removing the MCP allow-list must not have removed validation wholesale — the checks that
    protect a real invariant stay."""
    c, _ = _client()
    assert c.post("/api/events/agents", json=body, headers=ADMIN).status_code == 400, why


def test_a_foreign_mcp_server_is_not_a_bad_input():
    """The counterpart to the parametrized cases above, stated positively: an unknown SERVER is
    legitimate (CUGA resolves it against whatever registry it was given), while an unknown CHANNEL
    is not (we implement channels, so we know the whole set)."""
    c, _ = _client()
    r = c.post(
        "/api/events/agents",
        json={"name": "x", "mcp_servers": ["something_we_have_never_heard_of"]},
        headers=ADMIN,
    )
    assert r.status_code in (200, 201), r.text
