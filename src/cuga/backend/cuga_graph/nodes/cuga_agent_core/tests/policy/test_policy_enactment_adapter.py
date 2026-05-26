"""Behavior-pinning tests for the adapter-ized PolicyEnactment block path.

``_enact_block_intent`` was hard-coupled to ``CugaLiteState`` (read
``state.chat_messages``; write update keys ``chat_messages`` /
``cuga_lite_metadata``). It now takes an optional ``CoreGraphAdapter``:

- ``adapter=None`` → exact legacy Lite literals (back-compat for the
  output-formatter caller and any other path);
- Lite adapter → identical (its seam values equal those literals);
- Supervisor adapter → ``supervisor_chat_messages`` / ``supervisor_metadata``.
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter
from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment


class LiteLike(CoreGraphAdapter):
    messages_key = "chat_messages"

    def get_messages(self, state):
        return state.chat_messages

    def resolve_max_steps(self, state, override):
        return 999


class SupLike(CoreGraphAdapter):
    messages_key = "supervisor_chat_messages"
    metadata_key = "supervisor_metadata"

    def get_messages(self, state):
        return state.supervisor_chat_messages or []

    def resolve_max_steps(self, state, override):
        return 999


def _match():
    return SimpleNamespace(
        action=SimpleNamespace(content="blocked!"),
        policy=SimpleNamespace(id="p1", name="Guard"),
        reasoning="nope",
        confidence=0.9,
    )


def _lite_state():
    return SimpleNamespace(chat_messages=[HumanMessage(content="hi")])


def test_block_intent_legacy_default_no_adapter():
    cmd, meta = PolicyEnactment._enact_block_intent(_lite_state(), _match(), None)
    assert meta is None
    assert cmd.goto == END
    u = cmd.update
    assert isinstance(u["chat_messages"][-1], AIMessage)
    assert u["chat_messages"][-1].content == "blocked!"
    assert u["final_answer"] == "blocked!"
    assert u["execution_complete"] is True
    assert u["step_count"] == 0
    assert u["cuga_lite_metadata"]["policy_blocked"] is True
    assert u["cuga_lite_metadata"]["policy_id"] == "p1"
    assert u["cuga_lite_metadata"]["policy_type"] == "intent_guard"


def test_block_intent_lite_adapter_is_identical_to_legacy():
    legacy_cmd, _ = PolicyEnactment._enact_block_intent(_lite_state(), _match(), None)
    lite_cmd, _ = PolicyEnactment._enact_block_intent(_lite_state(), _match(), LiteLike())
    assert lite_cmd.goto == legacy_cmd.goto
    assert set(lite_cmd.update) == set(legacy_cmd.update)
    assert "cuga_lite_metadata" in lite_cmd.update
    assert "chat_messages" in lite_cmd.update


def test_block_intent_supervisor_adapter_uses_supervisor_keys():
    st = SimpleNamespace(supervisor_chat_messages=[HumanMessage(content="u")])
    cmd, _ = PolicyEnactment._enact_block_intent(st, _match(), SupLike())
    assert cmd.goto == END
    assert "supervisor_chat_messages" in cmd.update
    assert "supervisor_metadata" in cmd.update
    assert "chat_messages" not in cmd.update
    assert "cuga_lite_metadata" not in cmd.update
    assert cmd.update["supervisor_chat_messages"][-1].content == "blocked!"
    assert cmd.update["supervisor_metadata"]["policy_blocked"] is True
