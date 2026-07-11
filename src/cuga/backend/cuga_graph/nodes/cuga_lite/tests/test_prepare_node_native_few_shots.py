"""Native FC must not inject the code-act bundled few-shots (issue #471 self-review).

The bundled find_tools few-shots are code-act examples (they contain ```python```
blocks). Under native function calling they would contradict the dedicated FC
prompt and push the model back to writing code, so prepare_tools_and_apps must
skip them in native/hybrid mode while still honoring explicit caller few-shots.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

PN = "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node"

# A code-act-style few-shot turn (what the bundled loader returns).
BUNDLED_CODE_FEWSHOTS = [
    {"role": "user", "content": "list my accounts"},
    {"role": "assistant", "content": "```python\naccts = await get_accounts()\nprint(accts)\n```"},
]


def _tool(name):
    return StructuredTool.from_function(func=lambda: "ok", name=name, description=name)


def _adapter():
    adapter = MagicMock()
    adapter._task_todos_ref = []
    adapter._tools_context = {}
    adapter._instructions = ""
    adapter._special_instructions = None
    adapter._static_prompt = None
    adapter._thread_id = "t"
    adapter._model = MagicMock()
    adapter.set_metadata = MagicMock()
    one_tool = [_tool("get_accounts")]
    adapter._base_tool_provider = MagicMock()
    adapter._base_tool_provider.get_all_tools = AsyncMock(return_value=one_tool)
    adapter._base_tool_provider.get_apps = AsyncMock(return_value=[])
    adapter._base_tool_provider.get_tools = AsyncMock(return_value=one_tool)
    rendered = MagicMock()
    rendered.to_string = MagicMock(return_value="")
    adapter._prompt_template = MagicMock()
    adapter._prompt_template.invoke = MagicMock(return_value=rendered)
    return adapter


def _state():
    return SimpleNamespace(
        chat_messages=[HumanMessage(content="do it")],
        task_todos=None,
        sub_task=None,
        sub_task_app=None,
        api_intent_relevant_apps=None,
        cuga_lite_metadata=None,
        thread_id="t",
    )


async def _run(configurable):
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )

    # find_tools on (threshold 0 with 1 tool); patch the heavy find_tools + native
    # prompt loaders so we isolate the few-shot decision.
    with (
        patch(f"{PN}.settings.policy.enabled", new=False),
        patch(f"{PN}._web_search_enabled", return_value=False),
        patch(f"{PN}.create_find_tools_tool", new=AsyncMock(return_value=_tool("find_tools"))),
        patch(f"{PN}._load_default_find_tools_few_shot_examples", return_value=BUNDLED_CODE_FEWSHOTS),
        patch(f"{PN}.get_native_mcp_prompt_template", return_value=_adapter()._prompt_template),
    ):
        node = create_prepare_tools_and_apps_node(_adapter(), lc_bind_tools_meta={})
        return await node(
            _state(), config={"configurable": {"shortlisting_tool_threshold": 0, **configurable}}
        )


@pytest.mark.asyncio
async def test_code_mode_injects_bundled_few_shots():
    result = await _run({"cuga_lite_tool_invocation_mode": "code"})
    assert result.update.get("mcp_few_shot_messages") == BUNDLED_CODE_FEWSHOTS


@pytest.mark.asyncio
async def test_native_mode_skips_code_style_bundled_few_shots():
    result = await _run({"cuga_lite_tool_invocation_mode": "native"})
    assert result.update.get("mcp_few_shot_messages") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expect_native", [("native", True), ("hybrid", True), ("code", False)])
async def test_prompt_template_selection_wiring(mode, expect_native):
    """Native/hybrid selects the dedicated FC template; code uses the code-act one."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )

    native_tmpl = MagicMock()
    native_tmpl.invoke = MagicMock(return_value=MagicMock(to_string=MagicMock(return_value="")))
    with (
        patch(f"{PN}.settings.policy.enabled", new=False),
        patch(f"{PN}._web_search_enabled", return_value=False),
        patch(f"{PN}.create_find_tools_tool", new=AsyncMock(return_value=_tool("find_tools"))),
        patch(f"{PN}._load_default_find_tools_few_shot_examples", return_value=BUNDLED_CODE_FEWSHOTS),
        patch(f"{PN}.get_native_mcp_prompt_template", return_value=native_tmpl) as get_native,
    ):
        node = create_prepare_tools_and_apps_node(_adapter(), lc_bind_tools_meta={})
        await node(_state(), config={"configurable": {"cuga_lite_tool_invocation_mode": mode}})
    assert get_native.called is expect_native


@pytest.mark.asyncio
async def test_native_mode_still_honors_explicit_few_shots():
    explicit = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    result = await _run({"cuga_lite_tool_invocation_mode": "native", "mcp_few_shot_examples": explicit})
    assert result.update.get("mcp_few_shot_messages") == explicit
