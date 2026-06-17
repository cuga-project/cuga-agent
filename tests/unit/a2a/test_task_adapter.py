"""Unit tests for cuga.backend.server.a2a.task_adapter.

The adapter is the only place where CUGA's StreamEvent shape meets the
A2A wire format, so the failure modes worth pinning are:
- happy-path lifecycle (working -> completed),
- HITL interrupts (-> input-required),
- thread/context-id round-trip,
- forward-compatibility (unknown event names must not crash).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest


@pytest.fixture
def adapter():
    return pytest.importorskip("cuga.backend.server.a2a.task_adapter")


@dataclass
class _Ev:
    name: str
    data: Any = None
    final: bool = False


def _states(out):
    """Pull the `state` value out of whatever the adapter emits.

    The adapter may return a list of A2A TaskStatusUpdateEvent, or pairs
    of (event, message). We accept either shape and just probe `.status.state`.
    """
    states = []
    for item in out:
        # TaskStatusUpdateEvent.status.state, or a tuple where item[0] is one
        target = item[0] if isinstance(item, tuple) else item
        status = getattr(target, "status", None)
        state = getattr(status, "state", None) if status is not None else None
        if state is not None:
            states.append(str(state))
    return states


def test_in_flight_event_emits_working(adapter):
    out = list(
        adapter.stream_events_to_a2a(
            [_Ev("agent_thinking", {"text": "planning"})],
            task_id="t-1",
            context_id="c-1",
        )
    )
    states = _states(out)
    assert states, "adapter must emit at least one A2A event for an in-flight stream event"
    assert any("working" in s for s in states)


def test_final_event_emits_completed(adapter):
    out = list(
        adapter.stream_events_to_a2a(
            [_Ev("final_answer", {"text": "done"}, final=True)],
            task_id="t-1",
            context_id="c-1",
        )
    )
    states = _states(out)
    assert any("completed" in s for s in states), f"expected completed in {states!r}"


def test_full_lifecycle_progresses_working_then_completed(adapter):
    out = list(
        adapter.stream_events_to_a2a(
            [
                _Ev("agent_thinking", {"text": "planning"}),
                _Ev("final_answer", {"text": "done"}, final=True),
            ],
            task_id="t-1",
            context_id="c-1",
        )
    )
    states = _states(out)
    # Must see at least one working before completed.
    working_idx = next((i for i, s in enumerate(states) if "working" in s), -1)
    completed_idx = next((i for i, s in enumerate(states) if "completed" in s), -1)
    assert working_idx >= 0 and completed_idx >= 0
    assert working_idx < completed_idx


def test_hitl_event_maps_to_input_required(adapter):
    """If an interrupt is signalled, A2A must surface state=input-required
    so the client knows it needs to send a follow-up message."""
    out = list(
        adapter.stream_events_to_a2a(
            [_Ev("approval_required", {"prompt": "Confirm?"})],
            task_id="t-1",
            context_id="c-1",
        )
    )
    states = _states(out)
    assert any("input" in s for s in states), f"expected input-required state in {states!r}"


def test_context_id_round_trips_to_thread(adapter):
    """contextId in == contextId out. Mismatch breaks resumption."""
    out = list(
        adapter.stream_events_to_a2a(
            [_Ev("final_answer", {"text": "ok"}, final=True)],
            task_id="task-abc",
            context_id="ctx-xyz",
        )
    )
    seen_ctx = set()
    for item in out:
        target = item[0] if isinstance(item, tuple) else item
        ctx = getattr(target, "context_id", None) or getattr(target, "contextId", None)
        if ctx:
            seen_ctx.add(str(ctx))
    assert "ctx-xyz" in seen_ctx


def test_unknown_event_name_does_not_raise(adapter):
    """Forward-compat: a brand-new CUGA event type must not 500 the A2A
    endpoint. The adapter should drop or coerce it, never propagate."""
    list(
        adapter.stream_events_to_a2a(
            [_Ev("a_brand_new_event_type_we_havent_seen_before", {"x": 1})],
            task_id="t-1",
            context_id="c-1",
        )
    )


def test_empty_stream_emits_at_least_terminal_state(adapter):
    """An empty stream (graph yielded nothing) should still close the task
    cleanly so the A2A client doesn't hang."""
    out = list(adapter.stream_events_to_a2a([], task_id="t-1", context_id="c-1"))
    states = _states(out)
    # Either completed or failed is acceptable; what's not acceptable is
    # an open-ended task with no terminal event.
    assert any("complet" in s or "fail" in s for s in states)


def test_error_terminal_event_maps_to_failed_not_completed(adapter):
    """When the runner yields a terminal `error` event, A2A clients must
    see TaskState.failed — otherwise upstream callers can't tell a
    successful run from a crashed one. Earlier revisions silently mapped
    every terminal to `completed`, masking real failures."""
    out = list(
        adapter.stream_events_to_a2a(
            [_Ev("error", {"text": "kaboom"}, final=True)],
            task_id="t-1",
            context_id="c-1",
        )
    )
    states = _states(out)
    assert any("fail" in s for s in states), f"expected failed state in {states!r}"
    assert not any("complet" in s for s in states), f"unexpected completed in {states!r}"


def test_named_error_event_without_final_flag_still_terminates(adapter):
    """Even when the runner forgot to set `final=True`, an event named
    `error` / `failed` / `failure` / `exception` must close the task
    with state=failed. This is what protected the demo when the provider
    surfaced a model-access 401 as a single un-flagged error event."""
    out = list(
        adapter.stream_events_to_a2a(
            [_Ev("failure", {"text": "remote refused"})],
            task_id="t-1",
            context_id="c-1",
        )
    )
    states = _states(out)
    assert any("fail" in s for s in states), f"expected failed state in {states!r}"
