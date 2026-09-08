"""Direct Discord integration — a Gateway (WebSocket) bot, bypassing Activepieces.

Discord's real-time surface is the **Gateway** (a persistent WebSocket). AP's ``new_message`` trigger
POLLS (~5 min); this holds a Gateway connection for **instant** messages — the same "go direct" move
as ``slack_direct``. Bonus: the Gateway is an **outbound** connection, so it needs **no public URL**
(unlike Slack's Events API). This is the DEFAULT Discord backend; the AP polling path stays behind
``EVENTS_DISCORD_BACKEND=ap``.

Flow:  Discord Gateway (MESSAGE_CREATE) ▸ CUGA POST /run ▸ REST create-message back to the channel.

⚠️ Requires **MESSAGE CONTENT INTENT** enabled in the Discord Developer Portal (Bot → Privileged
Gateway Intents). Without it the Gateway closes with code 4014 (disallowed intents) or delivers empty
content. Env: ``DISCORD_BOT_TOKEN``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx

log = logging.getLogger("events.discord_direct")

GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"
API = "https://discord.com/api/v10"
# Gateway intents bitmask: GUILDS (1<<0) | GUILD_MESSAGES (1<<9) | MESSAGE_CONTENT (1<<15).
# GUILD_MEMBERS (1<<1) is PRIVILEGED: requesting it without enabling "Server Members Intent" in
# the Discord dev portal closes the whole gateway with 4014 — so it is opt-in via
# EVENTS_DISCORD_MEMBERS_INTENT=1 (set it only AFTER flipping the portal toggle).
INTENTS = (1 << 0) | (1 << 9) | (1 << 10) | (1 << 15)  # +GUILD_MESSAGE_REACTIONS (bit 10, non-priv)


def intents() -> int:
    n = INTENTS
    if os.environ.get("EVENTS_DISCORD_MEMBERS_INTENT", "") == "1":
        n |= 1 << 1  # GUILD_MEMBERS → GUILD_MEMBER_ADD events
    return n


def bot_token() -> str:
    try:
        from .secret_seam import secret as _secret
    except ImportError:  # flat load (tests put the events dir on sys.path)
        from secret_seam import secret as _secret
    return _secret("DISCORD_BOT_TOKEN")


def should_process(msg: dict) -> bool:
    """A real human message we should answer — has text + a channel, and the author isn't a bot."""
    if not msg or not msg.get("content") or not msg.get("channel_id"):
        return False
    if (msg.get("author") or {}).get("bot"):
        return False
    return True


_BOT_ID: dict = {"id": ""}  # the bot's own user id, learned from the Gateway READY


def chat_mode() -> str:
    """EVENTS_DISCORD_CHAT: 'all' (default — every channel message reaches the concierge) or
    'mention' (a channel message reaches CHAT only when it @mentions the bot; DMs and replies to
    the bot's own messages always do). Mirrors slack_direct.chat_mode."""
    return (os.environ.get("EVENTS_DISCORD_CHAT", "all").split(" #", 1)[0].strip().lower()) or "all"


def mention_gate(msg: dict) -> tuple[bool, str]:
    """(reaches CHAT?, text with the bot's mention stripped). Gates CHAT only — the caller must
    still dispatch channel-message WATCHERS on gated traffic (mirrors slack_direct.mention_gate).

    Passes: mode != 'mention' · a DM (no guild_id — inherently addressed to the bot) · an
    @mention of the bot (mentions[] or a raw <@id>/<@!id> token) · a REPLY to one of the bot's
    own messages (referenced_message.author.id == bot)."""
    text = msg.get("content") or ""
    if chat_mode() != "mention" or not msg.get("guild_id"):
        return True, text
    bid = _BOT_ID["id"]
    if not bid:
        return True, text  # READY not seen yet — fail open, never drop silently
    if (
        any(str(m.get("id")) == bid for m in (msg.get("mentions") or []))
        or f"<@{bid}>" in text
        or f"<@!{bid}>" in text
    ):
        return True, text.replace(f"<@!{bid}>", " ").replace(f"<@{bid}>", " ").strip()
    ref_author = ((msg.get("referenced_message") or {}).get("author") or {}).get("id")
    if str(ref_author or "") == bid:
        return True, text  # replying to the bot IS addressing it
    return False, text


