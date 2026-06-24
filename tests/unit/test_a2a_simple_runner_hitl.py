"""Unit tests for SimpleA2ARunner human-in-the-loop handling.

These exercise the two interrupt behaviours directly against the runner
(without the FastAPI/JSON-RPC layer): ``auto_approve=True`` resumes the graph
with a confirmed ActionResponse until an answer arrives, while the default
surfaces an ``input_required`` terminal event carrying the action_id.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.anyio


# -- fakes -----------------------------------------------------------------


class _Snap:
    """Minimal stand-in for a LangGraph StateSnapshot."""

    def __init__(self, next_, values):
        self.next = next_
        self.values = values


class _Graph:
    """Returns a queued sequence of snapshots, one per get_state() call."""

    def __init__(self, snaps):
        self._snaps = snaps
        self.calls = 0

    def get_state(self, _config):
        snap = self._snaps[min(self.calls, len(self._snaps) - 1)]
        self.calls += 1
        return snap


class _Agent:
    def __init__(self, graph):
        self.graph = graph


class _AppState:
    def __init__(self, graph):
        self.agent = _Agent(graph)
        self.output_format = None


def _answer_frame(text: str) -> bytes:
    payload = json.dumps({"data": text, "variables": {}, "active_policies": []})
    return f"event: Answer\ndata: {payload}\n\n".encode()


def _make_runner(app_state, event_stream, auto_approve):
    from cuga.backend.server.a2a.simple_runner import SimpleA2ARunner

    return SimpleA2ARunner(app_state, event_stream, auto_approve=auto_approve)


# -- tests -----------------------------------------------------------------


async def test_auto_approve_resumes_until_answer():
    """auto_approve=True confirms the interrupt and resumes to a final answer."""
    hitl = {
        "action_id": "tool_approval",
        "type": "confirmation",
        "description": "Approve tool execution?",
        "additional_data": {"tool": None},
    }
    # Entry read: not parked (fresh query). Post-stream read: paused with a
    # pending action that auto-approve then resumes.
    graph = _Graph([_Snap((), {}), _Snap(("WaitForResponse",), {"hitl_action": hitl})])
    seen = {"resume": []}

    async def event_stream(**kwargs):
        seen["resume"].append(kwargs.get("resume"))
        if kwargs.get("resume") is None:
            # First turn interrupts without answering.
            yield b"event: tool_call\ndata: preparing\n\n"
        else:
            yield _answer_frame("done!")

    runner = _make_runner(_AppState(graph), event_stream, auto_approve=True)
    events = [ev async for ev in runner.run("do it", "ctx-1")]

    final = events[-1]
    assert final.final is True
    assert final.name == "final_answer"
    assert final.data["text"] == "done!"

    # Resumed exactly once with a confirmed ActionResponse for the action.
    resumes = [r for r in seen["resume"] if r is not None]
    assert len(resumes) == 1
    assert resumes[0].confirmed is True
    assert resumes[0].action_id == "tool_approval"


async def test_surface_mode_emits_input_required():
    """auto_approve=False surfaces the pending action and stops."""
    hitl = {
        "action_id": "tool_approval",
        "type": "confirmation",
        "description": "Approve tool X?",
    }
    # Entry read: not parked. Post-stream read: paused awaiting approval.
    graph = _Graph([_Snap((), {}), _Snap(("WaitForResponse",), {"hitl_action": hitl})])

    async def event_stream(**kwargs):
        yield b"event: tool_call\ndata: preparing\n\n"

    runner = _make_runner(_AppState(graph), event_stream, auto_approve=False)
    events = [ev async for ev in runner.run("do it", "ctx-2")]

    final = events[-1]
    assert final.final is True
    assert final.name == "input_required"
    assert "Approve tool X?" in final.data["text"]
    assert final.data["action_id"] == "tool_approval"


async def test_plain_answer_no_interrupt():
    """A stream that answers directly never touches graph state."""
    graph = _Graph([_Snap((), {})])

    async def event_stream(**kwargs):
        yield _answer_frame("hello there")

    runner = _make_runner(_AppState(graph), event_stream, auto_approve=False)
    events = [ev async for ev in runner.run("hi", "ctx-3")]

    assert events[-1].name == "final_answer"
    assert events[-1].data["text"] == "hello there"
    # Only the entry parked-check reads state; answering needs no further reads.
    assert graph.calls == 1


# -- inbound approval round-trip (auto_approve=False) ----------------------


def _parked_graph(hitl):
    """Graph whose entry read reports a parked HITL interrupt."""
    return _Graph([_Snap(("WaitForResponse",), {"hitl_action": hitl})])


_HITL = {"action_id": "new_flow_approve", "type": "confirmation", "description": "Run the flow?"}


async def test_resume_on_structured_approval():
    """A parked thread + structured confirmed=True resumes and answers."""
    seen = {"resume": []}

    async def event_stream(**kwargs):
        seen["resume"].append(kwargs.get("resume"))
        yield _answer_frame("flow result")

    runner = _make_runner(_AppState(_parked_graph(_HITL)), event_stream, auto_approve=False)
    events = [
        ev
        async for ev in runner.run(
            "", "ctx-r1", approval={"action_id": "new_flow_approve", "confirmed": True}
        )
    ]

    assert events[-1].name == "final_answer"
    assert events[-1].data["text"] == "flow result"
    resumes = [r for r in seen["resume"] if r is not None]
    assert len(resumes) == 1
    assert resumes[0].confirmed is True
    assert resumes[0].action_id == "new_flow_approve"


async def test_resume_on_text_approval():
    """Plain-text 'approve' resumes with confirmed=True when no structured signal."""
    seen = {"resume": []}

    async def event_stream(**kwargs):
        seen["resume"].append(kwargs.get("resume"))
        yield _answer_frame("done")

    runner = _make_runner(_AppState(_parked_graph(_HITL)), event_stream, auto_approve=False)
    events = [ev async for ev in runner.run("approve", "ctx-r2")]

    assert events[-1].name == "final_answer"
    assert [r for r in seen["resume"] if r][0].confirmed is True


async def test_deny_on_text():
    """Plain-text 'no' resumes with confirmed=False."""
    seen = {"resume": []}

    async def event_stream(**kwargs):
        seen["resume"].append(kwargs.get("resume"))
        yield _answer_frame("cancelled")

    runner = _make_runner(_AppState(_parked_graph(_HITL)), event_stream, auto_approve=False)
    events = [ev async for ev in runner.run("no", "ctx-r3")]

    assert events[-1].name == "final_answer"
    assert [r for r in seen["resume"] if r][0].confirmed is False


async def test_transcript_echo_resolves_to_last_reply():
    """A caller that echoes the whole transcript (incl. our prompt line, which
    contains both 'approve' and 'deny') still resolves to the final reply."""
    seen = {"resume": []}

    async def event_stream(**kwargs):
        seen["resume"].append(kwargs.get("resume"))
        yield _answer_frame("done")

    blob = (
        "List the top 7 accounts by revenue\n"
        "Approval required: I will run a new flow autonomously\n\n"
        'Reply "approve" to proceed or "deny" to cancel.\n'
        "approve"
    )
    runner = _make_runner(_AppState(_parked_graph(_HITL)), event_stream, auto_approve=False)
    events = [ev async for ev in runner.run(blob, "ctx-echo")]

    assert events[-1].name == "final_answer"
    assert [r for r in seen["resume"] if r][0].confirmed is True


async def test_ambiguous_reply_reasks_without_resuming():
    """An unclear reply re-emits input_required and never starts the graph."""
    called = {"stream": False}

    async def event_stream(**kwargs):
        called["stream"] = True
        yield _answer_frame("should not happen")

    runner = _make_runner(_AppState(_parked_graph(_HITL)), event_stream, auto_approve=False)
    events = [ev async for ev in runner.run("hmm not sure", "ctx-r4")]

    assert called["stream"] is False  # graph not resumed on an ambiguous reply
    assert events[-1].name == "input_required"
    assert events[-1].final is True
    assert events[-1].data["action_id"] == "new_flow_approve"


# -- router approval extraction --------------------------------------------


async def test_extract_approval_from_datapart():
    from cuga.backend.server.a2a._a2a_types import MessageSendParams
    from cuga.backend.server.a2a.router import _extract_approval

    params = MessageSendParams.model_validate(
        {
            "message": {
                "role": "user",
                "parts": [{"kind": "data", "data": {"action_id": "x", "confirmed": True}}],
                "messageId": "m1",
            }
        }
    )
    assert _extract_approval(params) == {"action_id": "x", "confirmed": True}


async def test_extract_approval_from_metadata_and_none():
    from cuga.backend.server.a2a._a2a_types import MessageSendParams
    from cuga.backend.server.a2a.router import _extract_approval

    with_meta = MessageSendParams.model_validate(
        {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "ok"}],
                "messageId": "m1",
                "metadata": {"action_id": "y", "confirmed": False},
            }
        }
    )
    assert _extract_approval(with_meta) == {"action_id": "y", "confirmed": False}

    text_only = MessageSendParams.model_validate(
        {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "approve"}],
                "messageId": "m1",
            }
        }
    )
    assert _extract_approval(text_only) is None
