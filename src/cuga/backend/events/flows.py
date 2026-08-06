"""Activepieces flow **builders** — deterministic, testable, pure functions.

The concierge (an LLM) picks typed slots; these builders render a valid AP flow JSON
(see events_docs/ARCHITECTURE.md). Every flow is: TRIGGER ▸ POST /invoke ▸ [Router] ▸ sink.
No LLM here, no I/O — just dict construction, so it's fully unit-testable and diff-able
against tests/events (offline flow-builder tests).

Piece names/inputs are representative (AP schemas drift across versions); the shapes match the
offline flow-builder tests in tests/events.
"""

from __future__ import annotations

PIECE = {
    "schedule": "@activepieces/piece-schedule",
    "http": "@activepieces/piece-http",
    "telegram": "@activepieces/piece-telegram-bot",
    "discord": "@activepieces/piece-discord",
    "slack": "@activepieces/piece-slack",
    "gmail": "@activepieces/piece-gmail",
    "box": "@activepieces/piece-box",
    "github": "@activepieces/piece-github",
    "webhook": "@activepieces/piece-webhook",
    "rss": "@activepieces/piece-rss",
    "approval": "@activepieces/piece-approval",
}

# ── trigger resolution — the registry (triggers.py) is the single source of truth ──────────────
# ``SOURCE_TRIGGER``/``PUSH_PAYLOAD`` used to be hand-maintained dicts with exactly one trigger per
# integration; they are now *generated views* over the registry, kept only so legacy callers and
# older tests keep working. New code calls ``resolve_trigger(source, event)`` and gets the full
# registry row — and an UNKNOWN (source, event) raises instead of silently building a flow on a
# nonexistent ``new_item`` trigger that could never publish.
try:
    from . import triggers as _registry
except ImportError:  # bare import path (tests put the events dir itself on sys.path)
    import triggers as _registry  # type: ignore


def resolve_trigger(source: str, event: str = ""):
    """(source, event) → the registry Trigger row (ANY backend). Raises ValueError only on a
    genuinely-unknown trigger — loudly, at build time, instead of arming a flow that can never
    publish. Returns None for the legacy channel/webhook/rss rows (caller falls back to
    ``_LEGACY_SOURCE_TRIGGER``). Callers that need an AP-armable trigger must check ``.backend``."""
    t = _registry.get(source, event)
    if t is None:
        legacy = _LEGACY_SOURCE_TRIGGER.get(source)
        if legacy:
            return None  # caller falls back to the legacy (piece, trigger) pair
        known = ", ".join(f"{x.app}/{x.event}" for x in _registry.rows())
        raise ValueError(f"unknown push trigger {source!r}/{event!r} — known: {known}")
    return t


# Legacy channel/misc rows that predate the registry (converse flows + the AP webhook piece).
_LEGACY_SOURCE_TRIGGER = {
    "rss": ("rss", "new_item"),
    "telegram": ("telegram", "new_telegram_message"),
    "discord": ("discord", "new_message"),
    "slack": ("slack", "new-message"),
    "webhook": ("webhook", "catch_webhook"),
}

# Generated legacy view: one (piece, ap_trigger) per source name older callers use.
SOURCE_TRIGGER = dict(_LEGACY_SOURCE_TRIGGER)
for _t in _registry.rows():
    if _t.backend == "ap" and _t.default:
        SOURCE_TRIGGER[_t.app] = (_t.piece, _t.ap_trigger)
for _alias, (_app, _ev) in _registry.SOURCE_ALIASES.items():
    _row = _registry.get(_app, _ev)
    if _row is not None and _row.backend == "ap":
        SOURCE_TRIGGER[_alias] = (_row.piece, _row.ap_trigger)

# Generated legacy view of the curated /invoke payload per source name. Field paths follow each
# piece's OWN sample output (fetched from the live AP catalog — see triggers.py); unknown paths
# render empty and the `_raw` net (ap_engine) carries the full payload regardless.
PUSH_PAYLOAD = {}
for _t in _registry.rows():
    if _t.backend == "ap" and _t.payload:
        if _t.default:
            PUSH_PAYLOAD[_t.app] = dict(_t.payload)
        PUSH_PAYLOAD[f"{_t.app}/{_t.event}"] = dict(_t.payload)
for _alias, (_app, _ev) in _registry.SOURCE_ALIASES.items():
    _row = _registry.get(_app, _ev)
    if _row is not None and _row.payload:
        PUSH_PAYLOAD[_alias] = dict(_row.payload)


