"""Unit tests for the skill ``allowed-tools`` whitelist enforcement helper.

Pure function tests on :mod:`cuga.backend.slash_commands.allowed_tools_enforcement`.
The actual interrupt wiring is exercised indirectly via the dispatcher tests.
"""

from __future__ import annotations

from cuga.backend.slash_commands.allowed_tools_enforcement import (
    PYTHON_BUILTINS_SAFELIST,
    find_disallowed_calls,
)


def test_returns_empty_when_allowed_tools_is_none():
    # `None` means "key absent from frontmatter" → no restriction (status quo).
    assert find_disallowed_calls("await write_file('a', 'b')", None) == []


def test_returns_disallowed_call_outside_whitelist():
    code = "await read_file('a.txt')\nawait delete_file('a.txt')\n"
    assert find_disallowed_calls(code, ("read_file",)) == ["delete_file"]


def test_returns_nothing_when_all_calls_in_whitelist():
    code = "await read_file('a.txt')\nawait write_file('b.txt', 'x')\n"
    assert find_disallowed_calls(code, ("read_file", "write_file")) == []


def test_python_builtins_are_safelisted():
    # `print` and `len` aren't tools; the whitelist shouldn't have to enumerate them.
    code = "print(len('hello'))\nawait read_file('a.txt')\n"
    assert find_disallowed_calls(code, ("read_file",)) == []


def test_method_calls_are_not_flagged():
    # `response.get(...)` is a method call on a value, not a bare callable.
    # The AST walker rejects it (Attribute, not Name).
    code = "data = response.get('key')\nawait read_file('a.txt')\n"
    assert find_disallowed_calls(code, ("read_file",)) == []


def test_empty_whitelist_blocks_every_non_builtin_call():
    # `allowed-tools: []` in frontmatter means "allow nothing" — every bare
    # call to a non-builtin counts as disallowed.
    code = "await write_file('a', 'b')\nprint('done')\n"
    assert find_disallowed_calls(code, ()) == ["write_file"]


def test_disallowed_calls_are_sorted_and_deduplicated():
    code = "await zz_tool()\nawait aa_tool()\nawait zz_tool()\n"
    assert find_disallowed_calls(code, ()) == ["aa_tool", "zz_tool"]


def test_syntax_error_falls_back_to_no_blocking():
    # Unparseable code is the model's problem on the next turn; don't block
    # via the whitelist on a syntax error.
    assert find_disallowed_calls("def broken(", ()) == []


def test_safelist_includes_common_builtins():
    # Sanity check on the safelist surface — these would be very surprising
    # to drop without breaking real skill code.
    for name in ("print", "len", "range", "isinstance", "open"):
        assert name in PYTHON_BUILTINS_SAFELIST


def test_lambda_call_does_not_count():
    # ``(lambda: 1)()`` has a Call node whose func is a Lambda, not a Name.
    # Whitelist enforcement only cares about bare-name calls.
    assert find_disallowed_calls("x = (lambda: 1)()", ()) == []


# --- Wiring into the tool-approval gate -------------------------------------


def _make_fake_lite_state():
    """Minimal stand-in for ``CugaLiteState``. ``_check_skill_allowed_tools``
    only touches ``cuga_lite_metadata`` + ``chat_messages`` + ``step_count``,
    and ``_create_approval_interrupt`` reads ``cuga_lite_metadata`` for the
    policy fields we just stored."""
    from types import SimpleNamespace

    return SimpleNamespace(cuga_lite_metadata={}, chat_messages=[], step_count=0)


def test_gate_no_op_when_skill_allowed_tools_missing_from_config():
    from cuga.backend.cuga_graph.nodes.cuga_lite.tool_approval_handler import (
        ToolApprovalHandler,
    )

    state = _make_fake_lite_state()
    result = ToolApprovalHandler._check_skill_allowed_tools(
        state, "await read_file('a')", "content", config=None
    )
    assert result is None
    assert state.cuga_lite_metadata == {}


def test_gate_no_op_when_skill_allowed_tools_is_none_in_config():
    from cuga.backend.cuga_graph.nodes.cuga_lite.tool_approval_handler import (
        ToolApprovalHandler,
    )

    state = _make_fake_lite_state()
    result = ToolApprovalHandler._check_skill_allowed_tools(
        state,
        "await read_file('a')",
        "content",
        config={"configurable": {"skill_allowed_tools": None}},
    )
    assert result is None


def test_gate_returns_none_when_all_calls_in_whitelist():
    from cuga.backend.cuga_graph.nodes.cuga_lite.tool_approval_handler import (
        ToolApprovalHandler,
    )

    state = _make_fake_lite_state()
    result = ToolApprovalHandler._check_skill_allowed_tools(
        state,
        "await read_file('a')",
        "content",
        config={"configurable": {"skill_allowed_tools": ("read_file",)}},
    )
    assert result is None


def test_gate_routes_to_approval_when_disallowed_tool_present():
    """A disallowed bare call drops into the HITL approval flow with metadata
    set to a skill-whitelist-themed policy. Per the user's choice (option B),
    the user can still override on a case-by-case basis."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.tool_approval_handler import (
        ToolApprovalHandler,
    )

    state = _make_fake_lite_state()
    code = "await read_file('a')\nawait delete_file('a')\n"
    result = ToolApprovalHandler._check_skill_allowed_tools(
        state,
        code,
        "content",
        config={"configurable": {"skill_allowed_tools": ("read_file",)}},
    )

    assert result is not None, "disallowed call must trigger an approval interrupt"
    # Metadata is stashed onto state for the existing _create_approval_interrupt
    # to pick up — confirms we routed through the same HITL flow.
    assert state.cuga_lite_metadata["policy_type"] == "skill_allowed_tools"
    assert state.cuga_lite_metadata["required_tools"] == ["delete_file"]
    assert "allowed-tools" in state.cuga_lite_metadata["approval_message"]
