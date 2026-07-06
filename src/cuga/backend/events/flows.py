"""Activepieces flow **builders** — deterministic, testable, pure functions.

The concierge (an LLM) picks typed slots; these builders render a valid AP flow JSON
(DESIGN §6 "reason vs. build"). Every flow is: TRIGGER ▸ POST /invoke ▸ [Router] ▸ sink.
No LLM here, no I/O — just dict construction, so it's fully unit-testable and diff-able
against events_docs/examples/sample_flow.*.json.

Piece names/inputs are representative (AP schemas drift across versions); the shapes match
the sample flows checked into events_docs.
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
}

# AP piece + trigger for each integration/channel source we support.
SOURCE_TRIGGER = {
    "box": ("box", "new_file"),
    "github_pr": ("github", "new_pull_request"),
    "github_issue": ("github", "new_issue"),
    "gmail": ("gmail", "new_email"),
    "rss": ("rss", "new_item"),
    "telegram": ("telegram", "new_message"),
    "discord": ("discord", "new_message"),
    "slack": ("slack", "new_message"),
    "webhook": ("webhook", "catch_webhook"),
}

# CHANNEL descriptors — the ONLY place channel specifics live, as declarative config (AP owns the
# execution; CUGA just names the piece + which trigger fields hold the message/sender and which
# send action + arg names to use). Adding a channel = one row here, no CUGA code.
# Trigger/action names + field refs VERIFIED against live AP piece metadata (2026-07-03):
#   telegram-bot@0.6.4 · discord@0.5.3 · slack@0.17.2
CHANNELS = {
    "telegram": {"piece": "telegram", "trigger": "new_telegram_message",
                 "text_ref": "{{trigger.message.text}}",
                 "native_ref": "{{trigger.message.chat.id}}",
                 "send_action": "send_text_message", "target_arg": "chat_id", "text_arg": "message"},
    # Discord's new_message is a POLLING trigger that watches ONE channel (required `channel` input,
    # supplied at arm time). Replies go to the message's channel_id — which for a message posted in a
    # THREAD is the thread's id, so replies land back in the thread automatically.
    "discord": {"piece": "discord", "trigger": "new_message",
                "text_ref": "{{trigger.content}}",
                "native_ref": "{{trigger.channel_id}}",
                "user_ref": "{{trigger.author.id}}",   # message AUTHOR → per-user identity (VERIFY field
                                                       # name on first live Discord msg; wrong → falls back)
                "send_action": "sendMessageWithBot", "target_arg": "channel_id", "text_arg": "message",
                "trigger_args": ["channel"],    # the channel id to poll (given at arm time)
                "dynamic_props": ["channel", "channel_id"]},  # DROPDOWNs fed literal/template → DYNAMIC
    # Slack's new-message is an APP_WEBHOOK (Slack Events API → instant, like Telegram). Replies go
    # to the message's channel (thread-safe: a threaded reply carries the parent channel + thread_ts).
    # The trigger requires ignoreBots=true (avoids the bot replying to itself); the send DROPDOWN
    # channel is fed a template → DYNAMIC; sendAsBot is required.
    "slack": {"piece": "slack", "trigger": "new-message",
              "text_ref": "{{trigger.text}}",
              "native_ref": "{{trigger.channel}}",
              "user_ref": "{{trigger.user}}",            # AUTHOR → per-user id (AP path; direct sets ev.user)
              "send_action": "send_channel_message", "target_arg": "channel", "text_arg": "text",
              "const": {"sendAsBot": True},              # send_channel_message REQUIRES sendAsBot
              "trigger_const": {"ignoreBots": True},     # required trigger input (skip bot messages)
              "dynamic_props": ["channel"]},             # DROPDOWN fed a template → DYNAMIC
}

_HOST = "{{connections.ea_host}}"
_TOKEN = "{{connections.ea_gateway_token}}"


# ---- steps ---------------------------------------------------------------
def invoke_step(agent: str, thread_id: str, prompt: str, *, source_type: str,
                source_name: str, event_kind: str, payload: dict | None = None,
                deliver: bool = False, name: str = "step_1", source_user: str = "") -> dict:
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
    descriptor; gmail is an integration email. Adding a channel = a CHANNELS row, no code here."""
    if channel == "gmail":
        settings = {"pieceName": PIECE["gmail"], "actionName": "send_email",
                    "input": {"receiver": [target], "subject": "Update", "body_type": "plain_text",
                              "body": text}}
    elif channel in CHANNELS:
        d = CHANNELS[channel]
        settings = {"pieceName": PIECE[d["piece"]], "actionName": d["send_action"],
                    "input": {d["target_arg"]: target, d["text_arg"]: text, **d.get("const", {})}}
    else:  # generic fallback
        settings = {"pieceName": PIECE.get(channel, f"@activepieces/piece-{channel}"),
                    "actionName": "send_message", "input": {"channel": target, "text": text}}
    return {"name": name, "displayName": f"{channel.title()} · Send", "type": "PIECE",
            "settings": settings, "nextAction": None}