async def send_message(channel_id: str, text: str, reply_to: str = "") -> dict:
    """Post to the channel via the REST API (bot token). Discord caps content at 2000 chars.
    ``reply_to`` (a message id) makes it a REPLY to that message — chat answers reference the
    question they answer instead of floating loose in the channel."""
    tok = bot_token()
    if not tok:
        return {"ok": False, "error": "no DISCORD_BOT_TOKEN"}
    body: dict = {"content": (text or "")[:2000]}
    if reply_to:
        # fail_if_not_exists=False: if the original was deleted, post normally instead of 400ing
        body["message_reference"] = {"message_id": str(reply_to), "fail_if_not_exists": False}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{API}/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {tok}", "content-type": "application/json"},
            json=body,
        )
        if r.status_code < 300:
            return {"ok": True}
        return {"ok": False, "error": f"HTTP {r.status_code}", "detail": r.text[:200]}


async def run_gateway(
    on_message, *, stop: asyncio.Event | None = None, ready: asyncio.Future | None = None, on_event=None
) -> None:
    """Hold a Gateway connection and call ``on_message(msg)`` for each human MESSAGE_CREATE.

    ``on_event(dispatch_type, data)`` (optional) additionally receives NON-message dispatches we
    care about — today GUILD_MEMBER_ADD (only delivered when the members intent is opted in, see
    :func:`intents`). Handles HELLO → IDENTIFY → heartbeat, and reconnects on drop (no session
    resume — a fresh IDENTIFY is simple and robust enough for our use). ``stop`` (an Event) ends
    the loop cleanly; ``ready`` (a Future) is resolved on the first READY."""
    import websockets  # local import so the module loads even if websockets is absent

    tok = bot_token()
    if not tok:
        log.warning("discord_direct: no DISCORD_BOT_TOKEN — gateway not started")
        return
    while not (stop and stop.is_set()):
        try:
            async with websockets.connect(GATEWAY_URL, max_size=2**23) as ws:
                hello = json.loads(await ws.recv())
                hb_interval = float(hello["d"]["heartbeat_interval"]) / 1000.0
                seq = {"s": None}
                await ws.send(
                    json.dumps(
                        {
                            "op": 2,
                            "d": {
                                "token": tok,
                                "intents": intents(),
                                "properties": {"os": "linux", "browser": "cuga", "device": "cuga"},
                            },
                        }
                    )
                )

                async def _heartbeat():
                    try:
                        while True:
                            await asyncio.sleep(hb_interval)
                            await ws.send(json.dumps({"op": 1, "d": seq["s"]}))
                    except Exception:  # noqa: BLE001 — connection closing; outer loop reconnects
                        return

                hbt = asyncio.create_task(_heartbeat())
                try:
                    async for raw in ws:
                        ev = json.loads(raw)
                        if ev.get("s") is not None:
                            seq["s"] = ev["s"]
                        op = ev.get("op")
                        if op == 0:  # DISPATCH
                            t = ev.get("t")
                            if t == "READY":
                                u = (ev.get("d") or {}).get("user") or {}
                                _BOT_ID["id"] = str(u.get("id") or "")  # for mention_gate
                                log.info(
                                    "discord gateway READY as %s#%s",
                                    u.get("username"),
                                    u.get("discriminator"),
                                )
                                if ready is not None and not ready.done():
                                    ready.set_result(u)
                            elif t == "MESSAGE_CREATE":
                                msg = ev.get("d") or {}
                                if should_process(msg):
                                    asyncio.create_task(on_message(msg))
                            elif t in ("GUILD_MEMBER_ADD", "MESSAGE_REACTION_ADD") and on_event is not None:
                                asyncio.create_task(on_event(t, ev.get("d") or {}))
                        elif op == 1:  # server requests a heartbeat now
                            await ws.send(json.dumps({"op": 1, "d": seq["s"]}))
                        elif op in (7, 9):  # reconnect / invalid session
                            break
                finally:
                    hbt.cancel()
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            if code == 4014:
                log.error(
                    "discord gateway: DISALLOWED INTENTS (4014) — enable MESSAGE CONTENT "
                    "INTENT in the Developer Portal (Bot → Privileged Gateway Intents)"
                )
            else:
                log.warning("discord gateway error: %s (reconnecting in 5s)", e)
        if stop and stop.is_set():
            break
        await asyncio.sleep(5)
