"""Admin endpoints must AUTHENTICATE before consulting roles (Sami, PR 603 #1, P1).

The reported hole: `_is_admin()` reads a principal built from caller-controlled input — the request
body's `scope`, a query param, or the X-User-Id header — and `service.py` bootstraps the default
identity as `admin`. So with GATEWAY_TOKEN configured and EVENTS_ALLOW_UNAUTHENTICATED=0, an
unauthenticated `POST /api/events/admin/users` created ANOTHER admin and returned 200: the caller
asserted who they were and the role lookup agreed.

Authorization cannot be the first gate. These tests pin that every /api/events/admin/* route now
refuses an unauthenticated caller, and that the documented dev opt-out still works.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend"))
from events.app import register_events_routes  # noqa: E402

TOKEN = "gw-secret-for-tests"

# Every admin route, with a body good enough to reach the role check if auth let it through.
ADMIN_CALLS = [
    ("get", "/api/events/admin/users", None),
    ("post", "/api/events/admin/users", {"user_id": "mallory", "roles": ["admin"]}),
    ("post", "/api/events/admin/channels/slack/arm", {}),
    ("get", "/api/events/admin/oauth-apps", None),
    ("post", "/api/events/admin/oauth-apps", {"app": "gmail", "client_id": "x", "client_secret": "y"}),
    ("post", "/api/events/admin/credential", {"app": "github", "token": "ghp_x"}),
]


def _client(monkeypatch, *, token=TOKEN, open_by_choice=False):
    monkeypatch.setenv("GATEWAY_TOKEN", token)
    monkeypatch.setenv("EVENTS_ALLOW_UNAUTHENTICATED", "1" if open_by_choice else "")
    monkeypatch.setenv("EVENTS_DB", ":memory:")
    app = FastAPI()
    register_events_routes(app, runtime=object(), store=None, concierge=None, engine=None)
    return TestClient(app)


def _call(c, method, path, body):
    return c.get(path) if method == "get" else c.post(path, json=body or {})


@pytest.mark.parametrize("method,path,body", ADMIN_CALLS, ids=[f"{m}:{p}" for m, p, _ in ADMIN_CALLS])
def test_admin_route_refuses_an_unauthenticated_caller(monkeypatch, method, path, body):
    c = _client(monkeypatch)
    r = _call(c, method, path, body)
    assert r.status_code == 401, f"{method.upper()} {path} answered {r.status_code}, not 401"


@pytest.mark.parametrize("method,path,body", ADMIN_CALLS, ids=[f"{m}:{p}" for m, p, _ in ADMIN_CALLS])
def test_admin_route_refuses_a_WRONG_token(monkeypatch, method, path, body):
    c = _client(monkeypatch)
    r = (
        c.get(path, headers={"X-Gateway-Token": "wrong"})
        if method == "get"
        else c.post(path, json=body or {}, headers={"X-Gateway-Token": "wrong"})
    )
    assert r.status_code == 401


def test_the_reported_privilege_escalation_is_closed(monkeypatch):
    """Sami's exact reproduction: unauthenticated POST creating another admin, HTTP 200."""
    c = _client(monkeypatch)
    r = c.post("/api/events/admin/users", json={"user_id": "mallory", "roles": ["admin"]})
    assert r.status_code == 401
    assert "authentication" in (r.json().get("error") or "").lower()


def test_asserting_an_admin_scope_no_longer_helps(monkeypatch):
    """The principal was caller-controlled — claiming to be admin must not get past the gate."""
    c = _client(monkeypatch)
    r = c.post(
        "/api/events/admin/users",
        json={"user_id": "mallory", "roles": ["admin"], "scope": "default/default/admin"},
        headers={"X-User-Id": "admin"},
    )
    assert r.status_code == 401


@pytest.mark.parametrize("method,path,body", ADMIN_CALLS, ids=[f"{m}:{p}" for m, p, _ in ADMIN_CALLS])
def test_a_correct_token_gets_past_the_AUTH_gate(monkeypatch, method, path, body):
    """Authentication passing is not the same as the call succeeding — it just must not be 401."""
    c = _client(monkeypatch)
    r = (
        c.get(path, headers={"X-Gateway-Token": TOKEN})
        if method == "get"
        else c.post(path, json=body or {}, headers={"X-Gateway-Token": TOKEN})
    )
    assert r.status_code != 401


def test_the_documented_dev_opt_out_still_opens_it(monkeypatch):
    """EVENTS_ALLOW_UNAUTHENTICATED=1 is the same escape hatch the other events seams use. A gate
    nobody can open for local work gets routed around instead."""
    c = _client(monkeypatch, open_by_choice=True)
    assert c.get("/api/events/admin/users").status_code != 401


def test_no_gateway_token_configured_still_fails_CLOSED(monkeypatch):
    """An unset secret must refuse, not disable the check — that inversion is the whole point."""
    c = _client(monkeypatch, token="")
    assert c.get("/api/events/admin/users").status_code == 401
