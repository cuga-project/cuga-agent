"""Direct WhatsApp integration (Meta Cloud API) — no Activepieces.

AP's WhatsApp piece is SEND-ONLY (0 triggers, 3 actions), so it cannot back a channel: a channel
needs an inbound half. Telegram works over AP because ``piece-telegram-bot`` has a real webhook
trigger; WhatsApp has none. Since the inbound webhook has to be built here regardless — signature
check, verification handshake, payload parse — outbound is one more REST call, and routing that
through AP would add a hop and a dependency for the easy half. AP's real value (OAuth refresh,
per-user custody) does not apply either: a channel token is a single long-lived bot secret.

Flow:  Meta ▸ POST /api/events/whatsapp/events (this module) ▸ /run (cuga_door) ▸ Cloud API send.

VOICE NOTES go through the same door, wrapped in a turn pipeline (``turns/``): :func:`inbound` parses
text AND audio into channel-neutral ``InboundMessage``s, :class:`WhatsAppIO` is this channel's side of
the pipeline (fetch a media id's bytes; upload + send a voice reply), and ``EVENTS_TURN_PIPELINES``
decides what runs before and after CUGA (speech-to-text, text-to-speech, …). This module knows how
WhatsApp moves audio; it does not know how speech is recognised or produced.

THE 24-HOUR WINDOW is what makes WhatsApp unlike every other channel. Free-form text is only
permitted within 24h of the user's last inbound message; outside it Meta REJECTS the send and a
pre-approved template is required. So this module tracks ``last_inbound_at`` per wa_id and
:func:`send_message` picks the mode. Nothing else in the events layer has to know.

That branch is also the one a prototype can never reach: while developing you message the bot
constantly, so the window is always open and the template path is dead code that looks alive. Set
``WHATSAPP_FORCE_TEMPLATE=1`` to force it, and test both.

Setup (developers.facebook.com → your app → WhatsApp):
  • Webhook callback URL = <EVENTS_PUBLIC_URL>/api/events/whatsapp/events, verify token =
    WHATSAPP_VERIFY_TOKEN, subscribe the ``messages`` field.
  • Use a SYSTEM USER token, never the 24-hour dev token (it expires mid-test and reads as a bug).
Env: WHATSAPP_TOKEN · WHATSAPP_PHONE_NUMBER_ID (required) ·
     WHATSAPP_APP_SECRET (verifies X-Hub-Signature-256) · WHATSAPP_VERIFY_TOKEN (handshake) ·
     WHATSAPP_TEMPLATE_NAME / WHATSAPP_TEMPLATE_LANG (out-of-window send) · WHATSAPP_API_VERSION.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time

import httpx

try:
    from .turns.message import AUDIO, TEXT, InboundMessage, Part
except ImportError:  # flat load (tests put the events dir on sys.path)
    from turns.message import AUDIO, TEXT, InboundMessage, Part

log = logging.getLogger("events.whatsapp")

WINDOW_SECS = 24 * 3600  # Meta's customer service window


def _secret_of(key: str) -> str:
    try:
        from .secret_seam import secret as _secret
    except ImportError:  # flat load (tests put the events dir on sys.path)
        from secret_seam import secret as _secret
    return _secret(key)


def token() -> str:
    return _secret_of("WHATSAPP_TOKEN")


def phone_number_id() -> str:
    return _secret_of("WHATSAPP_PHONE_NUMBER_ID")


def app_secret() -> str:
    return _secret_of("WHATSAPP_APP_SECRET")


def verify_token() -> str:
    return _secret_of("WHATSAPP_VERIFY_TOKEN")


def api_version() -> str:
    return (os.environ.get("WHATSAPP_API_VERSION", "v23.0").split(" #", 1)[0].strip()) or "v23.0"


def graph_base() -> str:
    """``WHATSAPP_GRAPH_BASE`` overrides Meta's host — for a local fake Graph API in end-to-end tests,
    or an egress proxy. Unset in production."""
    return (
        os.environ.get("WHATSAPP_GRAPH_BASE", "").split(" #", 1)[0].strip().rstrip("/")
    ) or "https://graph.facebook.com"


def _graph(path: str) -> str:
    return f"{graph_base()}/{api_version()}/{path}"


# ── inbound ─────────────────────────────────────────────────────────────────────────────────────
def _safe_bytes(s) -> bytes:
    """UTF-8 bytes for a constant-time compare.

    ``hmac.compare_digest`` raises TypeError on a ``str`` containing non-ASCII, and both values it
    guards here (the signature header, hub.verify_token) come from the request. Comparing bytes keeps
    an attacker from turning a 401 into an unhandled 500 with one accented character.
    """
    if isinstance(s, bytes):
        return s
    return str(s or "").encode("utf-8", "replace")


# Meta's hub.challenge is a short random token that we must echo back VERBATIM. It is also the only
# request value this service ever reflects, so bound it: anything outside this alphabet, or absurdly
# long, is not a challenge Meta sent and there is no reason to echo it.
_CHALLENGE_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
_CHALLENGE_MAX = 128


def _safe_challenge(value: str) -> str:
    v = str(value or "")
    if not v or len(v) > _CHALLENGE_MAX or any(c not in _CHALLENGE_OK for c in v):
        return ""
    return v


def verify_signature(headers, raw_body: bytes | str) -> tuple[bool, str]:
    """Verify Meta's ``X-Hub-Signature-256`` (HMAC-SHA256 of the RAW body with the app secret).

    Returns (ok, reason). With no app secret configured we allow but flag it, matching
    slack_direct.verify_signature — set WHATSAPP_APP_SECRET to lock it down.

    The HMAC is over the bytes Meta sent, so callers must pass the raw body, not a re-serialised
    dict: ``json.dumps`` of a parsed payload reorders keys and changes whitespace, and the digest
    would never match.
    """
    secret = app_secret()
    if not secret:
        return True, "unverified (WHATSAPP_APP_SECRET not set)"
    sig = headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256") or ""
    if not sig:
        return False, "missing signature header"
    body = raw_body.encode() if isinstance(raw_body, str) else (raw_body or b"")
    mine = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # Compare as BYTES. `hmac.compare_digest` raises TypeError on str containing non-ASCII, and this
    # value is an attacker-supplied header — a single non-ASCII byte would turn a 401 into a 500.
    ok = hmac.compare_digest(mine.encode(), _safe_bytes(sig))
    return ok, ("ok" if ok else "bad signature")


def handshake(params) -> tuple[bool, str]:
    """Meta's webhook verification: a GET carrying hub.mode/hub.verify_token/hub.challenge.

    Echo the challenge VERBATIM when the token matches, else refuse. Unlike Slack (which does this
    over POST with a JSON body), Meta uses query params on a GET — the endpoint therefore needs a
    real GET handler, not a friendly "you opened this in a browser" probe.
    """
    mode = params.get("hub.mode") or ""
    tok = params.get("hub.verify_token") or ""
    challenge = _safe_challenge(params.get("hub.challenge") or "")
    want = verify_token()
    if not challenge:
        return False, "missing or malformed hub.challenge"
    if not want:
        return False, "WHATSAPP_VERIFY_TOKEN is not set"
    if mode != "subscribe":
        return False, "unexpected hub.mode"
    # bytes, for the same reason as verify_signature — hub.verify_token is attacker-supplied
    if not hmac.compare_digest(_safe_bytes(tok), _safe_bytes(want)):
        return False, "verify token mismatch"
    return True, challenge


def inbound(body: dict) -> list[InboundMessage]:
    """The inbound messages in a webhook payload, as channel-neutral ``InboundMessage``s.

    Meta nests these three deep (``entry[].changes[].value.messages[]``) and interleaves them with
    ``statuses[]`` (delivery receipts for messages WE sent). Only ``messages`` are human traffic;
    treating a status as a message makes the bot answer its own delivery receipt.

    Text and AUDIO are understood. A voice note arrives as a media id, not bytes — the bytes are
    fetched only if a pipeline step asks (:meth:`WhatsAppIO.fetch`), so a deployment with no voice
    pipeline never downloads audio it will not use. Images, documents, reactions and interactive
    replies are still skipped here; they are one ``elif`` away (the Part model already has kinds).
    """
    out: list[InboundMessage] = []
    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {
                c.get("wa_id"): ((c.get("profile") or {}).get("name") or "")
                for c in (value.get("contacts") or [])
                if isinstance(c, dict)
            }
            for m in value.get("messages") or []:
                if not isinstance(m, dict):
                    continue
                wa_id = str(m.get("from") or "")
                if not wa_id:
                    continue
                kind = m.get("type")
                if kind == "text":
                    text = str(((m.get("text") or {}).get("body")) or "")
                    if not text:
                        continue
                    parts = [Part.of_text(text)]
                elif kind == "audio":
                    a = m.get("audio") or {}
                    if not a.get("id"):
                        continue
                    parts = [
                        Part.of_audio(
                            ref=str(a["id"]), mime=str(a.get("mime_type") or ""), voice=bool(a.get("voice"))
                        )
                    ]
                else:
                    continue  # image/document/interactive/reactions — not handled yet
                out.append(
                    InboundMessage(
                        channel="whatsapp",
                        sender=wa_id,
                        parts=parts,
                        message_id=str(m.get("id") or ""),
                        sender_name=names.get(wa_id, ""),
                        meta={"ts": str(m.get("timestamp") or "")},
                    )
                )
    return out


def messages(body: dict) -> list[dict]:
    """The TEXT messages in a webhook payload → ``[{wa_id, text, id, ts, name}, …]`` — the original,
    text-only view, kept for callers that predate :func:`inbound`."""
    return [
        {
            "wa_id": m.sender,
            "text": m.text(),
            "id": m.message_id,
            "ts": m.meta.get("ts", ""),
            "name": m.sender_name,
        }
        for m in inbound(body)
        if m.modality == TEXT
    ]


# ── the 24-hour window ──────────────────────────────────────────────────────────────────────────
# wa_id → epoch seconds of that user's last INBOUND message. In-process: the window is a delivery
# optimisation, and being wrong costs one templated message instead of a free-form one — never a
# lost fire, because the send falls back to the template. Deliberately NOT in the events DB: it
# would be a write on every inbound message for a value that self-heals on the next one.
_LAST_INBOUND: dict[str, float] = {}
_MAX_TRACKED = 20000


def note_inbound(wa_id: str, when: float | None = None) -> None:
    """Record that ``wa_id`` messaged us — this is what opens the 24-hour window."""
    if not wa_id:
        return
    if len(_LAST_INBOUND) > _MAX_TRACKED:  # bounded: drop the stalest half
        for k in sorted(_LAST_INBOUND, key=_LAST_INBOUND.get)[: _MAX_TRACKED // 2]:
            _LAST_INBOUND.pop(k, None)
    _LAST_INBOUND[wa_id] = when if when is not None else time.time()


def window_open(wa_id: str, now: float | None = None) -> bool:
    """Is free-form text still permitted to ``wa_id``?

    ``WHATSAPP_FORCE_TEMPLATE=1`` answers False regardless — the only way to exercise the
    out-of-window path while developing, since a test phone keeps the window permanently open.
    """
    if os.environ.get("WHATSAPP_FORCE_TEMPLATE", "").strip() in ("1", "true", "yes"):
        return False
    last = _LAST_INBOUND.get(wa_id or "")
    if not last:
        return False  # never heard from them → assume closed, send the template
    return ((now if now is not None else time.time()) - last) < WINDOW_SECS


def template_name() -> str:
    return (os.environ.get("WHATSAPP_TEMPLATE_NAME", "").split(" #", 1)[0].strip()) or ""


def template_lang() -> str:
    return (os.environ.get("WHATSAPP_TEMPLATE_LANG", "en_US").split(" #", 1)[0].strip()) or "en_US"


# ── outbound ────────────────────────────────────────────────────────────────────────────────────
async def _post(payload: dict) -> dict:
    tok, pnid = token(), phone_number_id()
    if not tok:
        return {"ok": False, "error": "no WHATSAPP_TOKEN"}
    if not pnid:
        return {"ok": False, "error": "no WHATSAPP_PHONE_NUMBER_ID"}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                _graph(f"{pnid}/messages"),
                headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                json=payload,
            )
        body = r.json() if r.content else {}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"request failed: {type(e).__name__}"}
    if r.status_code == 200 and (body.get("messages") or body.get("contacts")):
        return {"ok": True, "response": body}
    err = (body.get("error") or {}) if isinstance(body, dict) else {}
    return {
        "ok": False,
        "error": str(err.get("message") or f"HTTP {r.status_code}"),
        "code": err.get("code"),
    }


async def send_text(to: str, text: str) -> dict:
    """Free-form text. Only valid INSIDE the 24-hour window — Meta rejects it outside."""
    return await _post(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }
    )


async def send_template(to: str, name: str = "", lang: str = "", params: list[str] | None = None) -> dict:
    """A pre-approved template — the ONLY thing sendable outside the window.

    A template is registered text with numbered slots, so an agent's free-form answer cannot ride
    it. Pass short values; the usual pattern is a nudge ("your digest is ready") that prompts a
    reply, which reopens the window for the full answer.
    """
    name = name or template_name()
    if not name:
        return {"ok": False, "error": "outside the 24h window and no WHATSAPP_TEMPLATE_NAME set"}
    payload: dict = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": {"name": name, "language": {"code": lang or template_lang()}},
    }
    if params:
        payload["template"]["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in params]}
        ]
    return await _post(payload)


async def send_message(to: str, text: str) -> dict:
    """Send to ``to``, choosing free-form or template by the 24-hour window.

    Callers (delivery.send_direct) stay ignorant of the window — they ask for a message to be sent
    and this decides how. Outside the window the agent's text is truncated into the template's first
    parameter, because a template body cannot carry arbitrary length.
    """
    if window_open(to):
        return await send_text(to, text)
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else "Update"
    res = await send_template(to, params=[first[:120]])
    if res.get("ok"):
        res["mode"] = "template"
    return res


# ── media (voice notes) ─────────────────────────────────────────────────────────────────────────
# What a phone will play as a WhatsApp voice message, best first. WAV is NOT on Meta's list — which
# is why a TTS that emits WAV gets transcoded to Opus before upload. OGG must be Opus, mono.
ACCEPTED_AUDIO = ("audio/ogg; codecs=opus", "audio/mpeg", "audio/aac", "audio/mp4", "audio/amr")
MAX_MEDIA_BYTES = 16 * 1024 * 1024  # Meta's audio limit; also bounds what we hold in memory
_MEDIA_HOSTS = (".fbsbx.com", ".facebook.com", ".whatsapp.net")


def _media_host_ok(url: str) -> bool:
    """Only hand the bearer token to Meta's own media hosts (or the configured fake/proxy base). The
    URL comes from Graph's response, but a token is worth a second check before it leaves."""
    from urllib.parse import urlparse

    u = urlparse(url or "")
    if u.scheme not in ("https", "http") or not u.hostname:
        return False
    if u.hostname == urlparse(graph_base()).hostname:
        return True
    return u.scheme == "https" and any(u.hostname.endswith(h) or u.hostname == h[1:] for h in _MEDIA_HOSTS)


