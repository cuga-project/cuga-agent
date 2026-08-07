"""HITL arming — nothing is armed until the human has approved the exact prompt.

WHY THIS EXISTS
---------------
Arming used to be one-shot: "/automate every 5 minutes send IBM stock price" was classified,
defaulted (delivery target, cadence), and armed **verbatim and unseen**. The user never saw the
instruction the agent would be handed every 5 minutes, so a vague prompt became a vague fire —
forever, silently. Since fire time has no human to ask (see the spec's descoped fire-time gaps),
the arm is where trust is earned.

THE DIALOGUE
------------
    DRAFT ──▶ NEEDS_INPUT ──▶ CONFIRM ──▶ ARMED
                 ▲   │           │  │
                 └───┘        edit│  └─ "yes" → persist
              (one field at       └────┐
               a time)   "/cancel" or TTL → CANCELLED

``CONFIRM`` is the gate: it renders the composed fire-time prompt back to the user and requires an
explicit **yes**. ``edit`` reopens a field (prompt / schedule / delivery) and re-confirms.

STATE lives in the events DB (``pending_arm``, see subscriptions.py), keyed by the principal-scoped
thread — so a restart or redeploy no longer drops an in-flight arm, and a second surface (Studio,
a channel) sees the same dialogue. It is read WITHOUT consuming: only an explicit transition
(answer / confirm / cancel / expiry) clears it, so an innocent chat message can't destroy an arm.

The structured ``{state, question, summary, subscription_id}`` reply rides a contextvar (the same
idiom as runmeta.py), so ``Concierge.run`` keeps returning human text while /invoke and
/api/concierge can surface the machine-readable state that drives edge stickiness and the UI.
"""

from __future__ import annotations

import contextvars
import logging
import os
import re

log = logging.getLogger("events.arming")

ARM_TTL_SECS = 10 * 60

# The four states a caller can observe. needs_input|confirm keep the thread sticky to the eventing
# layer (the edge routes the next message here, not to chat); armed|cancelled release it.
NEEDS_INPUT = "needs_input"
CONFIRM = "confirm"
ARMED = "armed"
CANCELLED = "cancelled"
STICKY = (NEEDS_INPUT, CONFIRM)

_state: contextvars.ContextVar[dict | None] = contextvars.ContextVar("arm_state", default=None)


def set_state(
    state: str, *, question: str = "", summary: dict | None = None, subscription_id: str = ""
) -> None:
    """Record the structured arming state for this request (routes read it after run())."""
    _state.set(
        {
            "state": state,
            "question": question or "",
            "summary": summary or None,
            "subscription_id": subscription_id or "",
        }
    )


def get() -> dict | None:
    return _state.get()


def reset() -> None:
    _state.set(None)


# ── reading the user's reply at the CONFIRM gate ────────────────────────────────────────────────
_YES = {
    "y",
    "yes",
    "yeah",
    "yep",
    "yup",
    "ok",
    "okay",
    "sure",
    "confirm",
    "confirmed",
    "arm",
    "arm it",
    "go",
    "go ahead",
    "do it",
    "approve",
    "approved",
    "looks good",
    "lgtm",
    "send it",
}
_NO = {
    "n",
    "no",
    "nope",
    "cancel",
    "stop",
    "abort",
    "nevermind",
    "never mind",
    "forget it",
    "don't",
    "dont",
    "no thanks",
}

# "change the prompt to X" / "edit prompt: X" / "prompt = X" — the field, then the new value.
# NB: every `\s`-run below is ` ?` (at most one space), never `\s*`/`\s+`. These patterns are applied
# to `_one_space()`-normalised text, so a single space is all that can occur — and the optional groups
# between them no longer give the engine two ways to consume the same run of whitespace, which is what
# made the originals quadratic on space-heavy input (CodeQL py/polynomial-redos). Same phrases match.
_EDIT_RX = re.compile(
    r"^(?:/edit )?(?:edit|change|update|set|make)? ?(?:the )?"
    r"(prompt|instruction|schedule|cadence|interval|delivery|destination|target|sink) ?"
    r"(?:to|=|:|as)? ?(.+)$",
    re.I | re.S,
)