def router_step(branches: list[dict], name: str = "step_2") -> dict:
    """A ROUTER with condition branches + a FALLBACK. Each branch: {name, match, action|None}.

    ``match`` is a string the worker's answer must start with (case-insensitive); the last
    branch with match=None is the fallback."""
    ap_branches, children = [], []
    for b in branches:
        if b.get("match") is None:
            ap_branches.append({"branchName": b.get("name", "else"), "branchType": "FALLBACK"})
        else:
            ap_branches.append({
                "branchName": b.get("name", b["match"]),
                "branchType": "CONDITION",
                "conditions": [[{"firstValue": "{{step_1.body.answer}}",
                                 "operator": "TEXT_STARTS_WITH",
                                 "secondValue": b["match"], "caseSensitive": False}]],
            })
        children.append(b.get("action"))
    return {"name": name, "displayName": "Router", "type": "ROUTER",
            "settings": {"branches": ap_branches}, "children": children, "nextAction": None}


# ---- triggers ------------------------------------------------------------
def _schedule_trigger(cron: str | None, interval_seconds: int | None, nxt: dict) -> dict:
    if cron:
        settings = {"pieceName": PIECE["schedule"], "triggerName": "cron_expression",
                    "input": {"cronExpression": cron}}
    else:
        settings = {"pieceName": PIECE["schedule"], "triggerName": "every_x_minutes",
                    "input": {"minutes": max(1, int((interval_seconds or 60) / 60))}}
    return {"name": "trigger", "displayName": "Schedule", "type": "PIECE_TRIGGER",
            "settings": settings, "nextAction": nxt}


def _piece_trigger(source: str, nxt: dict, source_input: dict | None = None) -> dict:
    piece, trig = SOURCE_TRIGGER.get(source, (source, "new_item"))
    return {"name": "trigger", "displayName": f"{piece.title()} · {trig}", "type": "PIECE_TRIGGER",
            "settings": {"pieceName": PIECE.get(piece, f"@activepieces/piece-{piece}"),
                         "triggerName": trig, "input": dict(source_input or {})},
            "nextAction": nxt}


# ---- flow builders (one per mode) ---------------------------------------
def build_cron_flow(*, agent: str, thread_id: str, prompt: str, cron: str | None = None,
                    interval_seconds: int | None = None, sink: dict | None = None,
                    display: str = "cron flow") -> dict:
    """CRON: schedule ▸ /invoke ▸ (send). Also used for POLL (see build_poll_flow)."""
    step1 = invoke_step(agent, thread_id, prompt, source_type="time", source_name="cron",
                        event_kind="tick", deliver=sink is None)
    if sink:
        step1["nextAction"] = sink
    return {"displayName": display, "valid": True,
            "trigger": _schedule_trigger(cron, interval_seconds, step1)}


