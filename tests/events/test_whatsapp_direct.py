"""WhatsApp channel — signature, handshake, payload parsing, and the 24-hour window.

The window tests carry the weight here. That branch is the one a prototype can never reach on its
own: while developing you message the bot constantly, so the window is always open and the template
path is dead code that looks alive. These force it.
"""

import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.events import whatsapp_direct as wa
from cuga.backend.events.agent_store import AgentStore
from cuga.backend.events.app import register_events_routes
from cuga.backend.events.concierge import Concierge
from cuga.backend.events.runtime import AgentSpec, AgentStoreRuntime
from cuga.backend.events.subscriptions import SubscriptionStore

SECRET = "test_app_secret"
VERIFY = "test_verify_token"


@pytest.fixture(autouse=True)
def _clean_window():
    """The window map is process-global; a leftover entry from one test silently opens the window
    for the next and turns a template assertion green for the wrong reason."""
    wa._LAST_INBOUND.clear()
    yield
    wa._LAST_INBOUND.clear()


def _client():
    rt = AgentStoreRuntime(agent_store=AgentStore(":memory:"))
    rt.upsert_agent(AgentSpec(name="cuga", prompt="c", integrations=[]), scope="default/default")
    store = SubscriptionStore(":memory:")
    app = FastAPI()
    register_events_routes(
        app,
        runtime=rt,
        store=store,
        concierge=Concierge(rt, store=store, engine=None),
        engine=None,
        gateway_token="",
    )
    return TestClient(app)


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _payload(text="hello", wa_id="15551234567"):
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": wa_id, "profile": {"name": "Anu"}}],
                            "messages": [
                                {
                                    "from": wa_id,
                                    "id": "wamid.X",
                                    "timestamp": "1",
                                    "type": "text",
                                    "text": {"body": text},
                                },
                            ],
                        }
                    }
                ]
            }
        ]
    }


# ── signature ───────────────────────────────────────────────────────────────────────────────────
def test_signature_is_over_the_RAW_body_not_a_reserialised_dict(monkeypatch):
    """Meta signs the exact bytes it sent. Re-serialising the parsed JSON reorders keys and changes
    whitespace, so the digest would never match — the endpoint must hash the raw body."""
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    raw = json.dumps(_payload(), separators=(",", ":")).encode()
    assert wa.verify_signature({"x-hub-signature-256": _sign(raw)}, raw)[0]
    # same object, different serialisation → must NOT verify
    resurfaced = json.dumps(json.loads(raw), indent=2).encode()
    assert not wa.verify_signature({"x-hub-signature-256": _sign(raw)}, resurfaced)[0]


def test_missing_or_wrong_signature_is_rejected(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    raw = b'{"a":1}'
    assert not wa.verify_signature({}, raw)[0]
    assert not wa.verify_signature({"x-hub-signature-256": "sha256=deadbeef"}, raw)[0]


def test_no_app_secret_allows_but_flags(monkeypatch):
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    ok, why = wa.verify_signature({}, b"{}")
    assert ok and "not set" in why


# ── the GET handshake (Meta verifies over GET; Slack uses POST) ─────────────────────────────────
def test_handshake_echoes_the_challenge_on_a_matching_token(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", VERIFY)
    ok, val = wa.handshake({"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "12345"})
    assert ok and val == "12345"


def test_handshake_refuses_a_wrong_token(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", VERIFY)
    assert not wa.handshake({"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "x"})[0]
    assert not wa.handshake({"hub.mode": "unsubscribe", "hub.verify_token": VERIFY, "hub.challenge": "x"})[0]


def test_verification_route_returns_PLAIN_TEXT_not_json(monkeypatch):
    """Meta compares the body byte-for-byte to the challenge. A JSON-quoted body fails
    verification, which presents as 'the callback URL could not be validated'."""
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", VERIFY)
    r = _client().get(
        "/api/events/whatsapp/events",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "abc123"},
    )
    assert r.status_code == 200
    assert r.text == "abc123"  # not '"abc123"'


def test_verification_route_refuses_a_bad_token(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", VERIFY)
    r = _client().get(
        "/api/events/whatsapp/events",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "abc"},
    )
    assert r.status_code == 403


# ── payload parsing ─────────────────────────────────────────────────────────────────────────────
def test_messages_are_extracted_from_the_nested_payload():
    got = wa.messages(_payload("look at my gmail"))
    assert len(got) == 1
    assert got[0]["wa_id"] == "15551234567"
    assert got[0]["text"] == "look at my gmail"
    assert got[0]["name"] == "Anu"


def test_delivery_STATUSES_are_not_treated_as_messages():
    """`statuses[]` are receipts for messages WE sent. Reading one as inbound makes the bot answer
    its own delivery receipt — and, worse, opens the 24h window on a message the user never sent."""
    body = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.X", "status": "delivered"}]}}]}]}
    assert wa.messages(body) == []


def test_non_text_messages_are_ignored():
    body = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [{"from": "1555", "id": "i", "type": "image", "image": {"id": "x"}}]
                        }
                    }
                ]
            }
        ]
    }
    assert wa.messages(body) == []


