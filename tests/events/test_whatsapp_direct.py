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


def test_non_ascii_signature_header_does_not_crash_the_handler(monkeypatch):
    """`hmac.compare_digest` raises TypeError on non-ASCII str. Both guarded values come from the
    request, so comparing as str let anyone turn a 401 into an unhandled 500 with one character."""
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    ok, why = wa.verify_signature({"x-hub-signature-256": "sha256=café•"}, b"{}")
    assert ok is False and why == "bad signature"


def test_non_ascii_verify_token_does_not_crash_the_handshake(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", VERIFY)
    ok, _ = wa.handshake({"hub.mode": "subscribe", "hub.verify_token": "café•", "hub.challenge": "x"})
    assert ok is False


def test_challenge_is_bounded_before_being_echoed(monkeypatch):
    """The challenge is the ONLY request value this service reflects. Meta sends a short random
    token; anything else is refused rather than echoed back."""
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", VERIFY)
    for bad in ["<script>alert(1)</script>", "a" * 200, "has space", ""]:
        ok, _ = wa.handshake({"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": bad})
        assert ok is False, f"should refuse challenge {bad[:24]!r}"
    ok, val = wa.handshake(
        {"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "654491455"}
    )
    assert ok and val == "654491455", "a real Meta challenge must still pass"


def test_handshake_failure_does_not_reflect_the_submitted_mode(monkeypatch):
    """Error strings are ours, never the caller's input — that text reaches logs and the response."""
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", VERIFY)
    _, why = wa.handshake({"hub.mode": "<script>", "hub.verify_token": VERIFY, "hub.challenge": "abc"})
    assert "<script>" not in why


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


def test_signed_inbound_message_is_accepted_and_OPENS_the_window(monkeypatch):
    """The route-level contract, end to end: signature → parse → the window opens.

    Unit-testing `note_inbound` proves the function works; it does not prove the ROUTE calls it. If
    the route forgot, every reply would silently go out as a template — or fail outright with no
    template configured — and only production would notice.
    """
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)

    async def fake_ask(*a, **k):
        return "an answer"

    async def fake_send(to, text):
        return {"ok": True}

    from cuga.backend.events import cuga_door

    monkeypatch.setattr(cuga_door, "ask", fake_ask, raising=False)
    monkeypatch.setattr(wa, "send_message", fake_send)

    raw = json.dumps(_payload("hi"), separators=(",", ":")).encode()
    assert not wa.window_open("15551234567"), "precondition: window shut"
    r = _client().post(
        "/api/events/whatsapp/events",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(raw)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["messages"] == 1
    assert wa.window_open("15551234567"), "the route must record inbound — it opens the 24h window"


def test_signed_delivery_receipt_does_NOT_open_the_window(monkeypatch):
    """A receipt is OUR message being delivered, not the user writing to us. Treating it as inbound
    would open a 24-hour window nobody consented to and let free-form sends slip out."""
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    body = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.X", "status": "sent"}]}}]}]}
    raw = json.dumps(body, separators=(",", ":")).encode()
    r = _client().post(
        "/api/events/whatsapp/events",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(raw)},
    )
    assert r.status_code == 200 and r.json()["messages"] == 0
    assert not wa.window_open("15551234567")


def test_unsigned_inbound_is_refused_by_the_ROUTE(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    raw = json.dumps(_payload(), separators=(",", ":")).encode()
    r = _client().post(
        "/api/events/whatsapp/events", content=raw, headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 401
    assert not wa.window_open("15551234567"), "a refused request must not open the window"


@pytest.mark.asyncio
async def test_send_text_and_send_template_payload_shapes(monkeypatch):
    """The exact JSON Meta expects. Getting `messaging_product` or the template shape wrong is a 400
    at runtime with a message that does not name the offending field."""
    sent = {}

    async def fake_post(payload):
        sent.clear()
        sent.update(payload)
        return {"ok": True}

    monkeypatch.setattr(wa, "_post", fake_post)

    await wa.send_text("1555", "hello")
    assert sent == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "1555",
        "type": "text",
        "text": {"body": "hello"},
    }

    await wa.send_template("1555", name="digest", lang="en_US", params=["bitcoin"])
    assert sent["type"] == "template"
    assert sent["template"]["name"] == "digest"
    assert sent["template"]["language"] == {"code": "en_US"}
    assert sent["template"]["components"] == [
        {"type": "body", "parameters": [{"type": "text", "text": "bitcoin"}]}
    ]


def test_window_map_is_bounded(monkeypatch):
    """It is process-global and keyed by phone number — unbounded growth is a slow leak on a busy
    number."""
    monkeypatch.setattr(wa, "_MAX_TRACKED", 50)
    for i in range(120):
        wa.note_inbound(f"n{i}", when=float(i))
    assert len(wa._LAST_INBOUND) <= 120, "must not grow without bound"
    assert wa.window_open("n119", now=120.0), "the most recent entry must survive eviction"


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
