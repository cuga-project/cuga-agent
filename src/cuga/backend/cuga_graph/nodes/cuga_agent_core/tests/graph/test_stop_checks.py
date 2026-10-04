"""Stop checks: run when the model is about to finish, each may send it back once per turn."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import END

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph import stop_checks
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import create_call_model_node
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.stop_checks import (
    STOP_CHECK_PREFIX,
    STOP_CHECKS_FIRED_KEY,
    StopContext,
    configured_stop_checks,
    pending_todos,
    register_stop_check,
    run_stop_checks,
)

pytestmark = pytest.mark.unit

_SUMMARIZE = "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization"


@pytest.fixture(autouse=True)
def _restore_registry():
    saved = dict(stop_checks._REGISTRY)
    yield
    stop_checks._REGISTRY.clear()
    stop_checks._REGISTRY.update(saved)


def _ctx(todos=None, answer="done") -> StopContext:
    return StopContext(answer=answer, messages=[], todos=todos or [])


# ── pending_todos ───────────────────────────────────────────────────────────


def test_pending_todos_objects_to_steps_never_started():
    todos = [
        {"text": "get orders", "status": "completed"},
        {"text": "refund the late ones", "status": "pending"},
        {"text": "answer", "status": "pending"},
    ]
    out = pending_todos(_ctx(todos))
    assert out is not None and "1 unfinished step(s): refund the late ones" in out


@pytest.mark.parametrize(
    "todos",
    [
        [],
        [{"text": "a", "status": "completed"}, {"text": "answer", "status": "pending"}],
        [{"text": "a", "status": "in_progress"}, {"text": "answer", "status": "in_progress"}],
        [{"text": "only step", "status": "pending"}],
    ],
)
def test_pending_todos_lets_finished_or_current_plans_through(todos):
    assert pending_todos(_ctx(todos)) is None


# ── runner ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_objection_wins_and_is_prefixed():
    register_stop_check("_t_quiet", lambda ctx: None)
    register_stop_check("_t_loud", lambda ctx: "cite your source")
    register_stop_check("_t_never", lambda ctx: pytest.fail("checks after an objection must not run"))

    assert await run_stop_checks(_ctx(), ["_t_quiet", "_t_loud", "_t_never"], fired=[]) == (
        "_t_loud",
        STOP_CHECK_PREFIX + "cite your source",
    )


@pytest.mark.asyncio
async def test_a_check_that_already_fired_this_turn_is_skipped():
    register_stop_check("_t_loud", lambda ctx: "again")
    assert await run_stop_checks(_ctx(), ["_t_loud"], fired=["_t_loud"]) is None


@pytest.mark.asyncio
async def test_async_checks_and_import_paths_work():
    async def check(ctx):
        return "async objection" if ctx.answer == "x" else None

    stop_checks._t_async_check = check  # importable as "<module>:_t_async_check"
    try:
        spec = f"{stop_checks.__name__}:_t_async_check"
        assert await run_stop_checks(_ctx(answer="x"), [spec], fired=[]) == (
            spec,
            STOP_CHECK_PREFIX + "async objection",
        )
    finally:
        del stop_checks._t_async_check


@pytest.mark.asyncio
async def test_broken_or_unknown_checks_never_block_the_answer():
    def boom(ctx):
        raise RuntimeError("bug in a check")

    register_stop_check("_t_boom", boom)
    assert await run_stop_checks(_ctx(), ["_t_boom", "no_such_check", "no.such.module:fn"], fired=[]) is None


def test_configured_checks_read_configurable_first():
    s = SimpleNamespace(advanced_features=SimpleNamespace(cuga_lite_stop_checks=["pending_todos"]))
    assert configured_stop_checks({}, s) == ["pending_todos"]
    assert configured_stop_checks({"cuga_lite_stop_checks": []}, s) == []
    assert configured_stop_checks({"cuga_lite_stop_checks": "a, b"}, s) == ["a", "b"]


# ── shared call_model ───────────────────────────────────────────────────────


class _Adapter(CoreGraphAdapter):
    messages_key = "chat_messages"
    execute_node_name = "sandbox"
    metadata_key = "cuga_lite_metadata"

    def __init__(self, objection=None):
        self.objection = objection
        self.seen_answer = None

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return list(state.chat_messages or [])

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        return 50

    async def check_before_finish(self, state, configurable, answer):
        self.seen_answer = answer
        return self.objection


def _state(metadata=None):
    vm = MagicMock()
    vm.get_variable_names.return_value = []
    return SimpleNamespace(
        chat_messages=[HumanMessage(content="do task")],
        step_count=0,
        prepared_prompt="SYS",
        cuga_lite_metadata=metadata or {},
        variables_storage=None,
        variable_counter_state=None,
        variable_creation_order=None,
        variables_manager=vm,
        tool_budget_exhausted=False,
    )


async def _run(adapter, state):
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=SimpleNamespace(content="All done.", additional_kwargs={}))
    settings = SimpleNamespace(
        advanced_features=SimpleNamespace(cuga_lite_max_steps=50), policy=SimpleNamespace(enabled=False)
    )
    with patch(_SUMMARIZE, new_callable=AsyncMock) as summarize:
        summarize.side_effect = lambda m, *a, **kw: m
        return await create_call_model_node(adapter, model, settings)(state, {})


@pytest.mark.asyncio
async def test_an_objection_sends_the_model_back_with_the_correction():
    adapter = _Adapter(objection=("pending_todos", STOP_CHECK_PREFIX + "finish step 2"))
    cmd = await _run(adapter, _state(metadata={STOP_CHECKS_FIRED_KEY: ["other"]}))

    assert adapter.seen_answer == "All done."
    assert cmd.goto == "call_model"
    persisted = cmd.update["chat_messages"]
    assert (
        persisted[-2].content == "All done." and persisted[-1].content == STOP_CHECK_PREFIX + "finish step 2"
    )
    assert cmd.update["cuga_lite_metadata"][STOP_CHECKS_FIRED_KEY] == ["other", "pending_todos"]
    assert cmd.update["step_count"] == 1


@pytest.mark.asyncio
async def test_no_objection_ends_the_turn():
    cmd = await _run(_Adapter(objection=None), _state())
    assert cmd.goto == END and cmd.update["final_answer"] == "All done."
