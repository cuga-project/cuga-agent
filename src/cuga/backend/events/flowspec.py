"""FlowSpec — the typed intermediate between natural language and an armed flow.

The NL→Flow contract in one sentence: **an utterance either resolves to exactly the right flow, or
to a question — never silently to the wrong flow.**

``resolve(text)`` runs the deterministic pipeline (classifier → slot extraction → registry
validation) and reports its own confidence honestly:

  * ``high``    — one distinct app matched and the registry validated the spec: armable without an
                  LLM (or ask-able: ``spec.ask`` carries the ONE question that completes it).
  * ``ambiguous`` — flow-shaped, but several apps matched or none did; an LLM (which sees the full
                  vocabulary) must decide. The resolver NEVER guesses through this.
  * ``none``    — not flow-shaped at all (answer-now / conversation).

The cardinal rule, and what the benchmark gates on: a ``high``-confidence spec that is WRONG is a
critical failure — a user silently armed the wrong watcher. Declining to ``high`` is always safe;
it just falls back to the LLM path that handles every utterance today.

``pending``/``fill`` implement ask-till-legit: when arming stops on a missing slot, the question and
the partial spec are parked per conversation thread; the user's next message is first tried as the
answer. A reply that doesn't parse as the answer (or that reads as a new request) drops the pending
spec and routes normally — the user is never trapped in a slot prompt.

Stdlib only. The LLM never appears in this module — it is the deterministic HALF of NL→Flow.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

try:
    from . import classify, triggers
except ImportError:  # flat load (offline tests put the events dir on sys.path)
    import classify          # type: ignore
    import triggers          # type: ignore


@dataclass
class FlowSpec:
    kind: str = ""                      # "push" | "cron" | "poll" | "now" | ""
    source: str = ""                    # integration app (push)
    event: str = ""                     # canonical trigger event (push)
    config: dict = field(default_factory=dict)   # slot values (repo/label/folder/…)
    cadence: dict = field(default_factory=dict)  # {"interval_seconds": n} | {"cron": "…"}
    confidence: str = "none"            # "high" | "ambiguous" | "none"
    ask: str = ""                       # the ONE question that completes a high-confidence spec
    missing: str = ""                   # which slot the ask is for
    candidates: tuple = ()              # (app, event) hits, best first (ambiguous diagnostics)

    @property
    def armable(self) -> bool:
        return self.confidence == "high" and self.kind == "push" and not self.ask


# ── slot extraction — language in, values out ────────────────────────────────────────────────────
# One extractor per slot. Quoted values always win (the user telling us the exact string); the
# unquoted fallbacks are guarded so filler words never become config ("label an email" must not
# yield label="an email" — that exact bug shipped a garbage value to Gmail's trigger).
_QUOTED = r"['\"‘’“”]([\w][\w .\-/]{0,38})['\"‘’“”]"
_STOP = {"an", "a", "the", "my", "this", "that", "it", "email", "emails", "message", "messages",
         "is", "was", "gets", "new", "file", "files", "in", "on", "with"}


def _x_repo(t: str) -> str:
    m = re.search(r"\b([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)\b", t)
    if m and "." not in m.group(0).split("/")[0]:       # skip URLs ("github.com/foo") + filenames
        return m.group(0)
    return ""


def _x_label(t: str) -> str:
    m = re.search(_QUOTED, t)
    if m:
        return m.group(1).strip()
    m = re.search(r"label(?:ed)?\s+(?:as\s+|it\s+)?([\w-]{2,30})", t, re.I)
    if m and m.group(1).lower() not in _STOP:
        return m.group(1)
    return ""


def _x_channel(t: str) -> str:
    m = re.search(r"#([\w-]{2,40})", t)
    return m.group(1) if m else ""


def _x_emoji(t: str) -> str:
    m = re.search(r":([a-z0-9_+\-]{2,40}):", t, re.I)
    if m:
        return m.group(1)
    m = re.search(r"\b(?:a|an|the)\s+([\w+\-]{2,30})\s+(?:reaction|emoji)\b", t, re.I)
    if m and m.group(1).lower() not in _STOP:
        return m.group(1)
    return ""


def _x_folder(t: str) -> str:
    m = re.search(r"\bfolder\s+(?:id\s+)?(\d{3,20})\b", t, re.I)
    return m.group(1) if m else ""


def _x_pattern(t: str) -> str:
    m = re.search(_QUOTED, t)
    return m.group(1).strip() if m else ""


def _x_yt_channel(t: str) -> str:
    """YouTube channel: an @handle, a youtube.com URL, or a raw UC… channel id."""
    m = re.search(r"(https?://(?:www\.)?youtube\.com/[^\s,]+)", t, re.I)
    if m:
        return m.group(1).rstrip(".,;")
    m = re.search(r"(@[A-Za-z0-9_.-]{2,40})", t)          # @Fireship
    if m:
        return m.group(1)
    m = re.search(r"\b(UC[0-9A-Za-z_-]{20,24})\b", t)     # raw channel id
    return m.group(1) if m else ""


def _x_board(t: str) -> str:
    """Pinterest board id — a long numeric id (optionally after 'board')."""
    m = re.search(r"\bboard\s+(?:id\s+)?(\d{6,25})\b", t, re.I) or re.search(r"\b(\d{9,25})\b", t)
    return m.group(1) if m else ""


def _x_calendar(t: str) -> str:
    """Calendar id — 'primary', a quoted name, or an email-style calendar id."""
    if re.search(r"\bprimary\b", t, re.I):
        return "primary"
    m = re.search(r"\bcalendar\s+(?:id\s+)?([\w.+-]+@[\w.-]+\.\w+)", t, re.I)
    return m.group(1) if m else ""


def _x_rss(t: str) -> str:
    """RSS feed URL — any http(s) URL in the utterance."""
    m = re.search(r"(https?://[^\s,]+)", t, re.I)
    return m.group(1).rstrip(".,;") if m else ""


_EXTRACT = {"repo": _x_repo, "label": _x_label, "channel": _x_channel,
            "emoji": _x_emoji, "folder": _x_folder, "pattern": _x_pattern,
            "yt_channel": _x_yt_channel, "board": _x_board, "calendar": _x_calendar,
            "rss_feed_url": _x_rss}


def extract_slots(app: str, event: str, text: str) -> dict:
    """Pull the trigger's slot values out of the utterance — only slots THIS trigger declares."""
    row = triggers.get(app, event)
    if row is None:
        return {}
    out = {}
    for name in row.slots:
        v = _EXTRACT.get(name, lambda _t: "")(text or "")
        if v:
            out[name] = v
    return out