def push_payload(source: str, event: str = "") -> dict:
    """The curated payload map for (source, event) — registry-first, legacy-name fallback."""
    t = _registry.get(source, event)
    if t is not None and t.payload:
        return dict(t.payload)
    return dict(PUSH_PAYLOAD.get(source, {}))


# CHANNEL descriptors — the ONLY place channel specifics live, as declarative config (AP owns the
# execution; CUGA just names the piece + which trigger fields hold the message/sender and which
# send action + arg names to use). Adding a channel = one row here, no CUGA code.
# Trigger/action names + field refs VERIFIED against live AP piece metadata (2026-07-03):
#   telegram-bot@0.6.4 · discord@0.5.3 · slack@0.17.2
CHANNELS = {
    "telegram": {
        "piece": "telegram",
        "trigger": "new_telegram_message",
        "text_ref": "{{trigger.message.text}}",
        "native_ref": "{{trigger.message.chat.id}}",
        "send_action": "send_text_message",
        "target_arg": "chat_id",
        "text_arg": "message",
    },
    # Discord's new_message is a POLLING trigger that watches ONE channel (required `channel` input,
    # supplied at arm time). Replies go to the message's channel_id — which for a message posted in a
    # THREAD is the thread's id, so replies land back in the thread automatically.
    "discord": {
        "piece": "discord",
        "trigger": "new_message",
        "text_ref": "{{trigger.content}}",
        "native_ref": "{{trigger.channel_id}}",
        "user_ref": "{{trigger.author.id}}",  # message AUTHOR → per-user identity (VERIFY field
        # name on first live Discord msg; wrong → falls back)
        "send_action": "sendMessageWithBot",
        "target_arg": "channel_id",
        "text_arg": "message",
        "trigger_args": ["channel"],  # the channel id to poll (given at arm time)
        "dynamic_props": ["channel", "channel_id"],
    },  # DROPDOWNs fed literal/template → DYNAMIC
    # Slack's new-message is an APP_WEBHOOK (Slack Events API → instant, like Telegram). Replies go
    # to the message's channel (thread-safe: a threaded reply carries the parent channel + thread_ts).
    # The trigger requires ignoreBots=true (avoids the bot replying to itself); the send DROPDOWN
    # channel is fed a template → DYNAMIC; sendAsBot is required.
    "slack": {
        "piece": "slack",
        "trigger": "new-message",
        "text_ref": "{{trigger.text}}",
        "native_ref": "{{trigger.channel}}",
        "user_ref": "{{trigger.user}}",  # AUTHOR → per-user id (AP path; direct sets ev.user)
        "send_action": "send_channel_message",
        "target_arg": "channel",
        "text_arg": "text",
        "const": {"sendAsBot": True},  # send_channel_message REQUIRES sendAsBot
        "trigger_const": {"ignoreBots": True},  # required trigger input (skip bot messages)
        "dynamic_props": ["channel"],
    },  # DROPDOWN fed a template → DYNAMIC
}

_HOST = "{{connections.ea_host}}"
_TOKEN = "{{connections.ea_gateway_token}}"


# ---- steps ---------------------------------------------------------------
def invoke_step(
    agent: str,
    thread_id: str,
    prompt: str,
    *,
    source_type: str,
    source_name: str,
    event_kind: str,
    payload: dict | None = None,
    deliver: bool = False,
    name: str = "step_1",
    source_user: str = "",
) -> dict:
    """The HTTP step that calls back into CUGA's ``/invoke`` seam. ``source_user`` (a trigger
    template like discord's ``{{trigger.author.id}}``) forwards the message AUTHOR for per-user id."""
    src = {"type": source_type, "name": source_name, "thread_id": thread_id}
    if source_user:
        src["user"] = source_user
    return {
        "name": name,
        "displayName": "POST /invoke  (CUGA worker)",
        "type": "PIECE",
        "settings": {
            "pieceName": PIECE["http"],
            "actionName": "send_request",
            "input": {
                "method": "POST",
                "url": f"{_HOST}/invoke",
                "headers": {"X-Gateway-Token": _TOKEN},
                "body": {
                    "agent": agent,
                    "thread_id": thread_id,
                    "text": prompt,
                    "deliver": deliver,
                    "source": src,
                    "event": {"kind": event_kind, "payload": payload or {}},
                },
            },
        },
        "nextAction": None,
    }


