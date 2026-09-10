"""Direct Telegram integration — long-polling, bypassing Activepieces.

Telegram's bot API offers two ways to receive messages: a **webhook** (Telegram POSTs to a public
URL — what the AP backend uses, via AP's tunnel) or **long-polling** (``getUpdates`` — an OUTBOUND
HTTPS call the bot makes in a loop). We use long-polling here, the same "go direct" move as
``discord_direct``'s Gateway: it's an outbound connection, so it needs **no public URL and no AP** —
which is exactly what lets Telegram chat work in a zero-AP, no-tunnel setup.

Flow:  getUpdates loop ▸ CUGA POST /run ▸ sendMessage back to the chat.

This is the DEFAULT Telegram backend. The AP webhook path stays behind ``EVENTS_TELEGRAM_BACKEND=ap``
(kept for the case where you want Telegram armed as an AP flow alongside the SaaS integrations).

Env: ``TELEGRAM_BOT_TOKEN`` (required), ``EVENTS_TELEGRAM_BOT_USERNAME`` (optional — enables the
group @mention gate), ``EVENTS_TELEGRAM_CHAT`` = ``all`` (default) | ``mention``.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

log = logging.getLogger("events.telegram_direct")

API = "https://api.telegram.org"


def bot_token() -> str:
    try:
        from .secret_seam import secret as _secret
    except ImportError:  # flat load (tests put the events dir on sys.path)
        from secret_seam import secret as _secret
    return _secret("TELEGRAM_BOT_TOKEN")


BACKOFF_DEFAULT = 5.0  # any non-200 with nothing better to go on
BACKOFF_MAX = 300.0  # a hostile or fat-fingered retry_after must not wedge the poller for hours


def backoff_seconds(resp, default: float = BACKOFF_DEFAULT) -> float:
    """How long to wait before re-polling after a non-200 ``getUpdates``.

    On 429 Telegram states the wait AUTHORITATIVELY in the body — ``{"parameters": {"retry_after":
    30}}`` — and retrying sooner just earns another 429, so ignoring it deepens the rate limit
    instead of riding it out. A ``Retry-After`` header is honoured as a fallback because proxies in
    front of the API set that one instead.

    Pure and duck-typed on purpose (anything with ``.json()`` and ``.headers`` works), so the
    back-off policy is unit-testable without a network or a real httpx response.
    """
    val = None
    try:
        body = resp.json()
        params = body.get("parameters") if isinstance(body, dict) else None
        if isinstance(params, dict):
            val = params.get("retry_after")
    except Exception:  # noqa: BLE001 — a non-JSON error page is normal here
        val = None
    if val is None:
        try:
            val = (resp.headers or {}).get("Retry-After")
        except Exception:  # noqa: BLE001
            val = None
    try:
        secs = float(val)
    except (TypeError, ValueError):
        return default
    if secs != secs or secs <= 0:  # NaN, or a nonsense non-positive value
        return default
    return min(secs, BACKOFF_MAX)


def _base() -> str:
    return f"{API}/bot{bot_token()}"


def should_process(msg: dict) -> bool:
    """A real human message we should answer — has text + a chat id, and the sender isn't a bot.
    ``msg`` is the Telegram ``message`` object (already lifted out of the update envelope)."""
    if not msg or not msg.get("text") or not (msg.get("chat") or {}).get("id"):
        return False
    if (msg.get("from") or {}).get("is_bot"):
        return False
    return True


def chat_mode() -> str:
    """EVENTS_TELEGRAM_CHAT: 'all' (default — every message with text reaches the concierge) or
    'mention' (a GROUP message reaches CHAT only when it @mentions the bot; a PRIVATE chat is
    inherently addressed to the bot and always passes). Mirrors discord_direct.chat_mode."""
    return (os.environ.get("EVENTS_TELEGRAM_CHAT", "all").split(" #", 1)[0].strip().lower()) or "all"


def bot_username() -> str:
    return os.environ.get("EVENTS_TELEGRAM_BOT_USERNAME", "").split(" #", 1)[0].strip().lstrip("@")


def mention_gate(msg: dict) -> tuple[bool, str]:
    """(reaches CHAT?, text with a leading @bot mention stripped). Gates CHAT only — the caller must
    still dispatch channel-message WATCHERS on gated traffic (mirrors discord_direct.mention_gate).

    Passes: mode != 'mention' · a PRIVATE chat (``chat.type == 'private'`` — inherently addressed to
    the bot) · a GROUP message that @mentions the bot's username. Without a configured username we
    can't detect a group mention, so we fail OPEN (never silently drop)."""
    text = msg.get("text") or ""
    ctype = (msg.get("chat") or {}).get("type") or "private"
    if chat_mode() != "mention" or ctype == "private":
        return True, text
    uname = bot_username()
    if not uname:
        return True, text  # can't detect a mention → fail open
    tag = f"@{uname}"
    if tag.lower() in text.lower():
        # strip a leading "@bot " so the concierge sees the bare request
        cleaned = text.replace(tag, " ").strip() if text.strip().lower().startswith(tag.lower()) else text
        return True, cleaned
    return False, text


