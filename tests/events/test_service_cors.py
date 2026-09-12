"""The Studio's browser calls must survive the cross-origin hop to the eventing service.

WHY THIS EXISTS
---------------
CUGA (:7860) serves the SPA; this service (:8100) answers ``/api/concierge``, ``/api/events/*`` and
``/invoke``. A browser treats those two ports as different origins, so every one of those calls is
preceded by an OPTIONS preflight — and a preflight that comes back without ``Access-Control-*``
headers makes the browser CANCEL the request.

That is what happened: CORS was added only when ``EVENTS_CORS_ORIGINS`` was set, on the reasoning
that "unset means combined mode, same origin, no CORS needed". Combined mode was removed, so unset
came to mean *split on localhost* — the preflight returned a bare 405 and the Studio's concierge box
printed **nothing at all**. Not an error, not an empty reply: the fetch never completed, so the UI
had nothing to render. Only Code Engine worked, because the deploy script is the one place that sets
the variable.

The regression is invisible from the server side — every curl succeeds, because curl does not
preflight. Hence this test.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend"))

from events import service  # noqa: E402

PREFLIGHT = {
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "content-type",
}


def _client(monkeypatch, tmp_path, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    monkeypatch.setenv("EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("EVENTS_SCHEDULER", "native")
    return TestClient(service.create_app())


@pytest.mark.parametrize("path", ["/api/concierge", "/api/events/status", "/invoke"])
def test_preflight_is_allowed_from_the_cuga_origin_without_configuration(monkeypatch, tmp_path, path):
    """The default case — nobody set EVENTS_CORS_ORIGINS, which is every local `make up-noap`."""
    c = _client(monkeypatch, tmp_path, EVENTS_CORS_ORIGINS=None, CUGA_URL="http://localhost:7860")
    r = c.options(path, headers={"Origin": "http://localhost:7860", **PREFLIGHT})
    assert r.status_code == 200, f"preflight for {path} returned {r.status_code} — the browser cancels"
    assert r.headers.get("access-control-allow-origin") == "http://localhost:7860"


def test_both_localhost_spellings_are_allowed(monkeypatch, tmp_path):
    """The SPA is reachable at either spelling and the browser sends whichever was typed."""
    c = _client(monkeypatch, tmp_path, EVENTS_CORS_ORIGINS=None, CUGA_URL="http://127.0.0.1:7860")
    for origin in ("http://127.0.0.1:7860", "http://localhost:7860"):
        r = c.options("/api/concierge", headers={"Origin": origin, **PREFLIGHT})
        assert r.headers.get("access-control-allow-origin") == origin, origin


def test_an_explicit_setting_still_wins(monkeypatch, tmp_path):
    """Code Engine pins the deployed CUGA route; the derived default must not widen it."""
    c = _client(
        monkeypatch,
        tmp_path,
        EVENTS_CORS_ORIGINS="https://cuga-core.example.com",
        CUGA_URL="http://localhost:7860",
    )
    ok = c.options("/api/concierge", headers={"Origin": "https://cuga-core.example.com", **PREFLIGHT})
    assert ok.headers.get("access-control-allow-origin") == "https://cuga-core.example.com"

    nope = c.options("/api/concierge", headers={"Origin": "http://evil.example", **PREFLIGHT})
    assert nope.headers.get("access-control-allow-origin") != "http://evil.example"
