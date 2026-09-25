"""WhatsApp voice notes — the channel's half of a turn pipeline (``WhatsAppIO``).

The speech itself is tested in test_turns.py with fake providers. Here: Meta's side — a voice note
arrives as a MEDIA ID (not bytes), the download needs a second authenticated hop, the bearer token
only ever goes to Meta's hosts, and a reply is upload-then-send in a format a phone will play.
"""

import hashlib
import hmac
import json

import httpx
import pytest

from cuga.backend.events import whatsapp_direct as wa
from cuga.backend.events.turns import AUDIO, Part

SECRET = "test_app_secret"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    wa._LAST_INBOUND.clear()
    monkeypatch.setenv("WHATSAPP_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PNID")
    monkeypatch.delenv("WHATSAPP_GRAPH_BASE", raising=False)
    yield
    wa._LAST_INBOUND.clear()


def _voice_payload(media_id="MEDIA1", wa_id="15551234567", mime="audio/ogg; codecs=opus"):
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": wa_id, "profile": {"name": "Ravi"}}],
                            "messages": [
                                {
                                    "from": wa_id,
                                    "id": "wamid.V",
                                    "timestamp": "2",
                                    "type": "audio",
                                    "audio": {"id": media_id, "mime_type": mime, "voice": True},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


def _mock_httpx(monkeypatch, handler):
    real = httpx.AsyncClient

    class C(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)

    monkeypatch.setattr(httpx, "AsyncClient", C)


# ── inbound ─────────────────────────────────────────────────────────────────────────────────────
def test_a_voice_note_is_parsed_as_an_audio_part_carrying_the_media_id():
    [m] = wa.inbound(_voice_payload())
    assert m.channel == "whatsapp" and m.sender == "15551234567" and m.sender_name == "Ravi"
    assert m.modality == AUDIO
    p = m.parts[0]
    assert p.ref == "MEDIA1" and p.data == b"" and p.mime.startswith("audio/ogg") and p.meta["voice"] is True


def test_the_text_only_view_still_ignores_voice_notes():
    """``messages()`` predates audio; its callers must keep seeing text only."""
    assert wa.messages(_voice_payload()) == []


def test_an_audio_message_without_a_media_id_is_dropped():
    body = _voice_payload()
    body["entry"][0]["changes"][0]["value"]["messages"][0]["audio"] = {}
    assert wa.inbound(body) == []


# ── fetch ───────────────────────────────────────────────────────────────────────────────────────
async def test_fetch_is_two_authenticated_hops(monkeypatch):
    calls = []

    def handler(req):
        calls.append((str(req.url), req.headers.get("authorization")))
        if req.url.host == "graph.facebook.com":
            return httpx.Response(
                200,
                json={
                    "url": "https://lookaside.fbsbx.com/whatsapp/x",
                    "mime_type": "audio/ogg",
                    "file_size": 9,
                },
            )
        return httpx.Response(200, content=b"OggSvoice")

    _mock_httpx(monkeypatch, handler)
    data, mime = await wa.fetch_media("MEDIA1")
    assert data == b"OggSvoice" and mime == "audio/ogg"
    assert calls[0][0].endswith("/MEDIA1") and all(c[1] == "Bearer tok" for c in calls)


async def test_the_token_is_never_sent_to_a_non_meta_host(monkeypatch):
    sent_to = []

    def handler(req):
        sent_to.append(req.url.host)
        return httpx.Response(200, json={"url": "https://evil.example.com/steal", "file_size": 3})

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="unexpected media host"):
        await wa.fetch_media("MEDIA1")
    assert sent_to == ["graph.facebook.com"]


async def test_oversized_media_is_refused_before_download(monkeypatch):
    _mock_httpx(
        monkeypatch,
        lambda req: httpx.Response(200, json={"url": "https://x.fbsbx.com/a", "file_size": 17 * 1024 * 1024}),
    )
    with pytest.raises(RuntimeError, match="16 MB"):
        await wa.fetch_media("MEDIA1")


def test_graph_base_override_is_the_only_other_trusted_host(monkeypatch):
    monkeypatch.setenv("WHATSAPP_GRAPH_BASE", "http://127.0.0.1:8399")
    assert wa._graph("PNID/media") == "http://127.0.0.1:8399/v23.0/PNID/media"
    assert wa._media_host_ok("http://127.0.0.1:8399/media/x")
    assert not wa._media_host_ok("http://evil.example.com/x")


# ── send ────────────────────────────────────────────────────────────────────────────────────────
async def test_voice_reply_is_upload_then_send_audio(monkeypatch):
    seen = []

    def handler(req):
        seen.append((req.url.path, req.content))
        if req.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "UP1"})
        return httpx.Response(200, json={"messages": [{"id": "wamid.R"}]})

    _mock_httpx(monkeypatch, handler)
    wa.note_inbound("15551234567")
    [res] = await wa.WhatsAppIO().send(
        "15551234567", [Part.of_audio(data=b"OggS", mime="audio/ogg; codecs=opus")]
    )
    assert res["ok"] and res["mode"] == "audio"
    assert seen[0][0].endswith("/PNID/media") and b'name="messaging_product"' in seen[0][1]
    body = json.loads(seen[1][1])
    assert body["type"] == "audio" and body["audio"] == {"id": "UP1"} and body["to"] == "15551234567"


