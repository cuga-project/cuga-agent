"""Direct Discord integration — a Gateway (WebSocket) bot, bypassing Activepieces.

Discord's real-time surface is the **Gateway** (a persistent WebSocket). AP's ``new_message`` trigger
POLLS (~5 min); this holds a Gateway connection for **instant** messages — the same "go direct" move
as ``slack_direct``. Bonus: the Gateway is an **outbound** connection, so it needs **no public URL**
(unlike Slack's Events API). This is the DEFAULT Discord backend; the AP polling path stays behind
``EVENTS_DISCORD_BACKEND=ap``.

Flow:  Discord Gateway (MESSAGE_CREATE) ▸ /invoke(concierge) ▸ REST create-message back to the channel.

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
INTENTS = (1 << 0) | (1 << 9) | (1 << 15)


def bot_token() -> str:
    return (os.environ.get("DISCORD_BOT_TOKEN", "") or "").split(" #", 1)[0].strip().strip('"').strip("'")


def should_process(msg: dict) -> bool:
    """A real human message we should answer — has text + a channel, and the author isn't a bot."""
    if not msg or not msg.get("content") or not msg.get("channel_id"):
        return False
    if (msg.get("author") or {}).get("bot"):
        return False
    return True


async def send_message(channel_id: str, text: str) -> dict:
    """Post a reply to the channel via the REST API (bot token). Discord caps content at 2000 chars."""
    tok = bot_token()
    if not tok:
        return {"ok": False, "error": "no DISCORD_BOT_TOKEN"}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{API}/channels/{channel_id}/messages",
                         headers={"Authorization": f"Bot {tok}",
                                  "content-type": "application/json"},
                         json={"content": (text or "")[:2000]})
        if r.status_code < 300:
            return {"ok": True}
        return {"ok": False, "error": f"HTTP {r.status_code}", "detail": r.text[:200]}


async def run_gateway(on_message, *, stop: asyncio.Event | None = None,
                      ready: asyncio.Future | None = None) -> None:
    """Hold a Gateway connection and call ``on_message(msg)`` for each human MESSAGE_CREATE.

    Handles HELLO → IDENTIFY → heartbeat, and reconnects on drop (no session resume — a fresh
    IDENTIFY is simple and robust enough for our use). ``stop`` (an Event) ends the loop cleanly;
    ``ready`` (a Future) is resolved on the first READY (used by the live check / tests)."""
    import websockets  # local import so the module loads even if websockets is absent
    tok = bot_token()
    if not tok:
        log.warning("discord_direct: no DISCORD_BOT_TOKEN — gateway not started")
        return
    while not (stop and stop.is_set()):
        try:
            async with websockets.connect(GATEWAY_URL, max_size=2 ** 23) as ws:
                hello = json.loads(await ws.recv())
                hb_interval = float(hello["d"]["heartbeat_interval"]) / 1000.0
                seq = {"s": None}
                await ws.send(json.dumps({"op": 2, "d": {
                    "token": tok, "intents": INTENTS,
                    "properties": {"os": "linux", "browser": "cuga", "device": "cuga"}}}))

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
                        if op == 0:                                   # DISPATCH
                            t = ev.get("t")
                            if t == "READY":
                                u = (ev.get("d") or {}).get("user") or {}
                                log.info("discord gateway READY as %s#%s",
                                         u.get("username"), u.get("discriminator"))
                                if ready is not None and not ready.done():
                                    ready.set_result(u)
                            elif t == "MESSAGE_CREATE":
                                msg = ev.get("d") or {}
                                if should_process(msg):
                                    asyncio.create_task(on_message(msg))
                        elif op == 1:                                 # server requests a heartbeat now
                            await ws.send(json.dumps({"op": 1, "d": seq["s"]}))
                        elif op in (7, 9):                            # reconnect / invalid session
                            break
                finally:
                    hbt.cancel()
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            if code == 4014:
                log.error("discord gateway: DISALLOWED INTENTS (4014) — enable MESSAGE CONTENT "
                          "INTENT in the Developer Portal (Bot → Privileged Gateway Intents)")
            else:
                log.warning("discord gateway error: %s (reconnecting in 5s)", e)
        if stop and stop.is_set():
            break
        await asyncio.sleep(5)