# ── the deterministic resolver ───────────────────────────────────────────────────────────────────
# HIGH confidence demands an explicit standing-flow marker. classify's PUSH heuristic is loose on
# purpose (the LLM tolerates false positives; "summarize the OPEN PRs" matches `opens?`) — but the
# fast path ACTS on its answer, so it holds itself to a stricter bar and defers everything else.
_STRONG = re.compile(r"\b(when(ever)?|every ?time|each time|any ?time|as soon as|if)\b", re.I)

# One marker set per app. An utterance that carries ANOTHER app's marker ("…my repo or my inbox…")
# is genuinely ambiguous even when only one app's trigger phrases matched — never fast-path it.
_MARKERS = {
    "github": re.compile(r"\b(github|repo(sitory)?|pull request|PRs?|issues?|commits?)\b", re.I),
    "gmail": re.compile(r"\b(gmail|email|inbox)\b", re.I),
    "box": re.compile(r"\bbox\b", re.I),
    "slack": re.compile(r"\bslack\b", re.I),
    "discord": re.compile(r"\bdiscord\b", re.I),
    "telegram": re.compile(r"\btelegram\b", re.I),
    "google_calendar": re.compile(r"\b(google )?calendar\b", re.I),
    "pinterest": re.compile(r"\bpinterest\b", re.I),
    "youtube": re.compile(r"\byoutube\b", re.I),
    "rss": re.compile(r"\b(rss|atom|feed)\b", re.I),
}