async def send_message(chat_id, text: str, reply_to=None) -> dict:
    """Post to the chat via the Bot API (bot token). Telegram caps a message at 4096 chars;
    ``reply_to`` (a message id) makes it a threaded reply to that message."""
    tok = bot_token()
    if not tok:
        return {"ok": False, "error": "no TELEGRAM_BOT_TOKEN"}
    body: dict = {"chat_id": chat_id, "text": (text or "")[:4096]}
    if reply_to:
        body["reply_to_message_id"] = reply_to
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{_base()}/sendMessage", json=body)
        if r.status_code < 300:
            return {"ok": True}
        return {"ok": False, "error": f"HTTP {r.status_code}", "detail": r.text[:200]}


async def _get_me() -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{_base()}/getMe")
            return (r.json() or {}).get("result") or {} if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001
        return {}


async def run_poller(
    on_message, *, stop: asyncio.Event | None = None, ready: asyncio.Future | None = None
) -> None:
    """Long-poll ``getUpdates`` and call ``on_message(message)`` for each human text message.

    Uses the ``offset`` cursor so each update is delivered once (ack'd by requesting offset =
    last_update_id + 1). Reconnects on error. ``stop`` (an Event) ends the loop cleanly; ``ready``
    (a Future) is resolved with getMe info on the first successful poll. Mirrors
    discord_direct.run_gateway's contract so app.py wires it the same way."""
    tok = bot_token()
    if not tok:
        log.warning("telegram_direct: no TELEGRAM_BOT_TOKEN — poller not started")
        return
    # A bot that previously ran on the AP WEBHOOK backend has a webhook registered with Telegram.
    # While a webhook is set, getUpdates is REJECTED with 409 Conflict and every message is routed to
    # the (now dead) webhook instead — the exact "my Telegram message never reached CUGA" symptom.
    # Long-poll and webhook are mutually exclusive, so clear any webhook before we start polling.
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{_base()}/deleteWebhook")
            if r.status_code == 200 and (r.json() or {}).get("result"):
                log.info("telegram: cleared a pre-existing webhook (switching to long-poll)")
    except Exception as e:  # noqa: BLE001
        log.warning("telegram deleteWebhook failed (%s) — long-poll may 409 if a webhook is set", e)

    me = await _get_me()
    if me and not bot_username() and me.get("username"):
        os.environ.setdefault("EVENTS_TELEGRAM_BOT_USERNAME", me["username"])  # enable group mention gate
    log.info("telegram long-poll started as @%s", me.get("username") or "?")
    if ready is not None and not ready.done():
        ready.set_result(me)
    # Drop any backlog queued while the bot was offline: start from the latest, so a restart doesn't
    # replay old messages (getUpdates with offset=-1 returns only the last update; +1 acks it).
    offset = None
    try:
        async with httpx.AsyncClient(timeout=35) as c:
            r = await c.get(f"{_base()}/getUpdates", params={"offset": -1, "timeout": 0})
            res = (r.json() or {}).get("result") or []
            if res:
                offset = res[-1]["update_id"] + 1
    except Exception:  # noqa: BLE001
        pass
    # Handler tasks are RETAINED. asyncio keeps only a weak reference to a bare create_task, so a
    # pending handler could be garbage-collected mid-flight — the message is simply lost, silently.
    # The done-callback also surfaces exceptions that would otherwise vanish with the task.
    pending: set[asyncio.Task] = set()

    def _spawn(coro) -> None:
        t = asyncio.create_task(coro)
        pending.add(t)
        t.add_done_callback(pending.discard)
        t.add_done_callback(
            lambda f: (
                None
                if f.cancelled() or f.exception() is None
                else log.warning("telegram handler failed: %r", f.exception())
            )
        )

    while not (stop and stop.is_set()):
        try:
            async with httpx.AsyncClient(timeout=35) as c:
                params = {"timeout": 25, "allowed_updates": '["message"]'}
                if offset is not None:
                    params["offset"] = offset
                r = await c.get(f"{_base()}/getUpdates", params=params)
                # A NON-200 returns immediately, and the old code turned it into an empty update
                # list with no sleep — so the loop re-requested at full speed. Telegram answers 401
                # for a revoked token, 409 while a webhook is registered, and 429 under rate
                # limiting: each of those pinned a CPU core and, in the 429 case, deepened the
                # limit. Back off exactly like the exception path does.
                if r.status_code != 200:
                    wait = backoff_seconds(r)
                    log.warning(
                        "telegram getUpdates HTTP %s: %s (retrying in %.0fs)",
                        r.status_code,
                        (r.text or "")[:200],
                        wait,
                    )
                    if stop and stop.is_set():
                        break
                    await asyncio.sleep(wait)
                    continue
                updates = (r.json() or {}).get("result") or []
            for up in updates:
                offset = up["update_id"] + 1
                msg = up.get("message") or up.get("edited_message")
                if msg and should_process(msg):
                    _spawn(on_message(msg))
        except Exception as e:  # noqa: BLE001
            log.warning("telegram poll error: %s (retrying in 5s)", e)
            if stop and stop.is_set():
                break
            await asyncio.sleep(5)
