"""The reason→build **planner** — pure, testable core of NL→Flow (events_docs/ARCHITECTURE.md).

The real concierge uses an LLM to fill the typed slots; this module turns a decision
(mode/source/cadence) into the deterministic AP flow via the builders. Used directly by
``dry_run`` (no LLM) and as the fallback when no model is configured. Dependency-free.
"""

from __future__ import annotations

try:  # works both as a package module and flat-imported (tests)
    from . import classify, flows
except ImportError:  # pragma: no cover
    import classify
    import flows


def plan(
    text: str,
    *,
    agent: str = "worker",
    thread_id: str = "web:local",
    deliver_to: list | None = None,
    sink: dict | None = None,
) -> dict:
    """Return ``{decision, flow}`` for an utterance — no side effects.

    ``flow`` is the would-be AP flow JSON, or ``None`` for a channel-NOW (which rides the
    standing inbound flow rather than getting its own flow)."""
    d = classify.decision(text)
    mode = d["mode"]

    if mode == "NOW":
        # channel-NOW needs no new flow; return the decision + a run-once shape for reference
        return {"decision": d, "flow": None, "note": "channel-NOW rides the inbound flow; run via /invoke"}

    if mode in ("CRON", "POLL"):
        cad = d.get("cadence") or {}
        flow = flows.build_flow(
            mode,
            agent=agent,
            thread_id=thread_id,
            prompt=text,
            cron=cad.get("cron"),
            interval_seconds=cad.get("interval_seconds"),
            sink=sink,
        )
        return {"decision": d, "flow": flow}

    if mode == "PUSH":
        source = d.get("source", "webhook")
        event = d.get("event", "new_item")
        flow = flows.build_push_flow(
            agent=agent, thread_id=thread_id, prompt=text, source=source, event_kind=event, sink=sink
        )
        return {"decision": d, "flow": flow}

    raise ValueError(f"unhandled mode {mode!r}")
