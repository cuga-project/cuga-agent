"""Every auth gate refuses when its secret is missing.

These guards were all written as "enforce IF configured", which reads as safe and is the opposite:
an unset secret disabled the check, the service logged a warning, and then it served the request.
On a public URL that is not a warning — it is an open agent-execution endpoint. The live Code
Engine deployment had `EVENTS_WEBHOOK_KEY` unset, so `POST /api/events/hook/<name>` ran an agent
for anyone who knew the address.

Inverted to match `/run`'s existing `CUGA_RUN_ALLOW_UNAUTHENTICATED`: no credential means refuse,
and running open has to be stated out loud. Each test here asserts BOTH halves — that it refuses,
and that the documented opt-out still works — because a gate nobody can open gets worked around.

`closed_gates` (conftest) removes the suite-wide dev switch; without it these would all pass
vacuously, which is the failure mode worth guarding against.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

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

from events.app import register_events_routes  # noqa: E402

pytestmark = pytest.mark.unit

_ENVELOPE = {
    "agent": "pricebot",
    "text": "hi",
    "source": {"type": "time", "name": "cron"},
    "event": {"kind": "runonce"},
}


class _Runtime:
    """Enough runtime to get PAST the gate and return a status rather than raising. The refusal
    tests never reach it; the acceptance tests would blow up on `object()` and look like failures
    when they are actually successes."""

    def get_agent(self, agent, scope=""):
        return object()

    async def run(self, agent, thread_id, worker_input, scope="", deliver_to=None):
        return "ok"


def _client(runtime=None, **kw):
    app = FastAPI()
    register_events_routes(app, runtime=runtime or _Runtime(), store=None, concierge=None, engine=None, **kw)
    return TestClient(app)


# ---- the webhook: the one that was actually open in production ----------
def test_webhook_refuses_when_no_key_is_configured(closed_gates):
    """THE LIVE HOLE. `if want and not compare_digest(...)` skipped the check entirely when
    EVENTS_WEBHOOK_KEY was unset — and it was unset on the deployment. Anyone with the URL could
    run an agent: unauthenticated LLM spend, reading whatever the service account can reach."""
    closed_gates.delenv("EVENTS_WEBHOOK_KEY", raising=False)
    r = _client().post("/api/events/hook/anything", json={"hello": "world"})
    assert r.status_code == 401, "an unset webhook key still serves — the endpoint is open"
    assert "EVENTS_WEBHOOK_KEY" in r.json()["error"]  # and it says how to fix it


def test_webhook_refuses_a_wrong_key(closed_gates):
    """Unset, a WRONG ?key= was accepted too — not merely a missing one."""
    closed_gates.setenv("EVENTS_WEBHOOK_KEY", "s3cr3t")
    c = _client()
    assert c.post("/api/events/hook/x", json={}).status_code == 401  # none
    assert c.post("/api/events/hook/x?key=nope", json={}).status_code == 401  # wrong


def test_webhook_opens_only_when_asked(closed_gates):
    """The dev path must still exist, or people route around the gate."""
    closed_gates.delenv("EVENTS_WEBHOOK_KEY", raising=False)
    closed_gates.setenv("EVENTS_ALLOW_UNAUTHENTICATED", "1")
    r = _client().post("/api/events/hook/x", json={})
    assert r.status_code != 401  # past the gate (it fails later — nothing is listening)


# ---- /invoke and the poll seams ----------------------------------------
def test_invoke_refuses_when_no_gateway_token_is_configured(closed_gates):
    """`if token and …` was a no-op with an empty token, so /invoke ran agents on a
    caller-supplied scope for anyone who could reach the port."""
    closed_gates.delenv("GATEWAY_TOKEN", raising=False)
    r = _client(gateway_token="").post("/invoke", json=_ENVELOPE)
    assert r.status_code == 401, "/invoke is open with no token configured"


def test_invoke_still_refuses_a_wrong_token(closed_gates):
    r = _client(gateway_token="s3cret").post("/invoke", json=_ENVELOPE, headers={"X-Gateway-Token": "nope"})
    assert r.status_code == 401


def test_invoke_accepts_the_right_token(closed_gates):
    """The gate must not have become unconditional — a correct token still passes."""
    r = _client(gateway_token="s3cret").post("/invoke", json=_ENVELOPE, headers={"X-Gateway-Token": "s3cret"})
    assert r.status_code != 401


def test_invoke_opens_only_when_asked(closed_gates):
    closed_gates.setenv("EVENTS_ALLOW_UNAUTHENTICATED", "1")
    assert _client(gateway_token="").post("/invoke", json=_ENVELOPE).status_code != 401


# ---- Slack signature ----------------------------------------------------
def test_slack_refuses_unverified_events_without_a_signing_secret(closed_gates):
    """verify_signature returned `True, "unverified"` — allow-and-flag — so a missing signing
    secret meant the endpoint accepted forged Slack events from anyone who could reach it."""
    from events import slack_direct

    closed_gates.delenv("SLACK_SIGNING_SECRET", raising=False)
    ok, why = slack_direct.verify_signature({}, "{}")
    assert ok is False, "unsigned Slack events are still accepted"
    assert "SLACK_SIGNING_SECRET" in why


def test_slack_opens_only_when_asked(closed_gates):
    from events import slack_direct

    closed_gates.delenv("SLACK_SIGNING_SECRET", raising=False)
    closed_gates.setenv("EVENTS_ALLOW_UNAUTHENTICATED", "1")
    ok, why = slack_direct.verify_signature({}, "{}")
    assert ok is True and "EVENTS_ALLOW_UNAUTHENTICATED" in why


def test_a_configured_signing_secret_still_verifies_properly(closed_gates):
    """The opt-out must not have replaced the real check."""
    import hashlib
    import hmac
    import time

    from events import slack_direct

    closed_gates.setenv("SLACK_SIGNING_SECRET", "shh")
    body, ts = '{"ok":1}', str(int(time.time()))
    good = "v0=" + hmac.new(b"shh", f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()

    ok, _ = slack_direct.verify_signature({"x-slack-request-timestamp": ts, "x-slack-signature": good}, body)
    assert ok is True
    bad, _ = slack_direct.verify_signature(
        {"x-slack-request-timestamp": ts, "x-slack-signature": "v0=deadbeef"}, body
    )
    assert bad is False