_FIELD_ALIAS = {
    "instruction": "prompt",
    "cadence": "schedule",
    "interval": "schedule",
    "destination": "delivery",
    "target": "delivery",
    "sink": "delivery",
}


def read_reply(text: str) -> tuple[str, str, str]:
    """Interpret a reply at the CONFIRM gate → ``(action, field, value)``.

    action ∈ ``yes`` | ``cancel`` | ``edit`` | ``unclear``. ``unclear`` is deliberate: we re-ask
    rather than guess, because guessing "yes" arms something the user never approved.
    """
    t = _one_space(text)  # single-space normal form — the precondition _EDIT_RX is written against
    if not t:
        return "unclear", "", ""
    low = t.lower().strip(" .!?")
    if low in _YES:
        return "yes", "", ""
    if low in _NO or low.startswith("/cancel"):
        return "cancel", "", ""
    m = _EDIT_RX.match(t)
    if m:
        field = _FIELD_ALIAS.get(m.group(1).lower(), m.group(1).lower())
        value = (m.group(2) or "").strip().strip("\"'")
        if value:
            return "edit", field, value
    # A bare cadence ("every 10 minutes") reads as a schedule edit — a common natural correction.
    if re.match(r"^\s*every\s+\w+", t, re.I):
        return "edit", "schedule", t
    return "unclear", "", ""


# ── composing the fire-time prompt ──────────────────────────────────────────────────────────────
_CADENCE_STRIP = re.compile(
    r"\b(every|each) (\d+|one|two|three|four|five|six|seven|eight|nine|ten|half)? ?"
    r"(second|sec|minute|min|hour|hr|day|week|weekday|morning|evening|night|monday|tuesday|"
    r"wednesday|thursday|friday|saturday|sunday)s?\b( at [\d:apm\.]+)?",
    re.I,
)
_DELIVERY_STRIP = re.compile(
    r"\b(and )?(send|message|dm|post|notify|tell|ping|email) (me|us|it|them)? ?"
    r"(on|to|via|in)? ?(slack|telegram|discord|whatsapp|email|web|here|this chat)?\b",
    re.I,
)


def _one_space(s: str) -> str:
    """Collapse every whitespace run to a single space. Cheap, linear, and the precondition the
    patterns above are written against — call it before matching user text with them."""
    return re.sub(r"\s+", " ", s or "").strip()


def compose_prompt(utterance: str, kind: str = "cron") -> str:
    """The instruction the agent gets on EVERY fire.

    Deterministic by default: strip the scheduling and delivery scaffolding (they are the
    subscription's job, not the agent's) and leave the task. "every 5 minutes send IBM stock
    price" → "send IBM stock price" → "Send IBM stock price." The user sees and can edit this,
    which is the real guarantee — composition only has to be a good starting point.

    EVENTS_ARM_COMPOSE_LLM=1 additionally asks the model to sharpen it; any failure falls back to
    the deterministic text, so this never becomes a way for arming to break.
    """
    t = _one_space(utterance)  # precondition for the two STRIP patterns (single-space normal form)
    core = _CADENCE_STRIP.sub(" ", t)
    core = _DELIVERY_STRIP.sub(" ", core)
    core = re.sub(r"\s+", " ", core).strip(" ,.;:-—and").strip()
    if not core:
        core = t  # the whole utterance WAS the cadence — keep it
    core = core[0].upper() + core[1:] if core else core
    if not core.endswith((".", "?", "!")):
        core += "."
    if os.environ.get("EVENTS_ARM_COMPOSE_LLM") == "1":
        try:
            core = _llm_compose(t, core, kind) or core
        except Exception as e:  # noqa: BLE001 — composition is a nicety, arming is not
            log.warning("prompt composition via LLM failed (%s) — using the deterministic text", e)
    return core


