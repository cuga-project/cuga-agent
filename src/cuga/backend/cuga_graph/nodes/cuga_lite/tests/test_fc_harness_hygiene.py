"""Function-calling / hybrid harness hygiene, ported from Claude Code (free-code).

- Runtime notes are tagged and sent wrapped in ``<system-reminder>``; persisted text is unchanged.
- Old, large tool results are cleared from the *outbound* transcript (microcompact); in
  hybrid mode the stub names the variable the full result is saved in.
- A streak of near-identical native calls gets one runtime note per streak.
- ``execute_python`` runs with its own (longer) wall clock.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import (
    AgentGraphAdapter,
    _normalize_history_for_replay,
    _turn_start,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers import execute_python as ep
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.harness_messages import (
    HARNESS_KEY,
    for_model,
    harness_kind,
    harness_message,
    is_harness_message,
    wrap_reminder,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.repeat_calls import (
    REPEAT_NUDGE_KEY,
    detect_repeat_streak,
    repeat_nudge_text,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_result_clearing import (
    CLEARED_CODE,
    CLEARED_PLAIN,
    CLEARED_SAVED,
    clear_old_tool_results,
    saved_variable,
)

pytestmark = pytest.mark.unit

FC = {"cuga_lite_execution_mode": "function_calling"}
HYBRID = {"cuga_lite_execution_mode": "hybrid"}
_UNBOUND = object()
BIG = "x" * 5000


def _ai(*calls) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": n, "args": a, "id": i, "type": "tool_call"} for n, a, i in calls],
    )


def _tool(call_id: str, name: str, content: str = BIG, **kwargs) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id, name=name, **kwargs)


# ── runtime notes ────────────────────────────────────────────────────────────


def test_a_runtime_note_is_tagged_and_wrapped_only_on_the_way_out():
    note = harness_message("Do the thing.", "noop_answer")
    assert note.content == "Do the thing.", "persisted text stays byte-identical to the constant"
    assert harness_kind(note) == "noop_answer" and is_harness_message(note)
    assert not is_harness_message(HumanMessage(content="Do the thing."))

    out = for_model(note)
    assert out.content == "<system-reminder>\nDo the thing.\n</system-reminder>"
    assert wrap_reminder(out.content) == out.content, "never wrapped twice"


def test_the_tag_survives_as_a_bare_shell_and_a_dict():
    shell = BaseMessage(content="note", type="human", additional_kwargs={HARNESS_KEY: "repeat_calls"})
    assert is_harness_message(shell)
    assert is_harness_message({"type": "human", "content": "n", "additional_kwargs": {HARNESS_KEY: "x"}})


def test_replay_wraps_runtime_notes_and_leaves_user_turns_alone():
    shell = BaseMessage(content="shell note", type="human", additional_kwargs={HARNESS_KEY: "empty_reply"})
    history = [HumanMessage(content="user asks"), harness_message("note", "mode_violation"), shell]

    out = _normalize_history_for_replay(history)

    assert out[0].content == "user asks"
    assert out[1].content == wrap_reminder("note")
    assert isinstance(out[2], HumanMessage) and out[2].content == wrap_reminder("shell note")
    assert history[1].content == "note", "the persisted message is not mutated"


def test_turn_start_skips_runtime_notes_whatever_their_text():
    history = [
        HumanMessage(content="first request"),
        AIMessage(content="done"),
        HumanMessage(content="second request"),
        harness_message("some brand-new correction text", "repeat_calls"),
    ]
    assert _turn_start(history) == 2


# ── old tool results ─────────────────────────────────────────────────────────


def _transcript(n: int) -> List[BaseMessage]:
    msgs: List[BaseMessage] = [HumanMessage(content="go")]
    for i in range(n):
        msgs += [_ai(("get", {"id": i}, f"c{i}")), _tool(f"c{i}", "get")]
    return msgs


def test_only_old_large_results_are_cleared_and_the_input_is_untouched():
    msgs = _transcript(5)
    out = clear_old_tool_results(msgs, keep_recent=3, min_chars=2000)

    tools = [m for m in out if isinstance(m, ToolMessage)]
    assert [t.content == BIG for t in tools] == [False, False, True, True, True]
    assert tools[0].content == CLEARED_PLAIN.format(name="get", chars=len(BIG))
    assert all(m.content == BIG for m in msgs if isinstance(m, ToolMessage)), "outbound copy only"
    assert tools[0].tool_call_id == "c0", "the reply still answers its call"


def test_off_small_errors_and_tool_search_are_never_cleared():
    msgs = _transcript(4)
    assert clear_old_tool_results(msgs, keep_recent=0, min_chars=10) == msgs

    msgs[2] = _tool("c0", "get", "short")
    msgs[4] = _tool("c1", "tool_search", BIG)
    msgs[6] = _tool("c2", "get", BIG, status="error")
    out = clear_old_tool_results(msgs, keep_recent=1, min_chars=2000)
    assert [m.content for m in out if isinstance(m, ToolMessage)] == ["short", BIG, BIG, BIG]


def test_results_the_model_has_not_answered_are_never_cleared():
    msgs = [HumanMessage(content="go"), _ai(*[("get", {"id": i}, f"c{i}") for i in range(5)])]
    msgs += [_tool(f"c{i}", "get") for i in range(5)]
    out = clear_old_tool_results(msgs, keep_recent=1, min_chars=10)
    assert all(m.content == BIG for m in out if isinstance(m, ToolMessage))


def test_hybrid_stub_names_the_saved_variable_only_while_it_exists():
    saved = _tool("c0", "get", BIG, artifact={"saved_as": "get_result"})
    noted = _tool("c1", "get", BIG + "\n[Result saved as variable `get_result_2` (this call's output only)]")
    code = _tool("c2", "execute_python", BIG)
    msgs = [HumanMessage(content="go"), _ai(("get", {}, "c0")), saved, _ai(("get", {}, "c1")), noted]
    msgs += [_ai(("execute_python", {"code": "x"}, "c2")), code, _ai(("get", {}, "c3")), _tool("c3", "get")]

    assert saved_variable(noted) == "get_result_2", "the reply's own note is the fallback"
    out = clear_old_tool_results(msgs, keep_recent=1, min_chars=2000, var_exists=lambda n: n == "get_result")
    by_id = {m.tool_call_id: m.content for m in out if isinstance(m, ToolMessage)}

    assert by_id["c0"] == CLEARED_SAVED.format(name="get", chars=len(BIG), var="get_result")
    assert by_id["c1"].startswith("[Earlier result of get cleared") and "Call the tool again" in by_id["c1"]
    assert by_id["c2"] == CLEARED_CODE.format(chars=len(BIG))
    assert by_id["c3"] == BIG


# ── repeated calls ───────────────────────────────────────────────────────────


def test_a_streak_varying_one_argument_is_detected_from_its_first_call():
    msgs = [_ai(("list", {"page": i, "size": 5}, f"c{i}")) for i in range(4)]
    streak = detect_repeat_streak(msgs)
    assert (streak.tool_name, streak.length, streak.first_call_id, streak.varying_arg) == (
        "list",
        4,
        "c0",
        "page",
    )
    assert "execute_python" in repeat_nudge_text(streak, hybrid=True)
    assert "execute_python" not in repeat_nudge_text(streak, hybrid=False)


def test_identical_calls_are_a_streak_too():
    streak = detect_repeat_streak([_ai(("get", {"id": 1}, f"c{i}")) for i in range(3)])
    assert streak.length == 3 and streak.varying_arg is None
    assert "same arguments" in repeat_nudge_text(streak, hybrid=True)


def test_what_ends_a_streak():
    same = [_ai(("get", {"id": i}, f"c{i}")) for i in range(2)]
    assert detect_repeat_streak(same + [_ai(("execute_python", {"code": ""}, "x"))]) is None
    assert (
        detect_repeat_streak([_ai(("get", {"a": 1, "b": 1}, "c0")), _ai(("get", {"a": 2, "b": 2}, "c1"))])
        is None
    )
    # tool_search between calls is not work and does not break the streak
    searched = [same[0], _ai(("tool_search", {"query": "q"}, "s")), same[1]]
    assert detect_repeat_streak(searched).length == 2
    # varying a different field starts a new streak
    mixed = [_ai(("get", {"a": 0, "b": 0}, "c0")), _ai(("get", {"a": 1, "b": 0}, "c1"))]
    mixed += [_ai(("get", {"a": 1, "b": 1}, "c2"))]
    assert detect_repeat_streak(mixed).first_call_id == "c1"


# ── wired into the function-calling turn ─────────────────────────────────────


class _Model:
    def __init__(self, response: Any):
        self.response = response
        self.seen: List[list] = []

    async def ainvoke(self, messages, config=None, **kwargs):
        self.seen.append(list(messages))
        return self.response


def _adapter() -> AgentGraphAdapter:
    return AgentGraphAdapter(
        tracker=MagicMock(),
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref={},
        base_tool_provider=None,
    )


def _state(messages, metadata=None):
    return SimpleNamespace(
        chat_messages=messages,
        step_count=0,
        cuga_lite_metadata=metadata if metadata is not None else {},
        mcp_few_shot_messages=[],
        cuga_lite_max_steps=None,
    )


async def _turn(state, configurable, response=None):
    model = _Model(
        response
        or AIMessage(content="", tool_calls=[{"name": "get", "args": {}, "id": "n", "type": "tool_call"}])
    )
    cmd = await _adapter().execute_call_model_fc(
        state=state,
        config=None,
        configurable=configurable,
        active_model=_UNBOUND,
        bound=model,
        invoke_config={},
        system_content="SYS",
        modified_messages=list(state.chat_messages),
        budget_exhausted=False,
        playbook_fired=False,
    )
    return cmd, model.seen[0]


@pytest.fixture(autouse=True)
def _policy_off(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)


def _looping(n: int) -> List[BaseMessage]:
    msgs: List[BaseMessage] = [HumanMessage(content="rename every playlist")]
    for i in range(n):
        msgs += [_ai(("rename", {"playlist_id": i}, f"c{i}")), _tool(f"c{i}", "rename", "ok")]
    return msgs


@pytest.mark.asyncio
async def test_a_streak_gets_one_note_persisted_ahead_of_the_reply():
    cmd, outbound = await _turn(_state(_looping(3)), HYBRID)

    persisted = cmd.update["chat_messages"]
    note = persisted[-2]
    assert is_harness_message(note) and harness_kind(note) == "repeat_calls"
    assert "execute_python" in note.content and "`playlist_id`" in note.content
    assert isinstance(persisted[-1], AIMessage), "the note sits before the model's reply"
    assert outbound[-1].content == wrap_reminder(note.content), "sent to the model wrapped"
    assert cmd.update["cuga_lite_metadata"][REPEAT_NUDGE_KEY] == "c0"


@pytest.mark.asyncio
async def test_a_growing_streak_is_not_nudged_again_but_a_new_one_is():
    cmd, _ = await _turn(_state(_looping(5), metadata={REPEAT_NUDGE_KEY: "c0"}), HYBRID)
    assert not any(is_harness_message(m) for m in cmd.update["chat_messages"])

    fresh = _looping(3) + [_ai(("get", {"id": 1}, "g0")), _tool("g0", "get", "a")]
    fresh += [_ai(("get", {"id": 2}, "g1")), _tool("g1", "get", "b"), _ai(("get", {"id": 3}, "g2"))]
    fresh += [_tool("g2", "get", "c")]
    cmd, _ = await _turn(_state(fresh, metadata={REPEAT_NUDGE_KEY: "c0"}), FC)
    assert cmd.update["cuga_lite_metadata"][REPEAT_NUDGE_KEY] == "g0"


@pytest.mark.asyncio
async def test_below_the_threshold_or_switched_off_there_is_no_note():
    cmd, _ = await _turn(_state(_looping(2)), HYBRID)
    assert not any(is_harness_message(m) for m in cmd.update["chat_messages"])

    cmd, _ = await _turn(_state(_looping(4)), {**HYBRID, "cuga_lite_repeat_call_nudge_after": 0})
    assert not any(is_harness_message(m) for m in cmd.update["chat_messages"])


@pytest.mark.asyncio
async def test_old_results_are_cleared_outbound_and_kept_in_state():
    state = _state(_transcript(5))
    cmd, outbound = await _turn(state, {**FC, "cuga_lite_repeat_call_nudge_after": 0})

    sent = [m.content for m in outbound if isinstance(m, ToolMessage)]
    assert sent[:2] == [CLEARED_PLAIN.format(name="get", chars=len(BIG))] * 2 and sent[2:] == [BIG] * 3
    kept = [m.content for m in cmd.update["chat_messages"] if isinstance(m, ToolMessage)]
    assert kept == [BIG] * 5, "persisted history keeps every result"

    _, outbound = await _turn(_state(_transcript(5)), {**FC, "cuga_lite_tool_result_keep_recent": 0})
    assert all(m.content == BIG for m in outbound if isinstance(m, ToolMessage))


# ── execute_python wall clock ────────────────────────────────────────────────


def test_execute_python_timeout_reads_its_own_setting(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "cuga_lite_execute_python_timeout", 90, raising=False)
    assert ep.execute_python_timeout() == 90.0
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_execute_python_timeout", 0, raising=False)
    assert ep.execute_python_timeout() == float(settings.advanced_features.sandbox_execution_timeout)


@pytest.mark.asyncio
async def test_run_code_passes_the_execute_python_timeout(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "cuga_lite_execute_python_timeout", 90, raising=False)
    manager = MagicMock()
    manager.get_variable_names.return_value = []
    state = SimpleNamespace(variables_manager=manager)
    with patch.object(CodeExecutor, "eval_with_tools_async", new=AsyncMock(return_value=("out", {}))) as run:
        output, _, _ = await ep.run_code(state, "print(1)", {})
    assert output == "out" and run.await_args.kwargs["timeout"] == 90.0


@pytest.mark.asyncio
async def test_the_local_executor_gets_the_passed_timeout():
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor

    local = MagicMock()
    local.execute = AsyncMock(return_value="done")
    manager = MagicMock()
    manager.get_variable_names.return_value = []
    state = SimpleNamespace(variables_manager=manager, thread_id=None)
    with patch.object(CodeExecutor, "_get_local_executor", return_value=local):
        await CodeExecutor.eval_with_tools_async("print(1)", {}, state, mode="local", timeout=90)
    assert local.execute.await_args.kwargs["timeout"] == 90


# ── tool failures inside execute_python ──────────────────────────────────────


def test_only_the_bare_error_reply_counts_as_a_failure():
    assert ep._failure_message({"error": "page_limit must be <= 20"}) == "page_limit must be <= 20"
    assert ep._failure_message({"error": "x", "items": []}) is None, "a payload with an error field is data"
    assert ep._failure_message({"error": ""}) is None
    assert ep._failure_message([{"error": "x"}]) is None
    assert ep._failure_message("error") is None


@pytest.mark.asyncio
async def test_a_failure_reply_raises_with_its_reason_and_is_still_recorded():
    async def listing(**kwargs):
        return {"error": "Invalid arguments for listing:\n- not allowed: page_limit must be <= 20"}

    async def ok(**kwargs):
        return [{"id": 1}]

    context = ep.code_context(
        SimpleNamespace(variables_manager=MagicMock(get_variable_names=lambda: [])),
        {"listing": listing, "ok": ok},
    )
    calls: List[str] = []
    token = ep.CODE_CALLS.set(calls)
    try:
        assert await context["ok"]() == [{"id": 1}]
        with pytest.raises(ep.ToolCallError, match=r"(?s)listing failed: .*page_limit must be <= 20"):
            await context["listing"](page_limit=100)
    finally:
        ep.CODE_CALLS.reset(token)
    assert calls == ["ok", "listing"]


@pytest.mark.asyncio
async def test_the_reason_reaches_execute_python_output_instead_of_a_type_error():
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor

    async def listing(page_limit=5, **kwargs):
        if page_limit > 20:
            return {"error": "Invalid arguments for listing:\n- not allowed: page_limit must be <= 20"}
        return [{"created_at": "2023-05-01"}]

    manager = MagicMock()
    manager.get_variable_names.return_value = []
    state = SimpleNamespace(variables_manager=manager, thread_id=None)
    code = "threads = await listing(page_limit=100)\nfor t in threads:\n    print(t['created_at'])"
    local = CodeExecutor._get_local_executor()
    with patch.object(CodeExecutor, "_get_local_executor", return_value=local):
        output, _ = await CodeExecutor.eval_with_tools_async(
            code, ep.code_context(state, {"listing": listing}), state, mode="local", timeout=10
        )
    assert "page_limit must be <= 20" in output
    assert "string indices" not in output
