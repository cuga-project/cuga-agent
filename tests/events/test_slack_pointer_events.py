"""Slack pointer events — the acknowledgement path and the redelivery that follows a slow one.

A reaction/star carries `item.{channel,ts}` but not the message, so it needs a
`conversations.replies` round-trip to be useful to an agent. That round-trip used to be AWAITED in
the webhook handler, before `return {"ok": True}` — a 10-second timeout in front of Slack's
3-second acknowledgement deadline. Slack then retried the event, and since neither
`direct_events.match` nor `dispatch_all` deduplicates, the retry ran every matched watcher a
second time. The slow path and the double-dispatch path were the same path.

These tests pin the two halves of the fix: hydration is off the ack path, and pointer events are
deduplicated on their own `event_ts`.
"""

from __future__ import annotations

import pytest


# ── the dedup gate ─────────────────────────────────────────────────────────────────────────────
def test_a_redelivered_pointer_event_is_only_dispatched_once():
    from cuga.backend.events.app import _slack_first_dispatch

    assert _slack_first_dispatch("1700000000.111") is True
    assert _slack_first_dispatch("1700000000.111") is False, "a Slack retry must not re-dispatch"


def test_distinct_reactions_are_not_confused_for_each_other():
    from cuga.backend.events.app import _slack_first_dispatch

    assert _slack_first_dispatch("1700000001.001") is True
    assert _slack_first_dispatch("1700000001.002") is True


def test_a_missing_event_ts_is_allowed_through():
    """Fail OPEN here: dropping an event we cannot key is worse than dispatching it twice."""
    from cuga.backend.events.app import _slack_first_dispatch

    assert _slack_first_dispatch("") is True
    assert _slack_first_dispatch("") is True


def test_the_pointer_namespace_is_separate_from_the_chat_one():
    """Sharing `_SLACK_ANSWERED_TS` would cross-cancel.

    Both gates key on a Slack timestamp. If a reaction's `event_ts` ever collided with a message
    `ts` the chat path had already recorded, a shared list would report the reaction as a
    duplicate and the watcher would never fire at all.
    """
    from cuga.backend.events import app as A

    assert A._SLACK_DISPATCHED_EVENT_TS is not A._SLACK_ANSWERED_TS

    ts = "1700000002.500"
    A._SLACK_ANSWERED_TS.append(ts)  # the chat path has answered this message
    try:
        assert A._slack_first_dispatch(ts) is True, "the watcher gate must not see the chat gate"
    finally:
        A._SLACK_ANSWERED_TS.remove(ts)
        A._SLACK_DISPATCHED_EVENT_TS.remove(ts)


def test_the_gate_is_bounded():
    """It is an in-memory list on a long-lived process, so it must not grow without limit."""
    from cuga.backend.events import app as A

    A._SLACK_DISPATCHED_EVENT_TS.clear()
    for i in range(500):
        A._slack_first_dispatch(f"ts-{i}")
    assert len(A._SLACK_DISPATCHED_EVENT_TS) <= 300
    A._SLACK_DISPATCHED_EVENT_TS.clear()


# ── hydration is off the acknowledgement path ──────────────────────────────────────────────────
def test_hydration_is_not_awaited_before_the_ack():
    """Pinned in source, because the failure is a timeout under load, not a wrong value.

    The marker is that `fetch_message_text` no longer appears between the handler's pointer
    comment and its `return {"ok": True}` — it moved into `_slack_dispatch_watchers`, which the
    handler launches with `create_task`.
    """
    import pathlib
    import re

    src = pathlib.Path("src/cuga/backend/events/app.py").read_text()
    i = src.index("POINTER-SHAPED EVENTS")
    # Anchor on CODE, not prose: the phrase `return {"ok": True}` also occurs in the comment that
    # explains this very fix, so a plain .index() from here lands inside the commentary.
    m = re.search(r'^\s*return \{"ok": True\}', src[i:], re.M)
    assert m, "could not find the handler's acknowledgement"
    window = src[i : i + m.start()]
    assert "await slack_direct.fetch_message_text" not in window, (
        "hydration is back on the ack path — a slow conversations.replies will blow Slack's "
        "3-second deadline and trigger the retry this was written to stop"
    )
    assert "create_task" in window, "dispatch must be launched as a background task"


@pytest.mark.asyncio
async def test_the_background_dispatcher_contains_its_own_failures(monkeypatch):
    """It is fire-and-forget, so an escaping exception is reported nowhere."""
    import pathlib

    src = pathlib.Path("src/cuga/backend/events/app.py").read_text()
    i = src.index("async def _slack_dispatch_watchers")
    body = src[i : i + 2600]
    assert "except Exception" in body and "_elog.exception" in body
