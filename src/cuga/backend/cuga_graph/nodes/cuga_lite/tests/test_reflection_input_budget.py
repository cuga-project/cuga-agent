"""Reflection and pre-execute VERIFY inputs are sized from the model and keep the newest content (#1001)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

pytestmark = pytest.mark.unit

SANDBOX = "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node"


def _long_run() -> list:
    """~42k chars of history: the old 30,000-char head cut dropped everything after step 4."""
    msgs = [HumanMessage(content="split the amazon prime bill")]
    for i in range(1, 8):
        msgs.append(AIMessage(content=f"step_{i}()"))
        msgs.append(HumanMessage(content=f"Execution output step {i}: " + "x" * 6000))
    msgs.append(AIMessage(content="lookup_bill()"))
    msgs.append(HumanMessage(content="Execution output NEWEST: {'bill_id': 8216, 'amount': 46.67}"))
    return msgs


def _adapter():
    adapter = MagicMock()
    adapter._tools_context = {}
    adapter._weak_schema_tool_names = frozenset()
    adapter._observed_tool_shapes = {}
    adapter._tracker = MagicMock()
    adapter.messages_key = "chat_messages"
    adapter.get_messages = MagicMock(return_value=[])
    adapter.resolve_max_steps = MagicMock(return_value=1000)
    return adapter


def _state(**kwargs):
    variables_manager = MagicMock()
    variables_manager.get_variable_names = MagicMock(return_value=[])
    variables_manager.get_variable = MagicMock(return_value=None)
    variables_manager.remove_variable = MagicMock()
    variables_manager.add_variable = MagicMock()
    variables_manager.get_variables_summary = MagicMock(return_value="txn 8216 amount=46.67")
    base = dict(
        variables_manager=variables_manager,
        chat_messages=_long_run(),
        tool_calls=[],
        step_count=0,
        script="await pay(amount=46.67)",
        thread_id="t",
        variables_storage={},
        variable_counter_state=0,
        variable_creation_order=[],
        reflection_apps=[],
        reflection_enable_find_tools=False,
        reflection_skills_enabled=False,
        reflection_skills_prompt_section="",
        verify_revise_streak=0,
        verify_revise_total=0,
        tool_calls_used_run=0,
        tool_calls_used_thread=0,
        sub_task="split the amazon prime bill",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _capturing_chain(content: str):
    captured: dict = {}
    chain = MagicMock()

    async def _ainvoke(payload, config=None):
        captured.update(payload)
        return SimpleNamespace(content=content)

    chain.ainvoke = _ainvoke
    return chain, captured


async def _run_node(*, reflection: bool, verify: bool, reflection_chain=None, verify_chain=None):
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

    node = create_sandbox_node(_adapter(), base_thread_id="t", base_apps_list=[])
    noop = MagicMock()
    noop.ainvoke = AsyncMock(return_value=SimpleNamespace(content=""))
    with (
        patch(f"{SANDBOX}.CodeExecutor.eval_with_tools_async", AsyncMock(return_value=("executed", {}))),
        patch(f"{SANDBOX}.settings.policy.enabled", False),
        patch(f"{SANDBOX}.reflection_task", return_value=reflection_chain or noop),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute.verify_task",
            return_value=verify_chain or noop,
        ),
    ):
        return await node(
            _state(),
            config={
                "configurable": {
                    "reflection_enabled": reflection,
                    "pre_execute_verify_enabled": verify,
                    "llm": MagicMock(spec=[]),  # unknown model: DEFAULT_CONTEXT_SIZE
                }
            },
        )


@pytest.mark.asyncio
async def test_sandbox_reflection_sees_newest_history_beyond_30k_chars():
    chain, captured = _capturing_chain("STATUS: ok\nNEXT: task complete.")
    await _run_node(reflection=True, verify=False, reflection_chain=chain)

    history = captured["agent_history"]
    assert len(history) > 30_000
    assert "NEWEST" in history, "the newest message reaches reflection"
    assert "step 1:" in history, "the whole run fits a 131,072-token budget"


@pytest.mark.asyncio
async def test_sandbox_verify_sees_newest_history_beyond_30k_chars():
    chain, captured = _capturing_chain("GATE: ok")
    await _run_node(reflection=False, verify=True, verify_chain=chain)

    history = captured["agent_history"]
    assert "NEWEST" in history, "the retrieved amount the write is grounded in reaches VERIFY"
    assert "46.67" in captured["write_arguments"]


@pytest.mark.asyncio
async def test_sandbox_verify_budget_follows_the_context_fraction_setting():
    chain, captured = _capturing_chain("GATE: ok")
    with patch(
        f"{SANDBOX}.settings.advanced_features.pre_execute_verify_context_fraction", 0.27, create=True
    ):
        await _run_node(reflection=False, verify=True, verify_chain=chain)

    history = captured["agent_history"]
    # After the default 25% completion reserve, this leaves a small input budget.
    assert len(history) < 8_000
    assert "NEWEST" in history
    assert "earlier messages omitted to fit the context budget]" in history


@pytest.mark.asyncio
@pytest.mark.parametrize("reflection", [False, True])
async def test_cold_budget_lookup_runs_outside_the_event_loop_thread(reflection):
    import threading

    event_loop_thread = threading.get_ident()
    lookup_threads = []

    def budget(*args, **kwargs):
        lookup_threads.append(threading.get_ident())
        return 200_000

    target = SANDBOX if reflection else "cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute"
    chain, captured = _capturing_chain("STATUS: ok" if reflection else "GATE: ok")
    with patch(f"{target}.model_input_budget_chars", side_effect=budget):
        await _run_node(
            reflection=reflection, verify=not reflection, reflection_chain=chain, verify_chain=chain
        )

    assert captured, "the prompt must reach the model after sizing"
    assert lookup_threads
    assert all(thread != event_loop_thread for thread in lookup_threads)


def test_ordered_variables_summary_lists_read_variables_first_then_newest():
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import CugaLiteState
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import ordered_variables_summary

    state = CugaLiteState()
    mgr = state.variables_manager
    for name in ("bill", "contacts", "cards", "history", "notes"):
        mgr.add_variable({"name": name}, name=name)

    summary = ordered_variables_summary(mgr, "await pay(amount=bill['amount'], to=contacts[0])")

    order = [line[3:] for line in summary.splitlines() if line.startswith("## ")]
    assert order == ["contacts", "bill", "notes", "history", "cards"]


def test_get_variables_summary_preserve_order_keeps_given_order():
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import CugaLiteState

    mgr = CugaLiteState().variables_manager
    for name in ("a", "b", "c"):
        mgr.add_variable(1, name=name)

    default = mgr.get_variables_summary(variable_names=["c", "a"])
    kept = mgr.get_variables_summary(variable_names=["c", "a"], preserve_order=True)

    assert default.index("## a") < default.index("## c"), "default stays in creation order"
    assert kept.index("## c") < kept.index("## a")