async def fetch_media(media_id: str) -> tuple[bytes, str]:
    """A media id → (bytes, mime). Two hops: Graph returns a short-lived URL (it expires in ~5 min),
    and that URL needs the same bearer token."""
    tok = token()
    if not tok:
        raise RuntimeError("no WHATSAPP_TOKEN")
    auth = {"Authorization": f"Bearer {tok}"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(_graph(media_id), headers=auth)
        meta = r.json() if r.content else {}
        if r.status_code != 200 or not meta.get("url"):
            raise RuntimeError(f"whatsapp media {media_id}: HTTP {r.status_code}")
        if int(meta.get("file_size") or 0) > MAX_MEDIA_BYTES:
            raise RuntimeError(
                f"whatsapp media {media_id}: {meta.get('file_size')} bytes exceeds the 16 MB limit"
            )
        if not _media_host_ok(meta["url"]):
            raise RuntimeError("whatsapp media: refusing an unexpected media host")
        d = await c.get(meta["url"], headers=auth)
    if d.status_code != 200 or not d.content:
        raise RuntimeError(f"whatsapp media {media_id}: download HTTP {d.status_code}")
    if len(d.content) > MAX_MEDIA_BYTES:
        raise RuntimeError("whatsapp media: download exceeds the 16 MB limit")
    # A JSON body here means the URL was not the media itself (a misrouted proxy, a wrong
    # WHATSAPP_GRAPH_BASE). Without this the bytes reach the speech provider and fail there as
    # "invalid data", which names the wrong component.
    if d.headers.get("content-type", "").startswith("application/json") or d.content[:1] == b"{":
        raise RuntimeError("whatsapp media: download returned JSON, not audio (check WHATSAPP_GRAPH_BASE)")
    return d.content, str(meta.get("mime_type") or d.headers.get("content-type") or "")


async def upload_media(data: bytes, mime: str, filename: str = "reply.ogg") -> dict:
    """Upload bytes to Meta → ``{"ok": True, "id": media_id}``. The id is what an audio message sends."""
    tok, pnid = token(), phone_number_id()
    if not (tok and pnid):
        return {"ok": False, "error": "no WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID"}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                _graph(f"{pnid}/media"),
                headers={"Authorization": f"Bearer {tok}"},
                data={"messaging_product": "whatsapp", "type": mime.split(";", 1)[0]},
                files={"file": (filename, data, mime)},
            )
        body = r.json() if r.content else {}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"upload failed: {type(e).__name__}"}
    if r.status_code == 200 and body.get("id"):
        return {"ok": True, "id": str(body["id"])}
    return {
        "ok": False,
        "error": str(
            ((body.get("error") or {}) if isinstance(body, dict) else {}).get("message")
            or f"HTTP {r.status_code}"
        ),
    }


