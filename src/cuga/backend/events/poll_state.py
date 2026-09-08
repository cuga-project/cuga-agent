"""Stateful native POLL — the DELTA gate (Phase 3, Tiers 0–2).

A native CRON always reports. A native POLL reports **only when something changed**. The change is
decided against tiny, durable state (the ``watch_state`` row), never the full observation — so it
scales: you store a number, a set of ids, or a short summary, never "the millions of files".

    Tier 0  always      no watch_state row → deliver every tick (plain cron; this module untouched)
    Tier 1  threshold   a scalar SIGNAL vs a baseline → fire on a fractional move ≥ threshold
    Tier 1  identity    a set of keys vs a seen-set   → fire on any NEW key (store keys, not content)
    Tier 2  fuzzy       no deterministic signal       → trust the agent's own changed? verdict

How the comparable value gets here: the agent RAN the tool, so the agent is asked to append a
machine-readable ``<<SIGNAL {…}>>`` line to its answer. :func:`augment_prompt` writes that
instruction (and, for fuzzy, feeds back the previous state); :func:`parse_signal` pulls it back out;
:func:`decide` — pure, no I/O — makes the call and returns the next state to persist.

The whole gate lives in ``/invoke`` (which owns delivery + run-logging); it activates only when the
subscription has a watch_state row, so cron and every non-poll path are entirely unaffected.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import namedtuple

log = logging.getLogger("events.poll_state")

Decision = namedtuple("Decision", "changed state reason")

_SIGNAL_RE = re.compile(r"<<\s*SIGNAL\s*(\{.*?\})\s*>>", re.S)
_SEEN_CAP = 5000  # bound the identity seen-set so it can't grow unbounded


# ── SIGNAL protocol ─────────────────────────────────────────────────────────────────────────────
def parse_signal(text: str) -> tuple[str, dict | None]:
    """Split an agent answer into ``(clean_text, signal_dict|None)``. The ``<<SIGNAL {…}>>`` line the
    agent appended is removed from the human-facing text and returned parsed. Malformed JSON → the
    marker is still stripped and signal is None (degrades to Tier-2/no-op, never crashes)."""
    if not isinstance(text, str) or "SIGNAL" not in text:
        return text, None
    m = _SIGNAL_RE.search(text)
    if not m:
        return text, None
    clean = (text[: m.start()] + text[m.end() :]).strip()
    try:
        return clean, json.loads(m.group(1))
    except Exception:  # noqa: BLE001 — a bad signal must not break delivery
        log.warning("unparseable SIGNAL payload: %r", m.group(1)[:120])
        return clean, None


def augment_prompt(ws: dict) -> str:
    """The instruction appended to a poll's fire prompt so the agent emits a comparable SIGNAL. For
    ``fuzzy`` it also feeds back the previous state so the agent can judge the delta itself."""
    kind = (ws or {}).get("kind") or "fuzzy"
    if kind == "threshold":
        what = (ws.get("value_path") or "the watched quantity").strip()
        return (
            "\n\n[WATCH] After your answer, output on a NEW final line EXACTLY "
            "`<<SIGNAL {\"value\": N}>>` where N is the current numeric value of "
            f"{what} — a bare number, no units, no thousands separators."
        )
    if kind == "identity":
        what = (ws.get("value_path") or "the items you found").strip()
        return (
            "\n\n[WATCH] After your answer, output on a NEW final line EXACTLY "
            "`<<SIGNAL {\"keys\": [...]}>>` — a JSON array of STABLE unique identifiers "
            f"(ids, urls, or exact titles) for {what}. I use it to report only NEW ones, so keep "
            "each key identical across runs for the same item."
        )
    prev = ""
    seen = (ws or {}).get("seen_keys") or []
    if seen:
        prev = str(seen[0])
    lead = f"You last saw this as: {prev!r}. " if prev else ""
    return (
        "\n\n[WATCH] " + lead + "After your answer, output on a NEW final line EXACTLY "
        "`<<SIGNAL {\"changed\": true|false, \"state\": \"<short new state>\"}>>` — "
        "changed=true ONLY if it MATERIALLY differs from what you last saw."
    )


# ── the DECISION core (pure) ─────────────────────────────────────────────────────────────────────
def _num(v):
    try:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        return float(str(v).replace(",", "").strip())
    except Exception:  # noqa: BLE001
        return None


def decide(ws: dict, signal: dict | None, now: float) -> Decision:
    """Given the prior state ``ws`` and the agent's ``signal``, return whether to report + the NEXT
    state to persist. Pure: no db, no network — the unit-test seam for all three tiers."""
    kind = (ws or {}).get("kind") or "fuzzy"
    state = dict(ws or {})
    state["updated_at"] = now
    if signal is None:  # agent gave nothing usable → don't fire, keep state
        return Decision(False, state, "no signal")

    if kind == "threshold":
        new = _num(signal.get("value"))
        if new is None:
            return Decision(False, state, "non-numeric signal")
        base = ws.get("baseline")
        if base is None:  # first observation seeds the baseline; never alerts
            state["baseline"] = new
            return Decision(False, state, f"baseline set to {new:g}")
        move = abs(new - base) / abs(base) if base else float("inf")
        thr = float(ws.get("threshold") or 0)
        changed = move >= thr
        policy = ws.get("reset_policy") or "ratchet"
        # ratchet: re-baseline from the last ALERT (next move measured from here).
        # per_tick: re-baseline every tick (measures tick-to-tick moves).
        # absolute: baseline is fixed forever (measures drift from the original).
        if (changed and policy == "ratchet") or policy == "per_tick":
            state["baseline"] = new
        return Decision(changed, state, f"move {move * 100:.2f}% vs {thr * 100:.2f}%")

    if kind == "identity":
        raw = signal.get("keys")
        if raw is None:
            raw = signal.get("value")
        keys = [str(k) for k in raw] if isinstance(raw, list) else ([str(raw)] if raw else [])
        seen = set(str(k) for k in (ws.get("seen_keys") or []))
        fresh = [k for k in keys if k not in seen]
        merged = list(seen | set(keys))
        if len(merged) > _SEEN_CAP:  # keep the most recent window
            merged = merged[-_SEEN_CAP:]
        state["seen_keys"] = merged
        return Decision(bool(fresh), state, f"{len(fresh)} new of {len(keys)}")

    # fuzzy (Tier 2): the agent judged it. Persist its compact state for next time's feedback.
    changed = bool(signal.get("changed"))
    st = signal.get("state")
    if st is not None:
        state["seen_keys"] = [str(st)]
    return Decision(changed, state, "agent verdict")


# ── SPEC extraction (arm time) — smart, not keyword ──────────────────────────────────────────────
def _default_ws(sub_id: str, spec: dict) -> dict:
    return {
        "subscription_id": sub_id,
        "kind": spec.get("kind") or "fuzzy",
        "seen_keys": [],
        "baseline": None,
        "reset_policy": spec.get("reset_policy") or "ratchet",
        "value_path": spec.get("value_path") or "",
        "threshold": float(spec.get("threshold") or 0),
        "updated_at": 0.0,
    }


def _heuristic_spec(utterance: str) -> dict:
    """Deterministic fallback (LLM off/unavailable). A percentage → threshold; a 'new <thing>' →
    identity; otherwise fuzzy. This is a SAFETY NET, not the primary path — poll-vs-cron and the
    intent are already an LLM decision upstream (the concierge picks the mode)."""
    t = (utterance or "").lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", t)
    if m or re.search(r"\b(changes?|moves?|jumps?|drops?|rises?|falls?)\b.*\bby\b", t):
        pct = float(m.group(1)) / 100.0 if m else 0.0
        return {"kind": "threshold", "threshold": pct, "reset_policy": "ratchet", "value_path": ""}
    if re.search(r"\b(new|another|fresh|added|appears?|shows? up|posted)\b", t):
        return {"kind": "identity", "value_path": ""}
    return {"kind": "fuzzy"}


_spec_model = None  # cached chat model


_SPEC_SYSTEM = (
    "You classify a recurring 'watch' request into HOW to detect a change. Reply with ONLY a JSON "
    "object, no prose:\n"
    '  {"kind": "threshold"|"identity"|"fuzzy", "threshold_pct": <number or null>, '
    '"watch": "<short phrase naming the exact value or items to compare across runs>"}\n'
    "Choose:\n"
    "  threshold — a NUMERIC quantity that fires on a move (a price, temperature, count, %). Put the "
    "trigger size in threshold_pct (e.g. 'by 5%' → 5; 'any move' → 0).\n"
    "  identity  — a stream of DISTINCT items where 'new ones' is the change (new files, emails, "
    "issues, posts, mentions).\n"
    "  fuzzy     — anything else, where only a human/agent can judge 'materially different' (weather "
    "outlook, a status summary, sentiment).\n"
    "'watch' names the comparable thing (e.g. 'IBM share price', 'files in /reports', 'SF weather')."
)


async def extract_spec(utterance: str) -> dict:
    """Extract the delta spec {kind, threshold, value_path, reset_policy} for a POLL. LLM-first
    (smart, not keyword) with a deterministic heuristic fallback. Always returns a spec (a poll has
    change semantics by definition; the weakest is 'fuzzy')."""
    if os.environ.get("EVENTS_POLL_LLM", "1") != "1":
        return _heuristic_spec(utterance)
    global _spec_model
    try:
        import asyncio
        from langchain_core.messages import HumanMessage, SystemMessage

        if _spec_model is None:
            try:
                from .llm import default_model_factory
            except ImportError:  # flat load (offline tests)
                from llm import default_model_factory
            _spec_model = default_model_factory(None)
        res = await asyncio.wait_for(
            _spec_model.ainvoke([SystemMessage(content=_SPEC_SYSTEM), HumanMessage(content=utterance or "")]),
            timeout=float(os.environ.get("EVENTS_POLL_LLM_TIMEOUT", "20")),
        )
        raw = (res.content or "").strip()
        m = re.search(r"\{.*\}", raw, re.S)
        d = json.loads(m.group(0)) if m else {}
        kind = str(d.get("kind") or "").lower()
        if kind not in ("threshold", "identity", "fuzzy"):
            raise ValueError(f"bad kind {kind!r}")
        spec = {"kind": kind, "value_path": str(d.get("watch") or "").strip()}
        if kind == "threshold":
            pct = d.get("threshold_pct")
            spec["threshold"] = (float(pct) / 100.0) if pct not in (None, "") else 0.0
            spec["reset_policy"] = "ratchet"
        return spec
    except Exception as e:  # noqa: BLE001
        log.warning("poll-spec LLM extract failed (%s) — heuristic fallback", str(e)[:120])
        return _heuristic_spec(utterance)


def spec_to_state(sub_id: str, spec: dict) -> dict:
    """A fresh watch_state row from an extracted spec (empty seen-set / no baseline yet)."""
    return _default_ws(sub_id, spec)
