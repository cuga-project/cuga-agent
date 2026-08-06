"""Step recorder — turns a live harness run into a readable narrative.

A pass/fail count tells you *whether* something broke. It does not tell you what a person would have
had to do to see it, or what they would have seen instead. So each harness also records its steps in
the second person — "you post a message in #eda-test" — along with what was expected and what actually
came back. `scripts/run_all_tests.py` renders those into the final report.

Recording is OFF unless ``E2E_STEPS_FILE`` names a file, so importing this costs a normal harness run
nothing and no harness needs a flag. One JSON object per line, appended, so a crashed run still leaves
every step it got through — which is exactly when you want them.

    from steps import step
    step(actor="you", action="post 'what is bitcoin worth?' in #eda-test",
         expect="the bot replies in-thread with a number",
         got=reply, ok=bool(reply))
"""

from __future__ import annotations

import json
import os
import threading

_LOCK = threading.Lock()
_PATH = os.environ.get("E2E_STEPS_FILE", "")


def enabled() -> bool:
    return bool(_PATH)


def _clip(v, n=240) -> str:
    s = " ".join(str(v or "").split())  # collapse newlines: one step = one row
    return s if len(s) <= n else s[: n - 1] + "…"


#: The dimensions a reader wants to slice a run by. Rendered as columns, but only in phases where at
#: least one row supplies them — a channel round-trip has no integration, and padding it with "—"
#: buys nothing.
DIMENSIONS = ("utterance", "channel", "integration", "trigger")


def step(
    *,
    actor: str,
    action: str,
    expect: str,
    got="",
    ok: bool | None = None,
    phase: str = "",
    surface: str = "",
    note: str = "",
    utterance: str = "",
    channel: str = "",
    integration: str = "",
    trigger: str = "",
) -> None:
    """Record one thing a person did and what came back. No-op unless E2E_STEPS_FILE is set.

    `ok=None` means "this step is setup, not an assertion" — it renders without a verdict, which keeps
    scene-setting ("you open Slack") from inflating the pass count.

    `utterance` is the sentence a person actually typed; `channel` / `integration` / `trigger` are the
    three axes a flow lives on. Supply them wherever they're meaningful and the report grows the
    columns by itself.
    """
    if not _PATH:
        return
    row = {
        "phase": phase,
        "surface": surface,
        "actor": actor,
        "action": action,
        "expect": _clip(expect),
        "got": _clip(got),
        "ok": ok,
        "note": _clip(note, 160),
        "utterance": _clip(utterance, 120),
        "channel": channel,
        "integration": integration,
        "trigger": trigger,
    }
    with _LOCK:
        with open(_PATH, "a") as f:
            f.write(json.dumps(row) + "\n")


def read(path: str) -> list[dict]:
    """Load a steps file, skipping any half-written trailing line from a killed run.

    Rows written before the dimension columns existed simply lack those keys, so every consumer must
    read them with ``.get(k, "")`` — which is what ``dims_present`` below relies on.
    """
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def dims_present(rows: list[dict]) -> list[str]:
    """Which dimension columns are worth rendering for this set of rows (any non-empty value)."""
    return [d for d in DIMENSIONS if any((r.get(d) or "").strip() for r in rows)]