async def send_audio(to: str, media_id: str) -> dict:
    """An uploaded media id → a voice message. Free-form, so only valid INSIDE the 24-hour window."""
    return await _post(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "audio",
            "audio": {"id": media_id},
        }
    )


class WhatsAppIO:
    """This channel's side of a turn pipeline (the ``turns.channel.ChannelIO`` interface)."""

    name = "whatsapp"
    accepted_audio = ACCEPTED_AUDIO

    async def fetch(self, part: Part) -> bytes:
        data, mime = await fetch_media(part.ref)
        part.data, part.mime = data, part.mime or mime
        return data

    async def send(self, recipient: str, parts) -> list[dict]:
        results = []
        for p in parts:
            if p.kind == TEXT:
                results.append(await send_message(recipient, p.text))
            elif p.kind == AUDIO:
                results.append(await self._send_audio(recipient, p))
            else:
                results.append({"ok": False, "error": f"whatsapp: sending {p.kind} is not supported"})
        return results

    async def _send_audio(self, to: str, p: Part) -> dict:
        if not window_open(to):
            # A template cannot carry audio; the text part (sent as a template) is the reply.
            return {"ok": False, "error": "voice reply needs an open 24h window", "mode": "skipped"}
        base = (p.mime or "").split(";", 1)[0].strip().lower()
        if base not in {a.split(";", 1)[0].strip().lower() for a in self.accepted_audio}:
            # The service was told what this channel plays (``accept``) and sent something else.
            # The text reply has already gone out, so this costs the voice note and nothing more.
            return {"ok": False, "error": f"whatsapp cannot play {p.mime or 'unknown audio'}", "mode": "skipped"}
        up = await upload_media(p.data, p.mime, filename="reply.ogg" if "ogg" in base else "reply.mp3")
        if not up.get("ok"):
            return up
        res = await send_audio(to, up["id"])
        if res.get("ok"):
            res["mode"] = "audio"
        return res