def resolve(text: str) -> FlowSpec:
    """Utterance → FlowSpec, honestly confidence-labelled. Never guesses across apps."""
    t = text or ""
    mode = classify.classify(t)
    if mode == "NOW":
        return FlowSpec(kind="now", confidence="none")
    if mode in ("CRON", "POLL"):
        # cadence is deterministic; the AGENT for a schedule is a domain judgment the LLM makes
        # well, so schedules stay on the LLM path — resolved here only for benchmark visibility.
        return FlowSpec(kind=mode.lower(), cadence=classify.cadence_of(t), confidence="ambiguous")
    cands = classify.source_candidates(t)
    if not cands:
        return FlowSpec(kind="push", confidence="ambiguous")
    apps_hit = {a for a, _ in cands}
    best_app, best_event = cands[0]
    # registry-known rows only (the legacy rss/webhook planner labels are not armable pushes)
    row = triggers.get(best_app, best_event)
    if row is None:
        return FlowSpec(kind="push", confidence="ambiguous", candidates=tuple(cands))
    spec = FlowSpec(kind="push", source=row.app, event=row.event,
                    config=extract_slots(row.app, row.event, t), candidates=tuple(cands))
    # three demotions to ambiguous (→ the LLM path), in honesty order:
    #  1. no explicit standing-flow marker — classify's loose PUSH heuristic isn't enough to ACT on
    #  2. several apps' trigger phrases hit and the winner isn't NAMED in the sentence
    #  3. another app's vocabulary appears ("…repo or inbox…") even if its phrases didn't match
    if (not _STRONG.search(t)
            or (len(apps_hit) > 1 and not re.search(rf"\b{re.escape(best_app)}\b", t, re.I))
            or any(a != row.app and rx.search(t) for a, rx in _MARKERS.items())):
        spec.confidence = "ambiguous"
        return spec
    _row, problem = triggers.validate(spec.source, spec.event, spec.config)
    spec.confidence = "high"
    if problem:
        spec.ask, spec.missing = problem, next(
            (s for s in row.slots if triggers.SLOTS[s].required and s not in spec.config), "")
    return spec


# ── ask-till-legit: pending questions per conversation thread ────────────────────────────────────
_PENDING: dict[str, tuple[FlowSpec, str, float]] = {}      # thread → (spec, utterance, expires)
PENDING_TTL_SECS = 10 * 60


def park(thread: str, spec: FlowSpec, utterance: str) -> None:
    _PENDING[thread] = (spec, utterance, time.time() + PENDING_TTL_SECS)


def pending_for(thread: str):
    """The parked (spec, original utterance) for this thread, if fresh. Popped — fill or lose."""
    item = _PENDING.pop(thread, None)
    if item is None or item[2] < time.time():
        return None
    return item[0], item[1]


def fill(spec: FlowSpec, reply: str) -> FlowSpec | None:
    """Try the user's reply as the answer to ``spec.ask``. None = it isn't one (route normally).

    A reply that looks like a NEW request (another flow shape, or a long sentence with no
    extractable value) must not be crammed into the slot — the user changed their mind."""
    t = (reply or "").strip()
    if not t or not spec.missing:
        return None
    if classify.classify(t) != "NOW" and len(t.split()) > 4:     # reads as a fresh request
        return None
    v = _EXTRACT.get(spec.missing, lambda _t: "")(t)
    if not v and len(t.split()) <= 3:
        # a bare answer ("Read-later", "acme/api", "1234") — take the meaningful token
        tok = t.strip(" .!'\"‘’“”")
        if tok and tok.lower() not in _STOP:
            ok = {"repo": lambda s: "/" in s, "folder": str.isdigit}.get(spec.missing,
                                                                         lambda s: True)(tok)
            v = tok if ok else ""
    if not v:
        return None
    cfg = dict(spec.config)
    cfg[spec.missing] = v
    _row, problem = triggers.validate(spec.source, spec.event, cfg)
    out = FlowSpec(kind="push", source=spec.source, event=spec.event, config=cfg,
                   confidence="high", candidates=spec.candidates)
    if problem:
        out.ask = problem
        out.missing = next((s for s in (_row.slots if _row else ())
                            if triggers.SLOTS[s].required and s not in cfg), "")
    return out
