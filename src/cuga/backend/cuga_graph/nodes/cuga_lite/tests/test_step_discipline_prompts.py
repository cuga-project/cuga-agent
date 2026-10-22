"""Step discipline reaches both prompts and keeps their examples consistent."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from pydantic import Field

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import create_mcp_prompt
from cuga.backend.llm.utils.helpers import load_one_prompt

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[1]


def _executor_prompt(**kwargs):
    template = load_one_prompt(str(_ROOT / "prompts/mcp_prompt.jinja2"), relative_to_caller=False)
    return create_mcp_prompt([], prompt_template=template, **kwargs)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("autonomous", [False, True])
def test_executor_contract_and_examples_follow_the_flag(enabled, autonomous):
    text = _executor_prompt(
        step_discipline=enabled,
        enable_find_tools=True,
        enable_todos=True,
        is_autonomous_subtask=autonomous,
    )
    assert ("numbered plan of at most 4 short steps" in text) is enabled
    assert ("Do not claim completion" in text) is enabled
    assert ("report the failure or blocker" in text) is enabled
    assert ("Code alone is still fine" in text) is not enabled
    assert ("long plan instead of tight reasoning" in text) is not enabled
    assert ("ALWAYS loop through pages" in text) is not enabled
    if enabled:
        assert "Only return to the user with a final answer when the entire task is complete" not in text
        assert "your response **MUST** be a Python Code Block" not in text
        if autonomous:
            assert "verifying that no available alternative can resolve a failure or blocker" in text
        examples = text.split("## Example: Correct vs. Incorrect Output", 1)[1]
        for example in re.split(r"(?=✅|❌)", examples):
            if not example.startswith("✅") or "```python" not in example:
                continue
            before, code, _ = example.split("```", 2)
            steps = re.findall(r"^\d+\. .+", before, re.MULTILINE)
            assert 1 <= len(steps) <= 4, example
            tree = ast.parse(code.removeprefix("python\n"))
            assert sum(isinstance(node, ast.Await) for node in ast.walk(tree)) <= 1, example
            assert not any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(tree)), example
            assert isinstance(tree.body[-1], ast.Expr)
            assert isinstance(tree.body[-1].value, ast.Call)
            assert tree.body[-1].value.func.id == "print"


def test_direct_executor_renderer_defaults_to_discipline_off():
    assert _executor_prompt() == _executor_prompt(step_discipline=False)


@pytest.mark.parametrize("enabled", [False, True])
def test_reflection_contract_and_next_examples_follow_the_flag(enabled):
    template = load_one_prompt(
        str(_ROOT / "reflection/prompts/reflection_system.jinja2"), relative_to_caller=False
    )
    text = template.format(instructions="", step_discipline=enabled)
    assert ("Exactly ONE sentence" in text) is enabled
    assert ("One to three sentences" in text) is not enabled
    assert "NEXT: task complete." in text
    assert "never re-issue a rejected action" not in text
    examples = text.split("### Examples", 1)[1]
    next_lines = re.findall(r"^NEXT: (.+)$", examples, re.MULTILINE)
    assert len(next_lines) == 3
    if enabled:
        assert all(not re.search(r"\b(then|next|finally)\b|;", line) for line in next_lines)
        assert "first product_id" in next_lines[2]
        assert "7826309761" in next_lines[1], "corrected arguments remain a valid retry"
        assert "unless the relevant state has changed" in text
        assert "at most\none retry" in text
        assert "do not repeat writes\nwhose outcome is unknown" in text
    else:
        assert "then" in next_lines[0] and "; if" in next_lines[1] and "then" in next_lines[2]


class _RecordingModel(FakeListChatModel):
    prompts: list = Field(default_factory=list)

    def _call(self, messages, **kwargs):
        self.prompts.append(list(messages))
        return super()._call(messages, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("discipline", ["off", "one_tool_per_step"])
@pytest.mark.parametrize("custom_demos", [False, True])
async def test_graph_passes_the_setting_to_executor_and_reflection(monkeypatch, discipline, custom_demos):
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
        CugaLiteState,
        create_cuga_lite_graph,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools import (
        _load_default_find_tools_few_shot_examples,
    )
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", False)
    monkeypatch.setattr(settings.advanced_features, "force_autonomous_mode", False)
    budget_tokens = []
    for name in (
        "_tool_call_budget_context",
        "_thread_tool_call_budget_context",
        "_block_tool_call_budget_context",
        "_block_tool_call_cap_override_context",
    ):
        context = getattr(tracker_module, name)
        token = context.set(None)
        budget_tokens.append((context, token))

    calls = []

    async def echo(value: int) -> int:
        """Echo a value."""
        calls.append(value)
        return value

    provider = MagicMock()
    provider.get_all_tools = AsyncMock(
        return_value=[StructuredTool.from_function(coroutine=echo, name="echo", description="Echo.")]
    )
    provider.get_apps = AsyncMock(return_value=[])
    provider.get_tools = AsyncMock(return_value=[])
    model = _RecordingModel(
        responses=[
            "1. Echo the requested value.\n```python\nvalue = await echo(value=1)\nprint(value)\n```",
            "STATUS: Echo returned 1.\nNEXT: task complete.",
            "Echo returned 1.",
        ]
    )
    demo_config = {}
    if custom_demos:
        demo_config["mcp_few_shot_examples"] = [
            {"role": "user", "content": "Echo 2."},
            {
                "role": "assistant",
                "content": "1. Echo 2.\n```python\nvalue = await echo(value=2)\nprint(value)\n```",
            },
        ]
    graph = create_cuga_lite_graph(model=model, tool_provider=provider, apps_list=[], thread_id="t")
    try:
        result = await graph.compile().ainvoke(
            CugaLiteState(chat_messages=[HumanMessage(content="echo 1")]),
            config={
                "configurable": {
                    "thread_id": "prompt-test",
                    "llm": model,
                    "enable_todos": False,
                    "shortlisting_tool_threshold": 0,
                    "cuga_lite_execution_mode": "codeact",
                    "cuga_lite_step_discipline": discipline,
                    "reflection_enabled": True,
                    "pre_execute_verify_enabled": False,
                    "cuga_lite_nl_auto_continue": False,
                    "cuga_lite_enable_few_shots": True,
                    **demo_config,
                }
            },
        )
    finally:
        for context, token in reversed(budget_tokens):
            context.reset(token)

    assert calls == [1]
    assert result["final_answer"] == "Echo returned 1."
    assert len(model.prompts) == 3, "the real reflection chain must run between code and final answer"
    executor = next(m.content for m in model.prompts[0] if isinstance(m, SystemMessage))
    reflection = next(m.content for m in model.prompts[1] if isinstance(m, SystemMessage))
    enabled = discipline == "one_tool_per_step"
    assert ("numbered plan of at most 4 short steps" in executor) is enabled
    assert ("Exactly ONE sentence" in reflection) is enabled
    demos = {turn["content"] for turn in _load_default_find_tools_few_shot_examples()}
    assert demos, "this must exercise the bundled demonstrations"
    assert any(message.content in demos for message in model.prompts[0]) is (not enabled and not custom_demos)
    if custom_demos:
        assert all(
            any(message.content == turn["content"] for message in model.prompts[0])
            for turn in demo_config["mcp_few_shot_examples"]
        )
