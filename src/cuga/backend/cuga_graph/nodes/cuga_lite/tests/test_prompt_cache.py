"""Prompt-cache friendliness: a fixed system prompt, run state at the tail, optional markers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import create_call_model_node
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.prompt_cache import (
    add_cache_markers,
    cache_markers_enabled,
    stable_system_prompt,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import create_mcp_prompt
from cuga.backend.llm.utils.helpers import load_one_prompt

pytestmark = pytest.mark.unit

_SUMMARIZE = "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization"
_MARK = {"type": "ephemeral"}


# ── switches ────────────────────────────────────────────────────────────────


def test_stable_system_prompt_defaults_on_and_reads_configurable():
    assert stable_system_prompt({}) is True
    assert stable_system_prompt({"cuga_lite_stable_system_prompt": False}) is False
    assert stable_system_prompt({"cuga_lite_stable_system_prompt": "false"}) is False


@pytest.mark.parametrize(
    "mode, model, expected",
    [
        ("off", "claude-sonnet-4", False),
        ("auto", "anthropic/claude-sonnet-4", True),
        ("auto", "gpt-4o", False),
        ("on", "gpt-4o", True),
    ],
)
def test_cache_markers_modes(mode, model, expected):
    assert cache_markers_enabled({"cuga_lite_prompt_cache_markers": mode}, model) is expected


def test_cache_markers_default_off():
    assert cache_markers_enabled({}, "claude-sonnet-4") is False


# ── markers ─────────────────────────────────────────────────────────────────


def test_markers_on_system_and_last_two_text_turns_of_role_dicts():
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    out = add_cache_markers(msgs)

    assert out[0]["content"] == [{"type": "text", "text": "SYS", "cache_control": _MARK}]
    assert out[1]["content"] == "u1", "only the last two turns are marked"
    assert out[2]["content"][0]["cache_control"] == _MARK and out[3]["content"][0]["text"] == "u2"
    assert msgs[0]["content"] == "SYS", "the input is never mutated"


def test_markers_skip_tool_replies_empty_turns_and_the_tail():
    call = AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1", "type": "tool_call"}])
    msgs: List[BaseMessage] = [
        SystemMessage(content="SYS"),
        HumanMessage(content="task"),
        call,
        ToolMessage(content="result", tool_call_id="c1"),
        HumanMessage(content="<system-reminder>run state</system-reminder>"),
    ]
    out = add_cache_markers(msgs, history_end=4)

    assert isinstance(out[0].content, list)
    assert isinstance(out[1].content, list), "the last markable turn inside the history is the request"
    assert out[2] is call and out[3] is msgs[3], "empty assistant turns and tool replies are left alone"
    assert out[4] is msgs[4], "the outbound tail changes every call and is never marked"


# ── shared node: run state at the tail ──────────────────────────────────────


class _Adapter(CoreGraphAdapter):
    messages_key = "chat_messages"
    execute_node_name = "sandbox"
    metadata_key = "cuga_lite_metadata"

    def __init__(self, context: str = ""):
        self.context = context

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return list(state.chat_messages or [])

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        return 50

    def prepare_turn_context(self, state: Any, configurable: dict) -> str:
        return self.context


def _state(messages):
    vm = MagicMock()
    vm.get_variable_names.return_value = []
    return SimpleNamespace(
        chat_messages=messages,
        step_count=0,
        prepared_prompt="SYS",
        cuga_lite_metadata={},
        variables_storage=None,
        variable_counter_state=None,
        variable_creation_order=None,
        variables_manager=vm,
        tool_budget_exhausted=False,
    )


def _settings():
    return SimpleNamespace(
        advanced_features=SimpleNamespace(cuga_lite_max_steps=50), policy=SimpleNamespace(enabled=False)
    )


async def _outbound(adapter, messages):
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=SimpleNamespace(content="done", additional_kwargs={}))
    with patch(_SUMMARIZE, new_callable=AsyncMock) as summarize:
        summarize.side_effect = lambda m, *a, **kw: m
        cmd = await create_call_model_node(adapter, model, _settings())(_state(messages), {})
    return model.ainvoke.await_args.args[0], cmd


@pytest.mark.asyncio
async def test_turn_context_rides_the_last_user_message_outbound_only():
    outbound, cmd = await _outbound(
        _Adapter("\n## Current Plan\n- [pending] x\n"), [HumanMessage(content="task")]
    )

    assert outbound[0] == {"role": "system", "content": "SYS"}, "the system prompt never carries run state"
    assert outbound[-1]["content"].startswith("task") and "Current Plan" in outbound[-1]["content"]
    assert all("Current Plan" not in m.content for m in cmd.update["chat_messages"])


@pytest.mark.asyncio
async def test_turn_context_gets_its_own_message_when_the_tail_is_not_a_user_turn():
    outbound, _ = await _outbound(
        _Adapter("\n## Current Plan\n- [pending] x\n"),
        [HumanMessage(content="task"), AIMessage(content="hm")],
    )
    assert outbound[-1] == {"role": "user", "content": "## Current Plan\n- [pending] x"}


@pytest.mark.asyncio
async def test_no_turn_context_changes_nothing():
    outbound, _ = await _outbound(_Adapter(""), [HumanMessage(content="task"), AIMessage(content="hm")])
    assert outbound[-1] == {"role": "assistant", "content": "hm"}


# ── CodeAct prompt wording ──────────────────────────────────────────────────


def test_codeact_prompt_points_to_where_the_plan_is():
    from pathlib import Path

    import cuga.backend.cuga_graph.nodes.cuga_lite as lite

    template = load_one_prompt(str(Path(lite.__file__).parent / "prompts" / "mcp_prompt.jinja2"), False)
    legacy = create_mcp_prompt([], prompt_template=template, enable_todos=True)
    stable = create_mcp_prompt([], prompt_template=template, enable_todos=True, plan_in_reminder=True)

    assert "bottom of the system prompt" in legacy and "latest message" not in legacy
    assert "system prompt" not in stable and "end of the latest message" in stable
