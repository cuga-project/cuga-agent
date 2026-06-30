"""Phase 2 — create_call_model_node shared factory tests.

Pins the routing behaviour of the shared call_model node so that:
1. When the model returns a fenced code block → Command(goto=execute_node_name)
2. When the model returns plain text → Command(goto=END) with final_answer
3. When the step limit is exceeded → Command(goto=END) with error message
4. When classify_auto_continue fires → Command(goto="call_model") with "continue"
5. When returning from tool-approval → approval resumption takes priority

All test use a _MinimalTestAdapter with no-op hooks (Supervisor-equivalent
behaviour) so the tests cover only the shared logic, not adapter-specific
behaviours tested in later phases.
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


# ── Shared test adapter ────────────────────────────────────────────────────


class _TestAdapter(CoreGraphAdapter):
    messages_key = "chat_messages"
    execute_node_name = "sandbox"
    metadata_key = "cuga_lite_metadata"

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return list(state.chat_messages or [])

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        return override if override is not None else getattr(state, "_max_steps", 50)


# ── Test state factory ─────────────────────────────────────────────────────


def _make_state(
    messages=None,
    step_count=0,
    max_steps=50,
    prepared_prompt="You are a helpful agent.",
    metadata=None,
):
    vm = MagicMock()
    vm.get_variable_names.return_value = []
    return SimpleNamespace(
        chat_messages=messages or [HumanMessage(content="do task")],
        step_count=step_count,
        _max_steps=max_steps,
        prepared_prompt=prepared_prompt,
        cuga_lite_metadata=metadata or {},
        variables_storage=None,
        variable_counter_state=None,
        variable_creation_order=None,
        variables_manager=vm,
    )


# ── Mock helpers ───────────────────────────────────────────────────────────


def _mock_response(content: str, reasoning: str | None = None):
    additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}
    return SimpleNamespace(content=content, additional_kwargs=additional_kwargs)


def _mock_model(content: str):
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=_mock_response(content))
    return model


def _mock_settings(policy_enabled=False):
    adv = SimpleNamespace(cuga_lite_max_steps=50)
    policy = SimpleNamespace(enabled=policy_enabled)
    return SimpleNamespace(advanced_features=adv, policy=policy)


# The factory under test — imported lazily so we see ImportError (RED) clearly.
def _get_factory():
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import (
        create_call_model_node,
    )

    return create_call_model_node


# ── 1. Code path routes to execute_node_name ──────────────────────────────


@pytest.mark.asyncio
@patch(
    "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
    new_callable=AsyncMock,
)
async def test_code_path_routes_to_execute_node(mock_summarize):
    mock_summarize.side_effect = lambda messages, *args, **kwargs: messages

    adapter = _TestAdapter()
    state = _make_state()
    model = _mock_model("```python\nprint('hi')\n```")
    settings = _mock_settings()

    node = _get_factory()(adapter, model, settings)
    result = await node(state, config=None)

    assert isinstance(result, Command)
    assert result.goto == "sandbox"
    assert result.update["script"] == "print('hi')"
    assert result.update["step_count"] == 1
    assert result.update["chat_messages"][-1].content == "```python\nprint('hi')\n```"


# ── 2. No-code path routes to END ─────────────────────────────────────────


@pytest.mark.asyncio
@patch(
    "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
    new_callable=AsyncMock,
)
async def test_no_code_path_routes_to_end(mock_summarize):
    mock_summarize.side_effect = lambda messages, *args, **kwargs: messages

    adapter = _TestAdapter()
    state = _make_state()
    model = _mock_model("The answer is 42.")
    settings = _mock_settings()

    node = _get_factory()(adapter, model, settings)
    result = await node(state, config=None)

    assert isinstance(result, Command)
    assert result.goto == END
    assert result.update["final_answer"] == "The answer is 42."
    assert result.update["execution_complete"] is True
    assert result.update["step_count"] == 1


# ── 3. Step limit in code path ────────────────────────────────────────────


@pytest.mark.asyncio
@patch(
    "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
    new_callable=AsyncMock,
)
async def test_step_limit_in_code_path_routes_to_end_with_error(mock_summarize):
    mock_summarize.side_effect = lambda messages, *args, **kwargs: messages

    adapter = _TestAdapter()
    # step_count=50 means new_step_count=51 which exceeds max_steps=50
    state = _make_state(step_count=50, max_steps=50)
    model = _mock_model("```python\nprint('hi')\n```")
    settings = _mock_settings()

    node = _get_factory()(adapter, model, settings)
    result = await node(state, config=None)

    assert isinstance(result, Command)
    assert result.goto == END
    assert "Maximum step limit" in result.update.get(
        "error", ""
    ) or "Maximum step limit" in result.update.get("final_answer", "")


# ── 4. Step limit in no-code path ─────────────────────────────────────────


@pytest.mark.asyncio
@patch(
    "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
    new_callable=AsyncMock,
)
async def test_step_limit_in_no_code_path_routes_to_end_with_error(mock_summarize):
    mock_summarize.side_effect = lambda messages, *args, **kwargs: messages

    adapter = _TestAdapter()
    state = _make_state(step_count=50, max_steps=50)
    model = _mock_model("The answer is 42.")
    settings = _mock_settings()

    node = _get_factory()(adapter, model, settings)
    result = await node(state, config=None)

    assert isinstance(result, Command)
    assert result.goto == END
    assert "Maximum step limit" in result.update.get(
        "error", ""
    ) or "Maximum step limit" in result.update.get("final_answer", "")


# ── 5. Auto-continue loops back to call_model ─────────────────────────────


class _AutoContinueAdapter(_TestAdapter):
    async def classify_auto_continue(
        self, state: Any, model: Any, content: str, reasoning: Optional[str]
    ) -> bool:
        return True  # always continue


@pytest.mark.asyncio
@patch(
    "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
    new_callable=AsyncMock,
)
async def test_auto_continue_loops_back_to_call_model(mock_summarize):
    mock_summarize.side_effect = lambda messages, *args, **kwargs: messages

    adapter = _AutoContinueAdapter()
    state = _make_state()
    model = _mock_model("Let me think about this first.")
    settings = _mock_settings()

    node = _get_factory()(adapter, model, settings)
    result = await node(state, config=None)

    assert isinstance(result, Command)
    assert result.goto == "call_model"
    msgs = result.update["chat_messages"]
    assert msgs[-1].content == "continue"
    assert result.update["execution_complete"] is False


# ── 6. messages_key and execute_node_name are respected ───────────────────


class _SupervisorLikeAdapter(_TestAdapter):
    messages_key = "supervisor_chat_messages"
    execute_node_name = "execute_agent_tool"
    metadata_key = "supervisor_metadata"

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return list(getattr(state, "supervisor_chat_messages", None) or [])


@pytest.mark.asyncio
@patch(
    "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
    new_callable=AsyncMock,
)
async def test_adapter_messages_key_and_execute_node_name_respected(mock_summarize):
    mock_summarize.side_effect = lambda messages, *args, **kwargs: messages

    adapter = _SupervisorLikeAdapter()
    state = SimpleNamespace(
        supervisor_chat_messages=[HumanMessage(content="orchestrate")],
        step_count=0,
        _max_steps=50,
        prepared_prompt="Supervisor prompt.",
        supervisor_metadata={},
        variables_storage=None,
        variable_counter_state=None,
        variable_creation_order=None,
        variables_manager=MagicMock(get_variable_names=lambda: []),
    )
    model = _mock_model("```python\nawait delegate_to_agent('do x')\n```")
    settings = _mock_settings()

    node = _get_factory()(adapter, model, settings)
    result = await node(state, config=None)

    assert result.goto == "execute_agent_tool"
    assert "supervisor_chat_messages" in result.update
    assert "execute_agent_tool" == result.goto


# ── 7. Configurable llm overrides base_model ──────────────────────────────


@pytest.mark.asyncio
@patch(
    "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
    new_callable=AsyncMock,
)
async def test_configurable_llm_overrides_base_model(mock_summarize):
    mock_summarize.side_effect = lambda messages, *args, **kwargs: messages

    adapter = _TestAdapter()
    state = _make_state()
    base_model = _mock_model("should not be called")
    override_model = _mock_model("The answer is 7.")
    settings = _mock_settings()

    node = _get_factory()(adapter, base_model, settings)
    config = {"configurable": {"llm": override_model}}
    result = await node(state, config=config)

    # override_model was invoked, base_model was not
    override_model.ainvoke.assert_called_once()
    base_model.ainvoke.assert_not_called()
    assert result.update["final_answer"] == "The answer is 7."


# ── 8. Empty visible content falls back to reasoning / execution output ─────


@pytest.mark.asyncio
@patch(
    "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
    new_callable=AsyncMock,
)
async def test_empty_content_falls_back_to_reasoning_for_final_answer(mock_summarize):
    mock_summarize.side_effect = lambda messages, *args, **kwargs: messages

    adapter = _TestAdapter()
    state = _make_state()
    model = _mock_model("")
    model.ainvoke = AsyncMock(
        return_value=_mock_response("", reasoning="Average spending is 200, total is 600.")
    )
    settings = _mock_settings()

    node = _get_factory()(adapter, model, settings)
    result = await node(state, config=None)

    assert result.goto == END
    assert result.update["final_answer"] == "Average spending is 200, total is 600."
    assert result.update["chat_messages"][-1].content == "Average spending is 200, total is 600."


@pytest.mark.asyncio
@patch(
    "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
    new_callable=AsyncMock,
)
async def test_empty_content_falls_back_to_execution_output(mock_summarize):
    mock_summarize.side_effect = lambda messages, *args, **kwargs: messages

    adapter = _TestAdapter()
    state = _make_state(
        messages=[
            HumanMessage(content="analyze spendings"),
            HumanMessage(content="Execution output:\navg=200\ntotal=600"),
        ]
    )
    model = _mock_model("")
    settings = _mock_settings()

    node = _get_factory()(adapter, model, settings)
    result = await node(state, config=None)

    assert result.goto == END
    assert result.update["final_answer"] == "avg=200\ntotal=600"