def test_empty_payload_is_safe():
    assert wa.messages({}) == []
    assert wa.messages({"entry": [{"changes": [{"value": {}}]}]}) == []


# ── the 24-hour window ──────────────────────────────────────────────────────────────────────────
def test_window_opens_on_inbound_and_expires_after_24h():
    wa.note_inbound("1555", when=1000.0)
    assert wa.window_open("1555", now=1000.0 + 3600)  # 1h later
    assert wa.window_open("1555", now=1000.0 + wa.WINDOW_SECS - 1)
    assert not wa.window_open("1555", now=1000.0 + wa.WINDOW_SECS + 1)


def test_an_unknown_number_is_treated_as_CLOSED():
    """Never heard from them → no window. Assuming open would send free-form and be rejected by
    Meta, and the user would hear nothing at all."""
    assert not wa.window_open("unknown-number")


def test_force_template_flag_closes_the_window(monkeypatch):
    """The only way to exercise the out-of-window path while developing: a test phone keeps the
    window permanently open, so this branch is otherwise unreachable until production."""
    wa.note_inbound("1555")
    assert wa.window_open("1555")
    monkeypatch.setenv("WHATSAPP_FORCE_TEMPLATE", "1")
    assert not wa.window_open("1555")


@pytest.mark.asyncio
async def test_send_message_picks_template_outside_the_window(monkeypatch):
    """The whole point of the adapter: callers ask to send, and the window decides how."""
    sent = {}

    async def fake_post(payload):
        sent.clear()
        sent.update(payload)
        return {"ok": True, "response": {"messages": [{"id": "x"}]}}

    monkeypatch.setattr(wa, "_post", fake_post)
    monkeypatch.setenv("WHATSAPP_TEMPLATE_NAME", "digest_ready")

    wa.note_inbound("1555")  # inside the window → free-form
    await wa.send_message("1555", "the full agent answer")
    assert sent["type"] == "text" and sent["text"]["body"] == "the full agent answer"

    wa._LAST_INBOUND.clear()  # outside → template
    res = await wa.send_message("1555", "the full agent answer\nsecond line")
    assert sent["type"] == "template"
    assert sent["template"]["name"] == "digest_ready"
    assert res.get("mode") == "template"


@pytest.mark.asyncio
async def test_outside_the_window_with_no_template_configured_fails_LOUDLY(monkeypatch):
    """A silent failure here is the worst case: the flow fires, the agent runs, and the user hears
    nothing. Report why instead."""
    monkeypatch.delenv("WHATSAPP_TEMPLATE_NAME", raising=False)
    wa._LAST_INBOUND.clear()
    res = await wa.send_message("1555", "hello")
    assert not res["ok"] and "template" in res["error"].lower()


# ── delivery wiring ─────────────────────────────────────────────────────────────────────────────
def test_whatsapp_is_registered_as_a_direct_channel():
    from cuga.backend.events import delivery

    assert delivery.is_direct("whatsapp")


@pytest.mark.asyncio
async def test_send_direct_routes_whatsapp_to_the_adapter(monkeypatch):
    from cuga.backend.events import delivery

    seen = {}

    async def fake_send(to, text):
        seen.update({"to": to, "text": text})
        return {"ok": True}

    monkeypatch.setattr(wa, "send_message", fake_send)
    ok, why = await delivery.send_direct("whatsapp", "15551234567", "fired")
    assert ok and why == "ok"
    assert seen == {"to": "15551234567", "text": "fired"}


def test_whatsapp_appears_in_the_channel_registry():
    from cuga.backend.events.connectors import CHANNELS

    row = next((c for c in CHANNELS if c["name"] == "whatsapp"), None)
    assert row and row["backend"] == "direct" and row["env"] == "WHATSAPP_TOKEN"
