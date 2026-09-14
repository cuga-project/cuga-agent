"""CUGA IS THE DOOR — every channel utterance goes to CUGA's ``POST /run``.

Before this, each channel adapter posted its message to the eventing service's own ``/invoke`` with
``agent="concierge"``, so the eventing layer decided chat-vs-arming and only *then* called CUGA.
That made the eventing layer the front door for chat, which is backwards: chat is CUGA's job, and
eventing is the exception.

Now the adapters are thin transports. They hold the bot tokens (they must — they own the sockets)
but they make **no routing decision at all**: they normalise a message and hand it to ``/run``.
CUGA decides:

    channel adapter ──POST cuga/run──▶  CUGA  ─┬─ plain chat  → run the agent, answer
    (slack/telegram/                           │
     discord/web)                              └─ /automate…  → POST events/api/concierge (arm)
                                                  or a thread with an arming dialogue open

The delivery target rides on ``thread_id`` (``gw:<channel>:<native>#<locus>``) — ``channel_origin``
parses it — so a flow armed from Slack fires back into that Slack thread without anyone passing a
"reply to" anywhere.

The one thing that does NOT come through here is a scheduled FIRE: a tick is already-decided work
aimed at a known agent, and it needs delivery + run-logging, so it keeps going through ``/invoke``.
"""

from __future__ import annotations

import os

from .trace import Trace, new_trace_id


def cuga_url() -> str:
    """Where CUGA lives. CUGA_URL is the deployed setting; the port fallback keeps a bare local
    run working. NB: not EVENTS_CUGA_PORT — that means "where /invoke lives", which is us."""
    base = (os.environ.get("CUGA_URL", "") or "").split(" #", 1)[0].strip()
    if base:
        return base.rstrip("/")
    return f"http://127.0.0.1:{os.environ.get('CUGA_PORT', '7860')}"


def _token() -> str:
    return (
        (os.environ.get("CUGA_RUN_TOKEN") or os.environ.get("GATEWAY_TOKEN") or "").split(" #", 1)[0].strip()
    )


async def ask(
    text: str, *, channel: str, native_id: str, user: str = "", locus: str = "", timeout: float = 180.0
) -> str:
    """Send one channel utterance to CUGA and return the answer text ("" if none).

    ``locus`` is the in-channel conversation anchor (a Slack thread_ts, a Discord thread id) — it
    keys MEMORY per topic. ``native_id`` is the channel/chat id, which is the DELIVERY address; the
    ``#locus`` suffix is stripped by ``channel_native_id`` so delivery is unaffected by threading.
    """
    import httpx

    tr = Trace(new_trace_id())
    tid = f"gw:{channel}:{native_id}" + (f"#{locus}" if locus else "")
    body = {
        "query": text,
        "thread_id": tid,
        # Identity travels so an armed flow lands in the scope the Studio browses, and so per-user
        # credentials resolve. CUGA passes these straight through when it forwards to the concierge.
        "channel": {"name": channel, "native_id": native_id, "user": user, "thread_id": tid},
    }
    headers = {"Content-Type": "application/json"}
    tok = _token()
    if tok:
        headers["X-Gateway-Token"] = tok
    base = cuga_url()
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(f"{base}/run", headers=headers, json=body)
    except Exception as e:  # noqa: BLE001 — a down CUGA must not kill the channel loop
        tr.error(f"{channel}.ask", err=str(e), cuga=base)
        return ""
    if r.status_code != 200:
        tr.error(f"{channel}.ask", status=r.status_code, body=r.text[:160], cuga=base)
        return ""
    data = r.json() or {}
    answer = data.get("answer") or ""
    tr(f"{channel}.ask", thread=tid, ok=bool(answer), status=data.get("status"))
    return answer
