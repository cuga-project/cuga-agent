"""The ``execute_call_model_fc`` seam in the shared ``call_model`` node.

The base adapter returns ``None`` and the CodeAct path runs exactly as before —
that is the Supervisor graph and every default CugaLite run. An adapter that
returns a ``Command`` short-circuits the node: the CodeAct invocation, response
normalisation and routing never run, so the two paths are never blended.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph import END
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import create_call_model_node

pytestmark = pytest.mark.unit

_SUMMARIZE = "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization"
_BOUND = object()  # what resolve_bind_tools hands back; the hook must receive exactly this


class _BaseAdapter(CoreGraphAdapter):
    messages_key = "chat_messages"
    execute_node_name = "sandbox"
    metadata_key = "cuga_lite_metadata"

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return list(state.chat_messages or [])

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        return override if override is not None else 50

    async def resolve_bind_tools(self, state, model, configurable, config):
        return model


class _ShortCircuitAdapter(_BaseAdapter):
    def __init__(self):
        self.seen_kwargs = None

    async def resolve_bind_tools(self, state, model, configurable, config):
        return _BOUND

    async def execute_call_model_fc(self, **kwargs) -> Optional[Command]:
        self.seen_kwargs = kwargs
        return Command(goto="tool_exec", update={"chat_messages": [], "script": None})


def _make_state():
    vm = MagicMock()
    vm.get_variable_names.return_value = []
    return SimpleNamespace(
        chat_messages=[HumanMessage(content="do task")],
        step_count=0,
        prepared_prompt="You are a helpful agent.",
        cuga_lite_metadata={},
        variables_storage=None,
        variable_counter_state=None,
        variable_creation_order=None,
        variables_manager=vm,
        tool_budget_exhausted=False,
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
async def test_base_hook_returns_none():
    assert (
        await _BaseAdapter().execute_call_model_fc(
            state=None,
            config=None,
            configurable={},
            active_model=None,
            bound=None,
            invoke_config={},
            system_content="",
            modified_messages=[],
            budget_exhausted=False,
            playbook_fired=False,
        )
        is None
    )


@pytest.mark.asyncio
@patch(_SUMMARIZE, new_callable=AsyncMock)
async def test_none_from_the_hook_leaves_the_codeact_path_untouched(mock_summarize):
    mock_summarize.side_effect = lambda messages, *a, **kw: messages
    model = _mock_model("Plain answer, no code.")
    node = create_call_model_node(_BaseAdapter(), model, _mock_settings())

    result = await node(_make_state(), config=None)

    assert model.ainvoke.await_count == 1, "the CodeAct invocation must still happen"
    assert isinstance(result, Command) and result.goto == END
    assert result.update["final_answer"] == "Plain answer, no code."


@pytest.mark.asyncio
@patch(_SUMMARIZE, new_callable=AsyncMock)
async def test_a_command_from_the_hook_short_circuits_the_node(mock_summarize):
    mock_summarize.side_effect = lambda messages, *a, **kw: messages
    adapter = _ShortCircuitAdapter()
    model = _mock_model("this must never be requested through the CodeAct path")
    node = create_call_model_node(adapter, model, _mock_settings())

    result = await node(_make_state(), config={"configurable": {"cuga_lite_max_steps": 7}})

    assert isinstance(result, Command) and result.goto == "tool_exec"
    assert model.ainvoke.await_count == 0, "the CodeAct invocation ran alongside the FC turn"

    kw = adapter.seen_kwargs
    assert kw["bound"] is _BOUND, "the hook must get the bind_tools-resolved model"
    assert isinstance(kw["system_content"], str) and "helpful agent" in kw["system_content"]
    assert all(isinstance(m, BaseMessage) for m in kw["modified_messages"]), (
        "history as message objects, not dicts"
    )
    assert kw["configurable"] == {"cuga_lite_max_steps": 7}
    assert kw["budget_exhausted"] is False and kw["playbook_fired"] is False


@pytest.mark.asyncio
@patch(_SUMMARIZE, new_callable=AsyncMock)
async def test_tool_results_left_by_a_function_calling_turn_reach_the_codeact_model(mock_summarize):
    """Switching a thread from function-calling to codeact must not hide what the
    tools returned. Both shapes occur: real ToolMessages inside one run, and bare
    ``type="tool"`` shells after the SDK boundary dropped the subclass."""
    mock_summarize.side_effect = lambda messages, *a, **kw: messages
    model = _mock_model("Plain answer.")
    state = _make_state()
    state.chat_messages = [
        HumanMessage(content="echo 7"),
        AIMessage(
            content="", tool_calls=[{"name": "echo", "args": {"value": 7}, "id": "c1", "type": "tool_call"}]
        ),
        ToolMessage(content="7", tool_call_id="c1", name="echo"),
        BaseMessage(type="tool", content="8", name="echo"),
        HumanMessage(content="what did the tools say?"),
    ]
    node = create_call_model_node(_BaseAdapter(), model, _mock_settings())

    await node(state, config=None)

    sent = model.ainvoke.await_args[0][0]
    assert {"role": "user", "content": "Tool result (echo):\n7"} in sent
    assert {"role": "user", "content": "Tool result (echo):\n8"} in sent