def send_step(channel: str, target: str, text: str, name: str = "step_2") -> dict:
    """A connector send-step (delivery). Channels (telegram/discord/slack) come from the CHANNELS
    descriptor. Adding a channel = a CHANNELS row, no code here.
    for the Gmail send path — one renderer for delivery."""
    if channel in CHANNELS:
        d = CHANNELS[channel]
        settings = {
            "pieceName": PIECE[d["piece"]],
            "actionName": d["send_action"],
            "input": {d["target_arg"]: target, d["text_arg"]: text, **d.get("const", {})},
        }
    else:  # generic fallback
        settings = {
            "pieceName": PIECE.get(channel, f"@activepieces/piece-{channel}"),
            "actionName": "send_message",
            "input": {"channel": target, "text": text},
        }
    return {
        "name": name,
        "displayName": f"{channel.title()} · Send",
        "type": "PIECE",
        "settings": settings,
        "nextAction": None,
    }


# ---- triggers ------------------------------------------------------------
def _schedule_trigger(cron: str | None, interval_seconds: int | None, nxt: dict) -> dict:
    if cron:
        settings = {
            "pieceName": PIECE["schedule"],
            "triggerName": "cron_expression",
            "input": {"cronExpression": cron},
        }
    else:
        settings = {
            "pieceName": PIECE["schedule"],
            "triggerName": "every_x_minutes",
            "input": {"minutes": max(1, int((interval_seconds or 60) / 60))},
        }
    return {
        "name": "trigger",
        "displayName": "Schedule",
        "type": "PIECE_TRIGGER",
        "settings": settings,
        "nextAction": nxt,
    }


def _piece_trigger(source: str, nxt: dict, source_input: dict | None = None, event: str = "") -> dict:
    """(source, event) → a PIECE_TRIGGER node. Registry-resolved; unknown triggers raise
    (the old code silently fell back to a nonexistent ``new_item`` trigger). A DIRECT-backend row
    (slack/discord/box-folder watchers — CUGA receives the event, no AP piece) renders a
    descriptive placeholder node: only the dry-run planner ever builds those as flows."""
    t = resolve_trigger(source, event)
    if t is not None and t.backend == "direct":
        return {
            "name": "trigger",
            "displayName": f"CUGA direct · {t.app}/{t.event}",
            "type": "PIECE_TRIGGER",
            "settings": {
                "pieceName": "cuga-direct",
                "triggerName": t.direct_kind or t.event,
                "input": dict(source_input or {}),
            },
            "nextAction": nxt,
        }
    if t is not None:
        piece, trig = t.piece, t.ap_trigger
    else:
        piece, trig = _LEGACY_SOURCE_TRIGGER[source]
    return {
        "name": "trigger",
        "displayName": f"{piece.title()} · {trig}",
        "type": "PIECE_TRIGGER",
        "settings": {
            "pieceName": PIECE.get(piece, f"@activepieces/piece-{piece}"),
            "triggerName": trig,
            "input": dict(source_input or {}),
        },
        "nextAction": nxt,
    }


# ---- flow builders (one per mode) ---------------------------------------
def build_cron_flow(
    *,
    agent: str,
    thread_id: str,
    prompt: str,
    cron: str | None = None,
    interval_seconds: int | None = None,
    sink: dict | None = None,
    display: str = "cron flow",
) -> dict:
    """CRON: schedule ▸ /invoke ▸ (send). Also used for POLL (see build_poll_flow)."""
    step1 = invoke_step(
        agent,
        thread_id,
        prompt,
        source_type="time",
        source_name="cron",
        event_kind="tick",
        deliver=sink is None,
    )
    if sink:
        step1["nextAction"] = sink
    return {
        "displayName": display,
        "valid": True,
        "trigger": _schedule_trigger(cron, interval_seconds, step1),
    }


def build_poll_flow(
    *,
    agent: str,
    thread_id: str,
    prompt: str,
    cron: str | None = None,
    interval_seconds: int | None = 900,
    sink: dict | None = None,
    display: str = "poll flow",
) -> dict:
    """POLL: same shape as cron (timer or cron); the worker suppresses no-op runs
    (emit-on-change via get_state/set_state)."""
    f = build_cron_flow(
        agent=agent,
        thread_id=thread_id,
        prompt=prompt,
        cron=cron,
        interval_seconds=interval_seconds if not cron else None,
        sink=sink,
        display=display,
    )
    f["__mode"] = "poll"  # marker: worker uses get_state/set_state to emit only on change
    return f


