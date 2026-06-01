"""Tests for the shared loop helpers (slice 4a).

These pin behavior of the de-duplicated ``append_chat_messages_with_step_limit``
and ``create_error_command`` extracted from Lite + Supervisor. The only
graph-specific bits are abstracted behind ``CoreGraphAdapter``:

- ``messages_key`` — state key the error Command writes messages to;
- ``get_messages`` — base message list to append onto;
- ``resolve_max_steps`` — how the step limit is resolved.

Parity guards reproduce the exact Lite and Supervisor adapter behavior so
the extraction cannot silently drift.
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    CoreGraphAdapter,
    append_chat_messages_with_step_limit,
    create_error_command,
    enforce_step_limit,
    execution_output_text,
    inject_playbook_guidance,
)


# ─── execution_output_text (Candidate 3 micro-slice) ────────────────────────


def test_execution_output_text_format():
    assert execution_output_text("hello\nworld") == "Execution output:\nhello\nworld"
    assert execution_output_text("") == "Execution output:\n"


# ─── CoreGraphAdapter.get_variable_manager default + override ────────────────


def test_get_variable_manager_default_uses_state_variables_manager():
    class A(CoreGraphAdapter):
        messages_key = "m"

        def get_messages(self, state):
            return []

        def resolve_max_steps(self, state, override):
            return 1

    vm = object()
    assert A().get_variable_manager(SimpleNamespace(variables_manager=vm)) is vm
    # absent attribute -> None (default is getattr-with-None)
    assert A().get_variable_manager(SimpleNamespace()) is None


def test_get_variable_manager_is_overridable():
    sup_vm = object()

    class SupLike(CoreGraphAdapter):
        messages_key = "supervisor_chat_messages"

        def get_messages(self, state):
            return []

        def resolve_max_steps(self, state, override):
            return 1

        def get_variable_manager(self, state):
            return state.supervisor_variables_manager

    assert SupLike().get_variable_manager(SimpleNamespace(supervisor_variables_manager=sup_vm)) is sup_vm


class _FakeAdapter(CoreGraphAdapter):
    messages_key = "m"

    def __init__(self, limit: int):
        self._limit = limit

    def get_messages(self, state):
        return list(getattr(state, "m", None) or [])

    def resolve_max_steps(self, state, override):
        return override if override is not None else self._limit


def _state(messages, step_count):
    return SimpleNamespace(m=messages, step_count=step_count)


# ─── append_chat_messages_with_step_limit ───────────────────────────────────


def test_under_limit_appends_and_returns_no_error():
    base = [HumanMessage(content="hi")]
    new = [AIMessage(content="ok")]
    out, err = append_chat_messages_with_step_limit(_FakeAdapter(limit=5), _state(base, step_count=1), new)
    assert err is None
    assert out == base + new


def test_over_limit_appends_error_message_and_returns_it():
    out, err = append_chat_messages_with_step_limit(
        _FakeAdapter(limit=2), _state([], step_count=2), [AIMessage(content="x")]
    )
    assert isinstance(err, AIMessage)
    assert "Maximum step limit (2) reached" in err.content
    assert out[-1] is err


def test_limit_boundary_is_strictly_greater_than():
    # step_count=1, limit=2 -> new_step_count=2, not > 2 -> OK
    _, err_ok = append_chat_messages_with_step_limit(_FakeAdapter(limit=2), _state([], step_count=1), [])
    assert err_ok is None
    # step_count=2, limit=2 -> new_step_count=3 > 2 -> error
    _, err_bad = append_chat_messages_with_step_limit(_FakeAdapter(limit=2), _state([], step_count=2), [])
    assert err_bad is not None


def test_max_steps_override_takes_precedence():
    _, err = append_chat_messages_with_step_limit(
        _FakeAdapter(limit=999), _state([], step_count=3), [], max_steps=2
    )
    assert err is not None
    assert "Maximum step limit (2) reached" in err.content


# ─── create_error_command ───────────────────────────────────────────────────


def test_error_command_routes_to_end_with_expected_update():
    msgs = [AIMessage(content="boom")]
    err = AIMessage(content="boom")
    cmd = create_error_command(_FakeAdapter(limit=1), msgs, err, step_count=4)
    assert cmd.goto == END
    u = cmd.update
    assert u["m"] == msgs
    assert u["script"] is None
    assert u["final_answer"] == "boom"
    assert u["execution_complete"] is True
    assert u["error"] == "boom"
    assert u["step_count"] == 5


def test_error_command_additional_updates_merge_and_override():
    err = AIMessage(content="e")
    cmd = create_error_command(
        _FakeAdapter(limit=1),
        [],
        err,
        step_count=0,
        additional_updates={"final_answer": "overridden", "extra": 1},
    )
    assert cmd.update["final_answer"] == "overridden"
    assert cmd.update["extra"] == 1


# ─── enforce_step_limit (Candidate 2: the in-call_model branch check) ────────


def test_enforce_step_limit_returns_none_under_limit():
    cmd = enforce_step_limit(
        _FakeAdapter(limit=99),
        state=_state([], step_count=3),
        messages=[HumanMessage(content="u")],
        new_step_count=4,
        limit=10,
    )
    assert cmd is None


def test_enforce_step_limit_returns_end_command_over_limit():
    msgs = [HumanMessage(content="u"), AIMessage(content="code")]
    cmd = enforce_step_limit(
        _FakeAdapter(limit=99),
        state=_state([], step_count=2),
        messages=msgs,
        new_step_count=3,
        limit=2,
    )
    assert cmd is not None
    assert cmd.goto == END
    # uses the PASSED messages (not adapter.get_messages) + appended error
    written = cmd.update["m"]
    assert written[:2] == msgs
    assert isinstance(written[-1], AIMessage)
    assert "Maximum step limit (2) reached" in written[-1].content
    assert cmd.update["final_answer"] == written[-1].content
    assert cmd.update["step_count"] == 3  # state.step_count (2) + 1


def test_enforce_step_limit_boundary_strictly_greater():
    common = dict(state=_state([], step_count=0), messages=[])
    assert enforce_step_limit(_FakeAdapter(1), new_step_count=2, limit=2, **common) is None
    assert enforce_step_limit(_FakeAdapter(1), new_step_count=3, limit=2, **common) is not None


def test_enforce_step_limit_respects_adapter_messages_key():
    class SupLike(CoreGraphAdapter):
        messages_key = "supervisor_chat_messages"

        def get_messages(self, state):
            return []

        def resolve_max_steps(self, state, override):
            return 50

    cmd = enforce_step_limit(
        SupLike(),
        state=_state([], step_count=5),
        messages=[],
        new_step_count=99,
        limit=1,
    )
    assert "supervisor_chat_messages" in cmd.update
    assert cmd.update["step_count"] == 6


# ─── parity with the real Lite / Supervisor adapter behavior ────────────────


def test_lite_like_adapter_matches_legacy_behavior():
    """Lite: base = state.chat_messages, key = 'chat_messages',
    max_steps = override or settings default (here injected = 50)."""

    class LiteLike(CoreGraphAdapter):
        messages_key = "chat_messages"

        def get_messages(self, state):
            return state.chat_messages

        def resolve_max_steps(self, state, override):
            return override if override is not None else 50

    st = SimpleNamespace(chat_messages=[HumanMessage(content="u")], step_count=0)
    out, err = append_chat_messages_with_step_limit(LiteLike(), st, [AIMessage(content="a")])
    assert err is None
    assert out == [HumanMessage(content="u"), AIMessage(content="a")]
    cmd = create_error_command(LiteLike(), out, AIMessage(content="z"), 0)
    assert "chat_messages" in cmd.update


def test_supervisor_like_adapter_handles_none_messages():
    """Supervisor: base = state.supervisor_chat_messages or [],
    key = 'supervisor_chat_messages', max_steps = state value or 50."""

    class SupLike(CoreGraphAdapter):
        messages_key = "supervisor_chat_messages"

        def get_messages(self, state):
            return state.supervisor_chat_messages or []

        def resolve_max_steps(self, state, override):
            v = state.cuga_lite_max_steps
            return v if v is not None else 50

    st = SimpleNamespace(supervisor_chat_messages=None, step_count=0, cuga_lite_max_steps=None)
    out, err = append_chat_messages_with_step_limit(SupLike(), st, [AIMessage(content="a")])
    assert err is None
    assert out == [AIMessage(content="a")]
    cmd = create_error_command(SupLike(), out, AIMessage(content="z"), 7)
    assert "supervisor_chat_messages" in cmd.update
    assert cmd.update["step_count"] == 8


# ─── inject_playbook_guidance ────────────────────────────────────────────────


def test_inject_playbook_guidance_no_op_when_metadata_is_none():
    msgs = [HumanMessage(content="hi")]
    assert inject_playbook_guidance(msgs, None) is msgs


def test_inject_playbook_guidance_no_op_when_empty_metadata():
    msgs = [HumanMessage(content="hi")]
    assert inject_playbook_guidance(msgs, {}) is msgs


def test_inject_playbook_guidance_no_op_when_not_policy_matched():
    msgs = [HumanMessage(content="hi")]
    meta = {"policy_matched": False, "policy_type": "playbook", "playbook_guidance": "g"}
    assert inject_playbook_guidance(msgs, meta) == msgs


def test_inject_playbook_guidance_no_op_when_wrong_policy_type():
    msgs = [HumanMessage(content="hi")]
    meta = {"policy_matched": True, "policy_type": "tool_guide", "playbook_guidance": "g"}
    assert inject_playbook_guidance(msgs, meta) == msgs


def test_inject_playbook_guidance_no_op_when_already_added():
    msgs = [HumanMessage(content="hi")]
    meta = {
        "policy_matched": True,
        "policy_type": "playbook",
        "playbook_guidance": "g",
        "playbook_guidance_added": True,
    }
    assert inject_playbook_guidance(msgs, meta) == msgs


def test_inject_playbook_guidance_no_op_when_guidance_empty():
    msgs = [HumanMessage(content="hi")]
    meta = {"policy_matched": True, "policy_type": "playbook", "playbook_guidance": ""}
    assert inject_playbook_guidance(msgs, meta) == msgs


def test_inject_playbook_guidance_injects_into_last_human_message():
    msgs = [
        HumanMessage(content="first"),
        AIMessage(content="resp"),
        HumanMessage(content="second"),
    ]
    meta = {"policy_matched": True, "policy_type": "playbook", "playbook_guidance": "step 1\nstep 2"}
    result = inject_playbook_guidance(msgs, meta)
    assert result[0].content == "first"
    assert result[1].content == "resp"
    assert result[2].content == "second\n\n## Task Guidance\nstep 1\nstep 2"


def test_inject_playbook_guidance_does_not_mutate_input():
    msgs = [HumanMessage(content="hi")]
    meta = {"policy_matched": True, "policy_type": "playbook", "playbook_guidance": "g"}
    result = inject_playbook_guidance(msgs, meta)
    assert msgs[0].content == "hi"
    assert result is not msgs


def test_inject_playbook_guidance_no_op_when_no_human_message():
    msgs = [AIMessage(content="response only")]
    meta = {"policy_matched": True, "policy_type": "playbook", "playbook_guidance": "g"}
    result = inject_playbook_guidance(msgs, meta)
    assert result == msgs
