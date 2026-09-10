"""call_model must end the turn once a terminal tool budget is spent.

Without this, exhaustion is 'recoverable' in a way that helps nobody: every tool
call raises, so the model burns one full LLM call per retry until the step limit
trips (cuga_lite_max_steps, default 70) and the task ends with
'Maximum step limit reached' — instead of the answer it could have written from
data it already had.

The fix is one grace turn with the tools withheld. Withholding is the part that
matters: an instruction not to call tools is a request, an empty tool list is a
constraint.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import END
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import (
    TOOL_BUDGET_EXHAUSTED_INSTRUCTION,
    create_call_model_node,
)

pytestmark = pytest.mark.unit

_SUMMARIZE = "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization"


class _TestAdapter(CoreGraphAdapter):
    messages_key = "chat_messages"
    execute_node_name = "sandbox"
    metadata_key = "cuga_lite_metadata"

    def __init__(self):
        self.bind_tools_calls = 0
        self.auto_continue_calls = 0

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return list(state.chat_messages or [])

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        return override if override is not None else getattr(state, "_max_steps", 50)

    async def resolve_bind_tools(self, state, model, configurable, config):
        self.bind_tools_calls += 1
        return model

    async def classify_auto_continue(self, state, model, content, reasoning):
        self.auto_continue_calls += 1
        return True  # would loop forever if consulted while exhausted


def _make_state(*, exhausted: bool, step_count: int = 0):
    vm = MagicMock()
    vm.get_variable_names.return_value = []
    return SimpleNamespace(
        chat_messages=[HumanMessage(content="list every invoice")],
        step_count=step_count,
        _max_steps=50,
        prepared_prompt="You are a helpful agent.",
        cuga_lite_metadata={},
        variables_storage=None,
        variable_counter_state=None,
        variable_creation_order=None,
        variables_manager=vm,
        tool_budget_exhausted=exhausted,
    )


def _mock_model(content: str):
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=SimpleNamespace(content=content, additional_kwargs={}))
    return model


def _mock_settings():
    return SimpleNamespace(
        advanced_features=SimpleNamespace(cuga_lite_max_steps=50),
        policy=SimpleNamespace(enabled=False),
    )


@pytest.mark.asyncio
@patch(_SUMMARIZE, new_callable=AsyncMock)
async def test_exhausted_turn_ends_instead_of_thrashing(mock_summarize):
    """One model call, then END — not 70 retries into a step-limit error."""
    mock_summarize.side_effect = lambda messages, *a, **kw: messages

    adapter = _TestAdapter()
    model = _mock_model("You have 42 overdue invoices, totalling $18,300.")
    node = create_call_model_node(adapter, model, _mock_settings())

    result = await node(_make_state(exhausted=True), config=None)

    assert isinstance(result, Command)
    assert result.goto == END
    assert result.update["final_answer"] == "You have 42 overdue invoices, totalling $18,300."
    assert result.update["execution_complete"] is True
    assert model.ainvoke.await_count == 1


@pytest.mark.asyncio
@patch(_SUMMARIZE, new_callable=AsyncMock)
async def test_tools_are_withheld_and_the_instruction_is_sent(mock_summarize):
    """The synthesis pass must go out with no tools bound and the instruction
    appended — asking without withholding is not a constraint."""
    mock_summarize.side_effect = lambda messages, *a, **kw: messages

    adapter = _TestAdapter()
    model = _mock_model("Here is what I found.")
    node = create_call_model_node(adapter, model, _mock_settings())

    await node(_make_state(exhausted=True), config=None)

    assert adapter.bind_tools_calls == 0, "tools were bound on the final synthesis pass"
    sent = model.ainvoke.await_args[0][0]
    assert sent[-1] == {"role": "user", "content": TOOL_BUDGET_EXHAUSTED_INSTRUCTION}


@pytest.mark.asyncio
@patch(_SUMMARIZE, new_callable=AsyncMock)
async def test_the_instruction_is_not_persisted_to_history(mock_summarize):
    """Outbound only, like the variables addendum (#600): it is rebuilt every
    turn, so persisting it would accumulate a copy per exhausted turn."""
    mock_summarize.side_effect = lambda messages, *a, **kw: messages

    adapter = _TestAdapter()
    node = create_call_model_node(adapter, _mock_model("Final answer."), _mock_settings())

    result = await node(_make_state(exhausted=True), config=None)

    persisted = [getattr(m, "content", "") for m in result.update["chat_messages"]]
    assert TOOL_BUDGET_EXHAUSTED_INSTRUCTION not in persisted


@pytest.mark.asyncio
@patch(_SUMMARIZE, new_callable=AsyncMock)
async def test_code_emitted_while_exhausted_is_not_executed(mock_summarize):
    """A model that ignores the instruction and emits code anyway must not be
    routed to the sandbox — every call there would raise on the same budget."""
    mock_summarize.side_effect = lambda messages, *a, **kw: messages

    adapter = _TestAdapter()
    model = _mock_model("```python\nawait call_api('billing', 'list', {})\n```")
    node = create_call_model_node(adapter, model, _mock_settings())

    result = await node(_make_state(exhausted=True), config=None)

    assert result.goto == END, "exhausted turn routed generated code to the executor"
    assert result.update.get("script") is None


@pytest.mark.asyncio
@patch(_SUMMARIZE, new_callable=AsyncMock)
async def test_auto_continue_cannot_reopen_an_exhausted_turn(mock_summarize):
    """classify_auto_continue returning True must not restart the loop — with
    no budget left that is an infinite call_model cycle."""
    mock_summarize.side_effect = lambda messages, *a, **kw: messages

    adapter = _TestAdapter()
    node = create_call_model_node(adapter, _mock_model("Partial answer."), _mock_settings())

    result = await node(_make_state(exhausted=True), config=None)

    assert result.goto == END
    assert adapter.auto_continue_calls == 0


@pytest.mark.asyncio
@patch(_SUMMARIZE, new_callable=AsyncMock)
async def test_normal_turns_are_untouched(mock_summarize):
    """With budget remaining, nothing changes: tools bound, code executed."""
    mock_summarize.side_effect = lambda messages, *a, **kw: messages

    adapter = _TestAdapter()
    model = _mock_model("```python\nprint('hi')\n```")
    node = create_call_model_node(adapter, model, _mock_settings())

    result = await node(_make_state(exhausted=False), config=None)

    assert result.goto == "sandbox"
    assert result.update["script"] == "print('hi')"
    assert adapter.bind_tools_calls == 1
    sent = model.ainvoke.await_args[0][0]
    assert all(m.get("content") != TOOL_BUDGET_EXHAUSTED_INSTRUCTION for m in sent)


@pytest.mark.asyncio
@patch(_SUMMARIZE, new_callable=AsyncMock)
async def test_state_without_the_field_behaves_normally(mock_summarize):
    """Graphs/states that predate the flag must not be forced into synthesis."""
    mock_summarize.side_effect = lambda messages, *a, **kw: messages

    adapter = _TestAdapter()
    state = _make_state(exhausted=False)
    del state.tool_budget_exhausted

    node = create_call_model_node(adapter, _mock_model("```python\nprint('hi')\n```"), _mock_settings())
    result = await node(state, config=None)

    assert result.goto == "sandbox"


@pytest.mark.asyncio
@patch(_SUMMARIZE, new_callable=AsyncMock)
async def test_grace_turn_survives_the_step_wall(mock_summarize):
    """The grace turn must not be killed by the step limit.

    This is the case the grace turn exists for. Summarization is what lets a
    looping turn reach `cuga_lite_max_steps` at all, so a runaway arrives here
    having already spent its budget — and enforcing the step limit on the
    synthesis pass replaces the answer the model just wrote with "Maximum step
    limit reached". The turn ends either way; the only difference is whether the
    user gets the answer.

    Every other test in this file seeds step_count=0, which is why this hid.
    """
    mock_summarize.side_effect = lambda messages, *a, **kw: messages

    adapter = _TestAdapter()
    answer = "You have 42 overdue invoices, totalling $18,300."
    node = create_call_model_node(adapter, _mock_model(answer), _mock_settings())

    # step_count=50 with max_steps=50 -> new_step_count 51 trips the limit.
    result = await node(_make_state(exhausted=True, step_count=50), config=None)

    assert result.goto == END
    assert result.update["final_answer"] == answer, (
        f"step limit overwrote the synthesised answer: {result.update['final_answer']!r}"
    )


@pytest.mark.asyncio
@patch(_SUMMARIZE, new_callable=AsyncMock)
async def test_step_limit_still_applies_to_normal_turns(mock_summarize):
    """The exemption is scoped to the grace turn only — an ordinary turn at the
    wall must still stop, or the step limit stops meaning anything."""
    mock_summarize.side_effect = lambda messages, *a, **kw: messages

    adapter = _TestAdapter()
    node = create_call_model_node(adapter, _mock_model("```python\nprint(1)\n```"), _mock_settings())

    result = await node(_make_state(exhausted=False, step_count=50), config=None)

    assert result.goto == END
    assert "Maximum step limit" in (result.update.get("final_answer") or "")
