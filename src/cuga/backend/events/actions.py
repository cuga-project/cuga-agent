"""The ACTION REGISTRY — the single source of truth for every (integration, action) we can RUN as a
post-agent step in a flow. The mirror image of :mod:`triggers`.

Where ``triggers.py`` answers *"what can we WATCH?"*, this answers *"what can we DO when it fires?"*.
The two are deliberately symmetric so the concierge validates actions with the same machine it
validates triggers (accept / ask / reject — never silently arm the wrong thing).

One row = one action. **Adding an action is adding ONE entry here** — rows are generated from the
LIVE Activepieces catalog by ``scripts/gen_actions.py`` (``GET /api/v1/pieces/<piece>``), so a new
piece is DATA, never renderer/flow/resolver code. See events_docs/plans/TRIGGERS_ACTIONS_DESIGN.md.

Field notes
-----------
``backend``    "ap"   → an Activepieces piece action (rendered by ``flows.action_step``).
               "tool" → resolved at provision time to an agent's OWN tool (``resolve_action``); no
                        flow step is emitted — the agent performs it in-run. (No rows ship as
                        "tool" yet; ``resolve_action`` promotes an "ap" row to the tool path when the
                        target agent already carries a matching tool. See design §3.3.)
``params``     the action's input schema, VERIFIED against live AP piece props (types + required).
               Each :class:`Param` carries a ``source`` hint so the compiler knows how to FILL it:
                 "answer"  → the agent's reply           ``{{step_1.body.answer}}``
                 "trigger" → a field on the firing event ``{{trigger.<path>}}`` (``Param.template``)
                 "static"  → a fixed default             (``Param.default``)
                 "user"    → must be supplied by the user (a SLOT the concierge asks for)
``destructive`` deletes / trashes / archives / overwrites. Gates approval (design §3.4b): a
               destructive action can NEVER arm on ``ambiguous`` confidence and gets a run-time
               approval step compiled into the flow.
``phrases``    regex fragments the concierge/classifier use to map an utterance VERB to this action
               (the verb-alignment check that stops "send email" compiling to "delete email").

Keep this module dependency-free (stdlib only) — like ``triggers``, it is imported by the
LLM-free deterministic half of NL→Flow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Param:
    name: str
    type: str = "SHORT_TEXT"          # the AP prop type (SHORT_TEXT/ARRAY/CHECKBOX/…)
    required: bool = False
    source: str = "answer"            # answer | trigger | static | user
    template: str = ""                # for source="trigger": the {{trigger.<path>}} template
    default: object = None            # for source="static": the fixed value
    question: str = ""                # for source="user": what the concierge asks when missing
    array: bool = False               # wrap the resolved value in a list (AP ARRAY props)


@dataclass(frozen=True)
class Action:
    app: str                          # "gmail", "github", "box", "slack", …
    name: str                         # OUR canonical action id ("send_email", "reply_to_email")
    title: str                        # human name (mirrors the AP displayName)
    backend: str = "ap"               # "ap" | "tool"
    piece: str = ""                   # AP piece key (ap backend)
    ap_action: str = ""               # AP actionName (ap backend)
    params: tuple = ()                # tuple[Param]
    destructive: bool = False
    phrases: tuple = ()               # verb regex fragments (verb-alignment)
    default: bool = False             # the app's default action
    # raw_input: for custom_api_call-backed actions (capabilities the piece has no native action for,
    # e.g. gmail archive/trash/mark-read via the Gmail REST API). When set, `action_step` uses it
    # verbatim as the AP `input` (native {{templates}} resolved by AP), bypassing param rendering.
    raw_input: dict | None = None
    # same_app_trigger: this action keys off the FIRING event (e.g. reply/draft need the trigger's
    # message id), so it is only valid downstream of a trigger from the SAME app. The gate rejects a
    # cross-app pairing (a Box trigger can't drive gmail/reply_to_email — there's no message to reply
    # to). Cross-app actions like send_email leave this False.
    same_app_trigger: bool = False
    notes: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.app, self.name)


def _a(*a, **kw) -> Action:
    return Action(*a, **kw)


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Gmail — NATIVE actions only (design D1). The live piece (@activepieces/piece-gmail, probed
# 2026-07-18) exposes send_email, reply_to_email, create_draft_reply as the mutating actions;
# it has NO archive/label/delete — those need custom_api_call, deliberately OUT of the pilot.
# The reply/draft actions key off the FIRING email's id: the gmail triggers put the message under
# {{trigger.message.*}} (see triggers.py _GMAIL), so message_id = {{trigger.message.id}}.
# ─────────────────────────────────────────────────────────────────────────────────────────────────
_GM = dict(app="gmail", backend="ap", piece="gmail")

_GMAIL = [
    _a(name="send_email", title="Send Email", ap_action="send_email", default=True,
       params=(
           # ALL props declared — AP's send_email validator requires every prop present (optionals as
           # typed empties, filled by render_params). receiver/cc/subject can be supplied; the rest
           # default to empty/false.
           Param("receiver", "ARRAY", required=True, source="user", array=True,
                 question="Who should I email? Give an address (or say 'the sender' to reply to them)."),
           Param("cc", "ARRAY", required=False, source="user", array=True),
           Param("bcc", "ARRAY", required=False, source="user", array=True),
           Param("subject", "SHORT_TEXT", required=True, source="static", default="Update"),
           Param("body_type", "STATIC_DROPDOWN", required=True, source="static", default="plain_text"),
           Param("body", "SHORT_TEXT", required=True, source="answer"),
           Param("reply_to", "ARRAY", required=False, source="user", array=True),
           Param("sender_name", "SHORT_TEXT", required=False, source="user"),
           Param("from", "SHORT_TEXT", required=False, source="user"),
           Param("attachments", "ARRAY", required=False, source="user", array=True),
           Param("in_reply_to", "SHORT_TEXT", required=False, source="user"),
           Param("draft", "CHECKBOX", required=True, source="static", default=False),
       ),
       phrases=(r"\bsend\b.{0,15}\be-?mail\b", r"\be-?mail (me|us|him|her|them|to)\b",
                r"\be-?mail\b.{0,15}\b(the\s+)?(sender|them|him|her)\b",
                r"\bnotify\b.{0,15}\bemail\b", r"\bmail (me|us)\b"),
       notes="receiver defaults to a user slot; the concierge fills it with the trigger sender "
             "({{trigger.message.from.value[0].address}}) when the utterance says 'reply/them/sender'.",
       **_GM),
    _a(name="reply_to_email", title="Reply to Email", ap_action="reply_to_email",
       params=(
           Param("message_id", "SHORT_TEXT", required=True, source="trigger",
                 template="{{trigger.message.id}}"),
           Param("body", "LONG_TEXT", required=True, source="answer"),
           Param("reply_type", "STATIC_DROPDOWN", required=True, source="static", default="reply"),
           Param("body_type", "STATIC_DROPDOWN", required=True, source="static", default="plain_text"),
       ),
       # bare \breply\b covers "reply confirming receipt"; a "draft a reply" utterance hits the
       # LONGER create_draft_reply phrase first (classifier_actions sorts by -len), so draft wins.
       phrases=(r"\breply\b", r"\brespond\b.{0,15}\be-?mail\b",
                r"\breply to (it|the e-?mail|them|the sender)\b", r"\bauto-?reply\b"),
       same_app_trigger=True,
       notes="only valid downstream of a gmail trigger (needs the firing message id).",
       **_GM),
    _a(name="create_draft_reply", title="Create Draft Reply", ap_action="create_draft_reply",
       params=(
           Param("message_id", "SHORT_TEXT", required=True, source="trigger",
                 template="{{trigger.message.id}}"),
           Param("body", "LONG_TEXT", required=False, source="answer"),
           Param("reply_type", "STATIC_DROPDOWN", required=True, source="static", default="reply"),
           Param("body_type", "STATIC_DROPDOWN", required=True, source="static", default="plain_text"),
           Param("include_original_message", "CHECKBOX", required=True, source="static", default=True),
       ),
       # tightened: a bare "draft" false-matched "draft PR". Require reply/response context.
       phrases=(r"\bdraft (a |me a )?(reply|response)\b", r"\bprepare a (reply|response)\b",
                r"\bcreate a draft\b", r"\bdraft a repl"),
       same_app_trigger=True,
       notes="prepares a draft (no send) — the safest Gmail action; downstream of a gmail trigger.",
       **_GM),
]


# ── Gmail capabilities the piece has NO native action for → custom_api_call over the Gmail REST API.
# message id comes from the firing trigger (same_app_trigger). System labels (INBOX/UNREAD) need no
# lookup, so archive/mark-read/trash work without resolving a label id. Add-label needs a label-id
# lookup (a user slot) — deliberately left out of the golden set (documented). Base URL is the Gmail
# API; AP attaches the gmail OAuth token as the step's connection auth (see ap_engine._action_op).
_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me/messages"

# custom_api_call needs EVERY prop present (like send_email). These bases carry them all.
def _capi(url: str, body=None) -> dict:
    d = {"url": url, "method": "POST", "headers": {}, "queryParams": {},
         "response_is_binary": False, "failsafe": False, "timeout": "", "followRedirects": False}
    if body is not None:
        d["body_type"] = "json"
        d["body"] = body
    else:
        d["body_type"] = "none"
    return d


_MID = "{{trigger.message.id}}"
_GMAIL_RAW = [
    _a(name="archive_email", title="Archive Email (remove from inbox)", ap_action="custom_api_call",
       same_app_trigger=True,
       raw_input=_capi(f"{_GMAIL_API}/{_MID}/modify", {"removeLabelIds": ["INBOX"]}),
       phrases=(r"\barchive\b",),
       notes="reversible (email kept, just out of inbox) — not flagged destructive.", **_GM),
    _a(name="mark_read", title="Mark as Read", ap_action="custom_api_call", same_app_trigger=True,
       raw_input=_capi(f"{_GMAIL_API}/{_MID}/modify", {"removeLabelIds": ["UNREAD"]}),
       phrases=(r"\bmark(ed)?\b.{0,10}\bread\b",), **_GM),
    _a(name="trash_email", title="Delete (move to Trash)", ap_action="custom_api_call",
       destructive=True, same_app_trigger=True,
       raw_input=_capi(f"{_GMAIL_API}/{_MID}/trash"),
       phrases=(r"\b(delete|trash|bin)\b.{0,15}\b(e-?mail|it|message|them)\b",
                r"\btrash it\b", r"\bmove.{0,10}trash\b"),
       notes="DESTRUCTIVE — approval-gated at run time.", **_GM),
]

ACTIONS: dict[tuple[str, str], Action] = {a.key: a for a in (_GMAIL + _GMAIL_RAW)}

# Canonical-name aliases the LLM/classifier may emit for an action.
ACTION_ALIASES = {
    ("gmail", "send"): "send_email",
    ("gmail", "email"): "send_email",
    ("gmail", "reply"): "reply_to_email",
    ("gmail", "draft"): "create_draft_reply",
    ("gmail", "draft_reply"): "create_draft_reply",
    ("gmail", "archive"): "archive_email",
    ("gmail", "delete"): "trash_email",
    ("gmail", "trash"): "trash_email",
    ("gmail", "delete_email"): "trash_email",
    ("gmail", "mark_as_read"): "mark_read",
}


# ── lookup / introspection (mirrors triggers.py) ────────────────────────────────────────────────
def rows() -> list:
    return list(ACTIONS.values())


def get(app: str, name: str = "") -> "Action | None":
    """Look up an action; resolves aliases and the app default. None if unknown."""
    app = (app or "").lower().strip()
    name = (name or "").lower().strip().replace("-", "_")
    name = ACTION_ALIASES.get((app, name), name)
    if name and (app, name) in ACTIONS:
        return ACTIONS[(app, name)]
    if not name:
        return next((a for a in ACTIONS.values() if a.app == app and a.default), None)
    return None


def apps() -> list:
    return sorted({a.app for a in ACTIONS.values()})


def actions_for(app: str) -> list:
    return [a for a in ACTIONS.values() if a.app == (app or "").lower()]


def action_names() -> tuple:
    return tuple(sorted({a.name for a in ACTIONS.values()}))


def validate(app: str, name: str, params: dict | None = None) -> tuple:
    """The arm-time action gate: (action, "") when armable, (None|action, problem) otherwise.

    Mirrors :func:`triggers.validate`. Called BEFORE anything is built. A required param that is
    ``source="user"`` and not supplied comes back as a QUESTION; auto-filled params (answer/trigger/
    static) are never asked about. An unknown action is a hard error (never a silent wrong action)."""
    a = get(app, name)
    if a is None:
        known = ", ".join(x.name for x in actions_for((app or "").lower())) or "none"
        return None, f"unknown action '{app}/{name}' — known actions for {app}: {known}"
    cfg = params or {}
    for p in a.params:
        if p.required and p.source == "user" and not cfg.get(p.name):
            return a, (p.question or f"What value should I use for '{p.name}'?")
    return a, ""


def render_params(action: "Action", supplied: dict | None = None) -> dict:
    """Resolve an action's params into the AP ``input`` dict of {{templates}} / literals.

    ``supplied`` overrides a param's source (e.g. the concierge resolved ``receiver`` to the trigger
    sender, or the user gave a literal address). Precedence: supplied value > trigger template >
    static default > answer template. ARRAY props are wrapped in a list. The result is native AP —
    AP interpolates ``{{trigger.*}}`` / ``{{step_1.body.answer}}`` at run time (no Python resolver)."""
    supplied = supplied or {}
    ANSWER = "{{step_1.body.answer}}"
    out: dict = {}
    for p in action.params:
        if p.name in supplied and supplied[p.name] is not None:
            val = supplied[p.name]
        elif p.source == "trigger":
            val = p.template
        elif p.source == "static":
            val = p.default
        elif p.source == "answer":
            val = ANSWER
        else:  # user slot with no supplied value → a TYPED EMPTY (AP's send_email validator wants
               # EVERY declared prop present, even optional ones; omitting them made the step invalid).
            val = [] if p.array else (False if p.type == "CHECKBOX" else "")
        if p.array and not isinstance(val, list) and val is not None:
            val = [val]
        out[p.name] = val
    return out


def prompt_vocabulary() -> str:
    """A compact app→actions vocabulary for the concierge system prompt (generated, never drifts)."""
    parts = []
    for app in apps():
        acts = []
        for a in sorted(actions_for(app), key=lambda x: (not x.default, x.name)):
            mark = "*" if a.default else ""
            risk = "!" if a.destructive else ""
            acts.append(f"{a.name}{mark}{risk}")
        parts.append(f"{app}: {', '.join(acts)}")
    return " · ".join(parts)


def classifier_actions() -> list:
    """(regex, (app, name)) pairs for verb-alignment / deterministic action hints, specific first."""
    out = []
    for a in ACTIONS.values():
        for ph in a.phrases:
            out.append((ph, (a.app, a.name), a.default))
    out.sort(key=lambda p: (p[2], -len(p[0])))
    return [(ph, an) for ph, an, _ in out]


def extract_action(utterance: str) -> tuple:
    """Deterministically detect a post-agent ACTION in an utterance — the generic on-ramp that wires
    the concierge's fast-path (and slash, and the LLM when it omits one) to the action gate.

    Registry-driven (matches ``classifier_actions`` phrases, most-specific first), so a NEW piece's
    actions are detectable with ZERO concierge code — just phrases on the row. Returns
    ``(action_id, action_to)``: ``action_id`` like 'gmail/reply_to_email' or None; ``action_to`` is
    the send recipient hint for send_email (an address, or 'sender'/'me'), else None.

    Deliberately NARROW: only real action verbs match (send/reply/draft/email-me). Plain delivery
    ('message me', 'ping me', 'notify me', 'tell me') matches nothing → a plain watcher, as before.
    """
    import re
    text = utterance or ""
    hit = None
    for ph, (app, name) in classifier_actions():
        if re.search(ph, text, re.I):
            hit = (app, name)
            break
    if hit is None:
        return None, None
    app, name = hit
    return f"{app}/{name}", _send_recipient(text) if name == "send_email" else None


def _send_recipient(text: str):
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)              # an explicit address wins
    if m:
        return m.group(0)
    if re.search(r"\b(the\s+)?(sender|them|him|her)\b", text, re.IGNORECASE):
        return "sender"
    if re.search(r"\b(me|us)\b", text, re.IGNORECASE):
        return "me"                                              # gate will ask for the address
    return None


# Action VERBS we recognize the user is asking for but have NO registry action for (label/forward/
# star/…). Matching the ACTION form ("label it") — NOT the trigger form ("when I label an email").
# Used to DECLINE honestly instead of silently arming a plain watcher (the benchmark's silent-failure
# catch). Kept narrow so plain delivery ("message me", "tell me") never trips it.
_UNSUPPORTED = re.compile(
    r"\b(label|star|flag|snooze|pin|mark)\s+(it|them|this|the\s+(e-?mail|message))\b"
    r"|\bforward\s+(it|this|the\s+(e-?mail|message))\b"
    r"|\bmove\s+(it|the\s+(e-?mail|message))\s+to\b", re.IGNORECASE)


def unsupported_action(utterance: str) -> str:
    """If the utterance asks for an action VERB we don't support (and no registry action matched),
    return that verb so the concierge can decline honestly. '' when nothing unsupported is asked."""
    m = _UNSUPPORTED.search(utterance or "")
    if not m:
        return ""
    return m.group(0).split()[0].lower()


def extract_actions(utterance: str) -> list:
    """Multi-action: detect ALL distinct actions an utterance asks for, in order (e.g. "summarize,
    email me AND archive it" → [send_email, archive_email]). Returns a list of
    ``{"action": "<app>/<name>", "action_to": <hint|None>}``; empty when none. Each registry action
    matches at most once. Order follows first appearance in the text so the flow chains them
    sensibly. Powers the concierge's sequential multi-action arming."""
    import re
    text = utterance or ""
    seen, hits, consumed = set(), [], []
    # classifier_actions is most-specific-first (longer phrases). Consume each match's span so a
    # shorter overlapping phrase can't double-count — "draft a reply" is ONE action (draft), not
    # draft + reply; two NON-overlapping verbs ("email me AND archive it") stay two actions.
    for ph, (app, name) in classifier_actions():
        if (app, name) in seen:
            continue
        m = re.search(ph, text, re.IGNORECASE)
        if not m:
            continue
        if any(not (m.end() <= s or m.start() >= e) for s, e in consumed):
            continue
        seen.add((app, name))
        hits.append((m.start(), app, name))
        consumed.append((m.start(), m.end()))
    hits.sort()                                                   # first-appearance order
    out = []
    for _pos, app, name in hits:
        out.append({"action": f"{app}/{name}",
                    "action_to": _send_recipient(text) if name == "send_email" else None})
    return out


# ── resolve_action: tool-first, AP-fallback (design §3.3) ───────────────────────────────────────
def resolve_action(app: str, name: str, agent_tool_names: list | None = None) -> tuple:
    """Decide HOW an action runs: ('tool', tool_name) | ('ap', Action) | ('ask', problem).

    Tool-first: if the target agent already carries a tool that performs this action, the agent does
    it in-run (no flow step). Else fall back to the AP action step. Pure — the caller injects the
    agent's bound tool names (from ``tools_bridge``), so this is unit-testable without a live agent."""
    a = get(app, name)
    if a is None:
        known = ", ".join(x.name for x in actions_for((app or "").lower())) or "none"
        return "ask", f"unknown action '{app}/{name}' — known: {known}"
    tool = _match_tool(a, agent_tool_names or [])
    if tool:
        return "tool", tool
    return "ap", a


def _match_tool(action: "Action", tool_names: list) -> str:
    """Match an action to an agent tool by name/alias. Generic (no per-piece code): compares the
    action id, its app-qualified id, and its aliases against the agent's tool names."""
    cands = {action.name, f"{action.app}_{action.name}", f"{action.app}.{action.name}",
             action.ap_action}
    for (a_app, alias), canon in ACTION_ALIASES.items():
        if a_app == action.app and canon == action.name:
            cands.add(alias)
            cands.add(f"{action.app}_{alias}")
    norm = {t.lower().replace("-", "_") for t in tool_names}
    for c in cands:
        if c and c.lower().replace("-", "_") in norm:
            return c
    return ""