def _llm_compose(utterance: str, fallback: str, kind: str) -> str:
    from .llm import default_model_factory

    model = default_model_factory()
    msg = (
        "Rewrite the user's automation request as a single, self-contained instruction that an "
        "agent will execute on a schedule. Keep it one sentence. Do NOT mention the schedule or "
        "where to send it — those are handled separately. Resolve obvious ambiguity (e.g. name the "
        "exchange for a stock ticker). Reply with the instruction only.\n\n"
        f"Request: {utterance}"
    )
    out = model.invoke(msg)
    text = (getattr(out, "content", None) or str(out) or "").strip().strip('"')
    return text if 0 < len(text) <= 400 else fallback


# ── validation: CONFIRM only ever shows an ARMABLE spec ─────────────────────────────────────────
def validate(parsed: dict, origin_thread: str = "") -> tuple[str, str]:
    """``(question, field)`` when something required is missing — else ``("", "")``.

    This is what widens clarification beyond PUSH slots: a cron/poll with no detectable cadence
    used to arm on a silent default. Now it asks."""
    kind = (parsed or {}).get("kind") or ""
    if kind in ("cron", "poll"):
        try:
            from . import classify
        except ImportError:  # flat load (offline tests)
            import classify
        cad = classify.cadence_of(parsed.get("utterance") or "")
        if not cad or not (cad.get("interval_seconds") or cad.get("cron")):
            return (
                "How often should this run? e.g. `every 5 minutes`, `every hour`, or `every weekday at 9am`.",
                "schedule",
            )
    if kind == "push" and not parsed.get("source"):
        return ("What should I watch? Name the app — e.g. github, gmail, box, or slack.", "source")
    return "", ""


# ── the human-facing confirmation card ──────────────────────────────────────────────────────────
def describe_delivery(origin_thread: str) -> str:
    """Where results will land, in the user's words. Mirrors the sink the arm will store: a
    channel-originated arm replies into that same conversation, and a web arm now does too — a
    browser cannot be pushed to, so the fire is delivered to a durable per-thread mailbox this
    chat drains (see web_inbox). It used to say "your Runs inbox", which was accurate when the
    answer went only to the runs log, and became a lie the moment delivery existed."""
    try:
        from .principal import channel_origin
    except ImportError:  # flat load
        from principal import channel_origin  # type: ignore
    try:
        origin = channel_origin(origin_thread or "")
    except Exception:  # noqa: BLE001
        origin = None
    if origin and origin[0]:
        return f"this {origin[0]} conversation"
    return "this chat (and the Runs tab)"


def describe_trigger(parsed: dict) -> str:
    kind = (parsed or {}).get("kind") or ""
    utter = (parsed or {}).get("utterance") or ""
    if kind in ("cron", "poll"):
        try:
            from . import classify
        except ImportError:  # flat load
            import classify
        cad = classify.cadence_of(utter) or {}
        secs = cad.get("interval_seconds")
        if secs:
            if secs % 3600 == 0 and secs >= 3600:
                return f"every {secs // 3600} hour(s)"
            if secs % 60 == 0:
                return f"every {secs // 60} minute(s)"
            return f"every {secs} second(s)"
        if cad.get("cron"):
            return f"on schedule `{cad['cron']}`"
        return "on a schedule"
    src = (parsed or {}).get("source") or "an integration"
    ev = (parsed or {}).get("event") or "a new event"
    return f"when {src} sees {ev}"


def summarize(parsed: dict, prompt: str, origin_thread: str, agent: str) -> dict:
    return {
        "trigger": describe_trigger(parsed),
        "prompt": prompt,
        "delivery": describe_delivery(origin_thread),
        "agent": agent,
    }


def render_card(summary: dict) -> str:
    """The CONFIRM card. Plain text on purpose — it has to read well in Slack, Telegram, Discord
    and the web chat alike."""
    return (
        f"**Ready to arm — check this first.**\n"
        f"• **When:** {summary.get('trigger')}\n"
        f"• **The agent will be asked:** “{summary.get('prompt')}”\n"
        f"• **Results go to:** {summary.get('delivery')}\n"
        f"• **Agent:** {summary.get('agent')}\n\n"
        f"Reply **yes** to arm · **change the prompt to …** (or schedule / delivery) to edit · "
        f"**cancel** to drop it."
    )