def build_runonce_flow(
    *, agent: str, thread_id: str, prompt: str, sink: dict | None = None, display: str = "run-once flow"
) -> dict:
    """NOW as a fire-once flow (for uniform run history). Channel-NOW usually rides the
    inbound flow instead — this is for NOW requests that need their own trigger."""
    step1 = invoke_step(
        agent,
        thread_id,
        prompt,
        source_type="time",
        source_name="runonce",
        event_kind="runonce",
        deliver=sink is None,
    )
    if sink:
        step1["nextAction"] = sink
    return {
        "displayName": display,
        "valid": True,
        "trigger": {
            "name": "trigger",
            "displayName": "Run once",
            "type": "PIECE_TRIGGER",
            "settings": {"pieceName": PIECE["schedule"], "triggerName": "run_once", "input": {}},
            "nextAction": step1,
        },
    }


def build_push_flow(
    *,
    agent: str,
    thread_id: str,
    prompt: str,
    source: str,
    source_input: dict | None = None,
    sink: dict | None = None,
    event_kind: str = "",
    display: str = "push flow",
) -> dict:
    """PUSH: <source>·<event> ▸ /invoke ▸ send. Powers the PR reviewer and the integration watchers.

    ``event_kind`` selects the SPECIFIC trigger via the registry; empty → the app's default
    trigger. The /invoke payload carries the trigger's curated fields plus the ``_raw`` net."""
    row = _registry.get(source, event_kind)
    kind = event_kind or (row.event if row is not None else "new_file")
    step1 = invoke_step(
        agent,
        thread_id,
        prompt,
        source_type="integration",
        source_name=source,
        event_kind=kind,
        payload={**push_payload(source, kind), "_raw": "{{trigger}}"},
    )
    step1["nextAction"] = sink
    return {
        "displayName": display,
        "valid": True,
        "trigger": _piece_trigger(source, step1, source_input, event=kind),
    }


def build_inbound_flow(*, channel: str, agent: str = "concierge", display: str | None = None) -> dict:
    """A standing channel-inbound flow: <channel>·new_message ▸ /invoke ▸ <channel>·send.
    This is how a channel message reaches the concierge (routing). Channel specifics come from
    the CHANNELS descriptor, so Telegram/Discord/Slack are one flow shape — no per-channel code.
    The sender's native id rides in thread_id (``gw:<channel>:<native>``) so /invoke resolves the
    user (decision 0007)."""
    d = CHANNELS.get(channel, CHANNELS["telegram"])
    native, text = d["native_ref"], d["text_ref"]
    step1 = invoke_step(
        agent,
        f"gw:{channel}:{native}",
        text,
        source_type="channel",
        source_name=channel,
        event_kind="message",
        deliver=False,
        source_user=d.get("user_ref", ""),
    )
    step1["nextAction"] = send_step(channel, native, "{{step_1.body.answer}}")
    # The trigger comes from the CHANNELS descriptor — the same row the LIVE path
    # (ap_engine.create_inbound_flow) arms. The old lookup went through SOURCE_TRIGGER, whose
    # telegram/slack trigger names had silently drifted from CHANNELS (new_message vs
    # new_telegram_message / new-message), so the offline builder rendered a different flow
    # than the live one armed.
    trig = {
        "name": "trigger",
        "displayName": f"{d['piece'].title()} · {d['trigger']}",
        "type": "PIECE_TRIGGER",
        "settings": {
            "pieceName": PIECE[d["piece"]],
            "triggerName": d["trigger"],
            "input": dict(d.get("trigger_const", {})),
        },
        "nextAction": step1,
    }
    return {"displayName": display or f"{channel}-inbound → {agent}", "valid": True, "trigger": trig}


# ---- dispatcher ----------------------------------------------------------
def build_flow(mode: str, **kw) -> dict:
    """Dispatch by trigger mode. mode ∈ NOW|CRON|POLL|PUSH (INBOUND via build_inbound_flow)."""
    m = (mode or "").upper()
    if m == "CRON":
        return build_cron_flow(**kw)
    if m == "POLL":
        return build_poll_flow(**kw)
    if m == "PUSH":
        return build_push_flow(**kw)
    if m == "NOW":
        return build_runonce_flow(**kw)
    raise ValueError(f"unknown flow mode {mode!r} (want NOW|CRON|POLL|PUSH)")
