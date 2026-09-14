"""DIRECT-event dispatch — standing watchers on transports CUGA already receives itself.

Slack's Events API, Discord's Gateway and the telegram message stream all reach CUGA without
Activepieces. Historically those events had exactly ONE consumer — the converse path (a human
message → the concierge) — and every other event type (a reaction, a member join, a channel
created) was dropped at the door. This module gives them a second consumer: **direct watcher
subscriptions** — rows armed by the concierge with ``ap_flow_id=None`` + an ``event`` +
``config`` (the trigger registry's direct-backend rows).

    event arrives (slack reaction / discord join / channel message)
        ▸ match(store, app, event, …)           — which active watchers want this?
        ▸ dispatch_all(...)                     — each fires its agent through POST /invoke
                                                   (the same seam every AP flow uses), delivering
                                                   back to where the watcher was armed from.

Filters come from the subscription's ``config`` slots:
    channel — the platform-native channel id the watcher is scoped to ("" = any)
    emoji   — reaction name, colons stripped ("" = any)
    pattern — a substring/regex the message text must match ("" = any)

No credentials here: outbound delivery rides /invoke's existing direct-channel adapters.
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("cuga.events.direct")

try:
    from . import triggers as _registry
except ImportError:  # flat load (tests put the events dir on sys.path)
    import triggers as _registry  # type: ignore


def kind_for(app: str, direct_kind: str) -> str:
    """A transport event type ("reaction_added", "GUILD_MEMBER_ADD") → our canonical event kind.
    "" when no registry row consumes it (the caller then drops the event, as before)."""
    for t in _registry.rows():
        if t.backend == "direct" and t.app == app and t.direct_kind == direct_kind:
            return t.event
    return ""


def _cfg_match(cfg: dict, *, channel: str = "", text: str = "", emoji: str = "") -> bool:
    want_ch = str(cfg.get("channel") or "").lstrip("#")
    if want_ch and want_ch != str(channel or "").lstrip("#"):
        return False
    want_emoji = str(cfg.get("emoji") or "").strip(":")
    if want_emoji and want_emoji != str(emoji or "").strip(":"):
        return False
    pattern = str(cfg.get("pattern") or "")
    if pattern:
        try:
            if not re.search(pattern, text or "", re.I):
                return False
        except re.error:  # a bad user regex degrades to substring match
            if pattern.lower() not in (text or "").lower():
                return False
    return True


def match(store, app: str, event: str, *, channel: str = "", text: str = "", emoji: str = "") -> list:
    """Active DIRECT watcher subscriptions for (app, event) whose config filters accept this
    occurrence. Store may be None (events layer without a subscription store) → []."""
    if store is None or not event:
        return []
    out = []
    for sub in store.list(status="active"):
        if sub.ap_flow_id:  # AP-armed flows are AP's to fire, not ours
            continue
        if sub.source_connector != app or (sub.event or "") != event:
            continue
        if not _cfg_match(sub.config or {}, channel=channel, text=text, emoji=emoji):
            continue
        out.append(sub)
    return out


def describe(app: str, event: str, payload: dict) -> str:
    """A compact, human-readable rendering of the event for the agent's prompt. The FULL payload
    also rides in event.payload, so nothing is lost by the summary."""
    bits = []
    for key in ("text", "reaction", "user", "channel", "name", "content"):
        v = payload.get(key)
        if v:
            bits.append(f"{key}={v}" if not isinstance(v, dict) else f"{key}={v.get('id', v)}")
    return f"[{app}/{event}] " + (", ".join(str(b) for b in bits) or "(see payload)")


async def dispatch_all(subs: list, *, app: str, event: str, payload: dict, engine=None) -> int:
    """Fire every matched watcher through POST /invoke (agent pinned to the subscription's,
    deliver=True → the answer goes back to the origin the watcher was armed from, via the
    existing direct-channel delivery). Returns how many dispatched; never raises."""
    import httpx

    try:
        from .secret_seam import secret as _secret
    except ImportError:
        from secret_seam import secret as _secret
    port = os.environ.get("EVENTS_CUGA_PORT", "7860")
    gw = _secret("GATEWAY_TOKEN")
    n = 0
    for sub in subs:
        text = f"{sub.prompt}\n\nThe watched event just happened: {describe(app, event, payload)}"
        inv = {
            "agent": sub.target_agent,
            "text": text,
            "deliver": True,
            "scope": sub.tenant,
            "source": {"type": "integration", "name": app, "thread_id": sub.thread_id},
            "event": {"kind": event, "payload": dict(payload or {})},
        }
        try:
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.post(f"http://127.0.0.1:{port}/invoke", headers={"X-Gateway-Token": gw}, json=inv)
            log.info("direct dispatch %s/%s → %s (HTTP %s)", app, event, sub.target_agent, r.status_code)
            n += 1
        except Exception as e:  # noqa: BLE001 — one broken watcher must not drop the others
            log.warning("direct dispatch %s/%s → %s failed: %s", app, event, sub.target_agent, e)
    return n