async def test_text_parts_still_go_through_the_window_aware_sender(monkeypatch):
    sent = []

    async def fake_send(to, text):
        sent.append((to, text))
        return {"ok": True}

    monkeypatch.setattr(wa, "send_message", fake_send)
    await wa.WhatsAppIO().send("1555", [Part.of_text("hello")])
    assert sent == [("1555", "hello")]


async def test_voice_reply_outside_the_window_is_skipped_not_templated(monkeypatch):
    """A template cannot carry audio. Outside the window the TEXT part (sent as a template) is the
    reply; the voice note is skipped rather than failing the whole send."""
    [res] = await wa.WhatsAppIO().send("15550000000", [Part.of_audio(data=b"OggS", mime="audio/ogg")])
    assert not res["ok"] and res["mode"] == "skipped"


async def test_audio_whatsapp_cannot_play_is_skipped_with_a_reason(monkeypatch):
    """The speech service is TOLD what this channel accepts. If it sends something else (WAV, say),
    the voice note is dropped — the text reply has already gone, so this costs nothing else. We do
    not transcode here: formats are the service's problem, not the channel's."""
    posted = []
    _mock_httpx(
        monkeypatch, lambda req: (posted.append(req.url.path), httpx.Response(200, json={"id": "UP9"}))[1]
    )
    wa.note_inbound("1555")
    [res] = await wa.WhatsAppIO().send("1555", [Part.of_audio(data=b"RIFFwav", mime="audio/wav")])
    assert not res["ok"] and res["mode"] == "skipped" and "cannot play" in res["error"]
    assert posted == [], "nothing was uploaded"


# ── the route ───────────────────────────────────────────────────────────────────────────────────
def _client():
    """The same app wiring as test_whatsapp_direct's route tests."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cuga.backend.events.agent_store import AgentStore
    from cuga.backend.events.app import register_events_routes
    from cuga.backend.events.concierge import Concierge
    from cuga.backend.events.runtime import AgentSpec, AgentStoreRuntime
    from cuga.backend.events.subscriptions import SubscriptionStore

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


def test_a_signed_voice_note_is_accepted_and_opens_the_window(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    handled = []

    async def fake_handle(msg, *, channel, ask):
        handled.append((msg.modality, msg.parts[0].ref, type(channel).__name__))

    from cuga.backend.events import turns

    monkeypatch.setattr(turns, "handle", fake_handle)
    raw = json.dumps(_voice_payload(), separators=(",", ":")).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    r = _client().post(
        "/api/events/whatsapp/events",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
    )
    assert r.status_code == 200 and r.json()["messages"] == 1
    assert wa.window_open("15551234567"), "a voice note opens the 24h window like text does"
