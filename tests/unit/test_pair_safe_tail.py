"""Truncation never orphans a tool reply (fc-deferred-tool-search §3.6)."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.utils.message_utils import pair_safe_tail

pytestmark = pytest.mark.unit


def _ai_calls(*ids):
    return AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": i} for i in ids])


def _reply(call_id):
    return ToolMessage(content="ok", tool_call_id=call_id, name="t")


HISTORY = [
    HumanMessage(content="q"),
    _ai_calls("a", "b"),
    _reply("a"),
    _reply("b"),
    AIMessage(content="answer"),
]


def _orphans(messages):
    issued = {c["id"] for m in messages for c in (getattr(m, "tool_calls", None) or [])}
    return [m.tool_call_id for m in messages if isinstance(m, ToolMessage) and m.tool_call_id not in issued]


def test_a_cut_on_tool_replies_moves_back_to_the_issuing_turn():
    tail = pair_safe_tail(HISTORY, 2)  # plain slicing would start on reply "b"
    assert tail == HISTORY[1:]
    assert _orphans(tail) == []


def test_a_cut_elsewhere_is_a_plain_tail():
    assert pair_safe_tail(HISTORY, 1) == HISTORY[-1:]
    assert pair_safe_tail(HISTORY, 4) == HISTORY[1:]


def test_orphans_without_an_issuing_turn_are_skipped_forward():
    history = [_reply("gone"), _reply("gone2"), AIMessage(content="x")]
    assert pair_safe_tail(history, 3) == history  # nothing cut
    assert pair_safe_tail([HumanMessage(content="q")] + history, 3) == history[2:]


@pytest.mark.parametrize("keep", [0, -1])
def test_non_positive_keep_keeps_nothing(keep):
    assert pair_safe_tail(HISTORY, keep) == []


def test_short_history_is_returned_whole():
    assert pair_safe_tail(HISTORY, 50) == HISTORY


def test_sliding_window_uses_the_pair_safe_tail(monkeypatch):
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "message_window_limit", 2, raising=False)
    state = AgentState(input="", url="", chat_messages=list(HISTORY))
    state.apply_message_sliding_window()
    assert _orphans(state.chat_messages) == []
