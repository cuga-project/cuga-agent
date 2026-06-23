"""Behavior-pinning tests for the adapter-ized ToolApprovalHandler.

The handler used to be hard-coupled to ``CugaLiteState`` (``cuga_lite_metadata``,
``chat_messages``, ``goto="sandbox"``, ``sender="CugaLite"``). It now takes a
``CoreGraphAdapter`` whose approval seams default to exactly the legacy Lite
values, so Lite behavior must be byte-identical. Expected values below are
the *current* Lite behavior, locked before/after the refactor.
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter
from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.tool_approval_handler import ToolApprovalHandler


class LiteLikeAdapter(CoreGraphAdapter):
    """Mirrors _CugaLiteLoopAdapter's seam values (the defaults)."""

    messages_key = "chat_messages"

    def get_messages(self, state):
        return state.chat_messages

    def resolve_max_steps(self, state, override):
        return override if override is not None else 999


class SupLikeAdapter(CoreGraphAdapter):
    """Mirrors _CugaSupervisorLoopAdapter's approval seam overrides."""

    messages_key = "supervisor_chat_messages"
    metadata_key = "supervisor_metadata"
    execute_node_name = "execute_agent_tool"
    sender_name = "CugaSupervisor"

    def get_messages(self, state):
        return state.supervisor_chat_messages or []

    def resolve_max_steps(self, state, override):
        return override if override is not None else 999


# ─── CoreGraphAdapter approval-seam defaults (must equal legacy Lite) ────────


def test_default_approval_seams_are_lite_values():
    a = LiteLikeAdapter()
    assert a.metadata_key == "cuga_lite_metadata"
    assert a.execute_node_name == "sandbox"
    assert a.sender_name == "CugaLite"
    st = SimpleNamespace(cuga_lite_metadata={"x": 1})
    assert a.get_metadata(st) == {"x": 1}
    a.set_metadata(st, {"y": 2})
    assert st.cuga_lite_metadata == {"y": 2}
    # absent -> {}
    assert a.get_metadata(SimpleNamespace()) == {}


def test_supervisor_adapter_overrides_seams():
    a = SupLikeAdapter()
    assert a.metadata_key == "supervisor_metadata"
    assert a.execute_node_name == "execute_agent_tool"
    assert a.sender_name == "CugaSupervisor"
    st = SimpleNamespace(supervisor_metadata={"p": 1})
    assert a.get_metadata(st) == {"p": 1}
    a.set_metadata(st, {"q": 9})
    assert st.supervisor_metadata == {"q": 9}


# ─── pure handler methods (adapter-parameterized) ───────────────────────────


def test_should_skip_and_is_returning_from_approval():
    a = LiteLikeAdapter()
    yes = SimpleNamespace(cuga_lite_metadata={"user_approved": True})
    no = SimpleNamespace(cuga_lite_metadata={})
    assert ToolApprovalHandler.should_skip_policy_check(a, yes) is True
    assert ToolApprovalHandler.should_skip_policy_check(a, no) is False
    assert ToolApprovalHandler.is_returning_from_approval(a, yes) is True
    assert ToolApprovalHandler.is_returning_from_approval(a, no) is False


def test_extract_approved_code_reads_via_adapter_messages():
    a = LiteLikeAdapter()
    st = SimpleNamespace(
        chat_messages=[
            HumanMessage(content="do it"),
            AIMessage(content="```python\nprint('x')\n```"),
        ]
    )
    assert ToolApprovalHandler.extract_approved_code(a, st) == "print('x')"
    st2 = SimpleNamespace(chat_messages=[HumanMessage(content="no ai")])
    assert ToolApprovalHandler.extract_approved_code(a, st2) is None


def test_clean_approval_metadata_strips_known_fields():
    md = {
        "keep": 1,
        "approval_required": True,
        "user_approved": True,
        "required_tools": [],
        "required_apps": [],
        "full_code": "x",
        "code_preview": ["a"],
    }
    assert ToolApprovalHandler.clean_approval_metadata(md) == {"keep": 1}


def test_handle_denial_lite_shape():
    a = LiteLikeAdapter()
    denied = SimpleNamespace(cuga_lite_metadata={"user_approved": False}, step_count=4)
    cmd = ToolApprovalHandler.handle_denial(a, denied)
    assert cmd.goto == END
    assert cmd.update == {
        "execution_complete": True,
        "final_answer": "Execution cancelled by user.",
        "step_count": 5,
        "cuga_lite_metadata": {},
    }
    assert ToolApprovalHandler.handle_denial(a, SimpleNamespace(cuga_lite_metadata={})) is None


def test_handle_approval_resumption_routes_to_adapter_execute_node():
    a = LiteLikeAdapter()
    st = SimpleNamespace(
        chat_messages=[AIMessage(content="```python\nprint('go')\n```")],
        cuga_lite_metadata={"user_approved": True, "policy_name": "P"},
        step_count=2,
    )
    cmd = ToolApprovalHandler.handle_approval_resumption(a, st)
    assert cmd.goto == "sandbox"
    assert cmd.update["script"] == "print('go')"
    assert cmd.update["step_count"] == 3
    # cleaned metadata written under the adapter's metadata_key
    assert "user_approved" not in cmd.update["cuga_lite_metadata"]
    assert cmd.update["cuga_lite_metadata"]["policy_name"] == "P"


def test_real_supervisor_adapter_has_correct_approval_seams():
    """The real _CugaSupervisorLoopAdapter must carry supervisor seam values
    (not the Lite defaults) once Slice B wires approval into Supervisor."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_graph import (
        _SUPERVISOR_LOOP_ADAPTER,
    )

    a = _SUPERVISOR_LOOP_ADAPTER
    assert a.metadata_key == "supervisor_metadata"
    assert a.execute_node_name == "execute_agent_tool"
    assert a.sender_name == "CugaSupervisor"
    st = SimpleNamespace(supervisor_metadata={"k": 1})
    assert a.get_metadata(st) == {"k": 1}
    a.set_metadata(st, {"k": 2})
    assert st.supervisor_metadata == {"k": 2}


def test_handle_approval_resumption_supervisor_seams():
    a = SupLikeAdapter()
    st = SimpleNamespace(
        supervisor_chat_messages=[AIMessage(content="```python\nprint('go')\n```")],
        supervisor_metadata={"user_approved": True},
        step_count=0,
    )
    cmd = ToolApprovalHandler.handle_approval_resumption(a, st)
    assert cmd.goto == "execute_agent_tool"
    assert "supervisor_metadata" in cmd.update
    assert cmd.update["script"] == "print('go')"


def _hitl_return_to(adapter, metadata_key, messages_key):
    st = SimpleNamespace(
        **{
            messages_key: [],
            metadata_key: {
                "policy_name": "P",
                "required_tools": ["t1"],
                "approval_message": "Approve",
            },
            "step_count": 0,
        }
    )
    cmd = ToolApprovalHandler._create_approval_interrupt(adapter, st, "print('x')", "content", ["print('x')"])
    return cmd.update["hitl_action"].return_to


def test_create_approval_interrupt_return_to_supervisor():
    assert (
        _hitl_return_to(SupLikeAdapter(), "supervisor_metadata", "supervisor_chat_messages")
        == "CugaSupervisor"
    )


def test_create_approval_interrupt_return_to_lite():
    assert _hitl_return_to(LiteLikeAdapter(), "cuga_lite_metadata", "chat_messages") == "CugaLite"
