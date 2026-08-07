"""Direct WhatsApp integration (Meta Cloud API) — no Activepieces.

AP's WhatsApp piece is SEND-ONLY (0 triggers, 3 actions), so it cannot back a channel: a channel
needs an inbound half. Telegram works over AP because ``piece-telegram-bot`` has a real webhook
trigger; WhatsApp has none. Since the inbound webhook has to be built here regardless — signature
check, verification handshake, payload parse — outbound is one more REST call, and routing that
through AP would add a hop and a dependency for the easy half. AP's real value (OAuth refresh,
per-user custody) does not apply either: a channel token is a single long-lived bot secret.

Flow:  Meta ▸ POST /api/events/whatsapp/events (this module) ▸ /run (cuga_door) ▸ Cloud API send.

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


def _graph(path: str) -> str:
    return f"https://graph.facebook.com/{api_version()}/{path}"


# ── inbound ─────────────────────────────────────────────────────────────────────────────────────
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
    ok = hmac.compare_digest(mine, sig)
    return ok, ("ok" if ok else "bad signature")


def handshake(params) -> tuple[bool, str]:
    """Meta's webhook verification: a GET carrying hub.mode/hub.verify_token/hub.challenge.

    Echo the challenge VERBATIM when the token matches, else refuse. Unlike Slack (which does this
    over POST with a JSON body), Meta uses query params on a GET — the endpoint therefore needs a
    real GET handler, not a friendly "you opened this in a browser" probe.
    """
    mode = params.get("hub.mode") or ""
    tok = params.get("hub.verify_token") or ""
    challenge = params.get("hub.challenge") or ""
    want = verify_token()
    if not want:
        return False, "WHATSAPP_VERIFY_TOKEN is not set"
    if mode != "subscribe":
        return False, f"unexpected hub.mode {mode!r}"
    if not hmac.compare_digest(tok, want):
        return False, "verify token mismatch"
    return True, challenge


def messages(body: dict) -> list[dict]:
    """The inbound messages in a webhook payload → ``[{wa_id, text, id, ts, name}, …]``.

    Meta nests these three deep (``entry[].changes[].value.messages[]``) and interleaves them with
    ``statuses[]`` (delivery receipts for messages WE sent). Only ``messages`` are human traffic;
    treating a status as a message makes the bot answer its own delivery receipt.
    """
    out: list[dict] = []
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
                if m.get("type") != "text":
                    continue  # media/interactive/reactions — not handled yet
                wa_id = str(m.get("from") or "")
                text = str(((m.get("text") or {}).get("body")) or "")
                if not (wa_id and text):
                    continue
                out.append(
                    {
                        "wa_id": wa_id,
                        "text": text,
                        "id": str(m.get("id") or ""),
                        "ts": str(m.get("timestamp") or ""),
                        "name": names.get(wa_id, ""),
                    }
                )
    return out


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