def build_poll_flow(*, agent: str, thread_id: str, prompt: str, cron: str | None = None,
                    interval_seconds: int | None = 900, sink: dict | None = None,
                    display: str = "poll flow") -> dict:
    """POLL: same shape as cron (timer or cron); the worker suppresses no-op runs
    (emit-on-change via get_state/set_state)."""
    f = build_cron_flow(agent=agent, thread_id=thread_id, prompt=prompt, cron=cron,
                        interval_seconds=interval_seconds if not cron else None,
                        sink=sink, display=display)
    f["__mode"] = "poll"  # marker: worker uses get_state/set_state to emit only on change
    return f


def build_runonce_flow(*, agent: str, thread_id: str, prompt: str, sink: dict | None = None,
                       display: str = "run-once flow") -> dict:
    """NOW as a fire-once flow (for uniform run history). Channel-NOW usually rides the
    inbound flow instead — this is for NOW requests that need their own trigger."""
    step1 = invoke_step(agent, thread_id, prompt, source_type="time", source_name="runonce",
                        event_kind="runonce", deliver=sink is None)
    if sink:
        step1["nextAction"] = sink
    return {"displayName": display, "valid": True,
            "trigger": {"name": "trigger", "displayName": "Run once", "type": "PIECE_TRIGGER",
                        "settings": {"pieceName": PIECE["schedule"], "triggerName": "run_once",
                                     "input": {}}, "nextAction": step1}}


def build_push_flow(*, agent: str, thread_id: str, prompt: str, source: str,
                    source_input: dict | None = None, branches: list[dict] | None = None,
                    sink: dict | None = None, event_kind: str = "new_file",
                    display: str = "push flow") -> dict:
    """PUSH: <source>·<event> ▸ /invoke ▸ Router(branches) or send. Powers the resume
    watcher (source='box', branches MATCH→gmail / SKIP→stop) and the PR reviewer."""
    step1 = invoke_step(agent, thread_id, prompt, source_type="integration", source_name=source,
                        event_kind=event_kind,
                        payload={"file_id": "{{trigger.id}}", "name": "{{trigger.name}}"})
    step1["nextAction"] = router_step(branches) if branches else sink
    return {"displayName": display, "valid": True,
            "trigger": _piece_trigger(source, step1, source_input)}


def build_inbound_flow(*, channel: str, agent: str = "concierge",
                       display: str | None = None) -> dict:
    """A standing channel-inbound flow: <channel>·new_message ▸ /invoke ▸ <channel>·send.
    This is how a channel message reaches the concierge (routing). Channel specifics come from
    the CHANNELS descriptor, so Telegram/Discord/Slack are one flow shape — no per-channel code.
    The sender's native id rides in thread_id (``gw:<channel>:<native>``) so /invoke resolves the
    user (decision 0007)."""
    d = CHANNELS.get(channel, CHANNELS["telegram"])
    native, text = d["native_ref"], d["text_ref"]
    step1 = invoke_step(agent, f"gw:{channel}:{native}", text, source_type="channel",
                        source_name=channel, event_kind="message", deliver=False,
                        source_user=d.get("user_ref", ""))
    step1["nextAction"] = send_step(channel, native, "{{step_1.body.answer}}")
    return {"displayName": display or f"{channel}-inbound → {agent}", "valid": True,
            "trigger": _piece_trigger(channel, step1)}


def build_resume_watcher_flow(*, agent: str = "resume_judge", thread_id: str,
                              deliver_to: str = "gmail", target: str = "{{trigger.body.email}}",
                              display: str = "resume watcher (Box → Gmail)") -> dict:
    """The canonical Box PUSH watcher: Box·NewFile ▸ /invoke(resume_judge) ▸ Router⟨MATCH⟩→Gmail /
    ⟨else⟩→stop. A specialization of build_push_flow — the resume use case, clean via Box."""
    return build_push_flow(
        agent=agent, thread_id=thread_id,
        prompt=("A resume landed in Box. Judge fit vs the JD. Start your reply with MATCH or SKIP."),
        source="box", event_kind="new_file", display=display,
        branches=[{"name": "MATCH", "match": "MATCH", "action": send_step(deliver_to, target,
                                                                          "{{step_1.body.answer}}")},
                  {"name": "skip", "match": None, "action": None}])


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
