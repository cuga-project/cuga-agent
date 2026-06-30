"""prepare_tools_and_apps computes adapter._weak_schema_tool_names (issue #272)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage


def _make_fake_tool(name: str, response_schemas: dict):
    func = SimpleNamespace(_response_schemas=response_schemas)
    return SimpleNamespace(name=name, func=func, description="d", args_schema=None)


def _build_mock_adapter(tools: list) -> MagicMock:
    adapter = MagicMock()
    adapter._task_todos_ref = []
    adapter._tools_context = {}
    adapter._instructions = ""
    adapter._special_instructions = None
    adapter._static_prompt = None
    adapter._thread_id = "test-thread"
    adapter._model = MagicMock()
    adapter._weak_schema_tool_names = frozenset()
    adapter.set_metadata = MagicMock()

    adapter._base_tool_provider = MagicMock()
    adapter._base_tool_provider.get_all_tools = AsyncMock(return_value=tools)
    adapter._base_tool_provider.get_apps = AsyncMock(return_value=[])
    adapter._base_tool_provider.get_tools = AsyncMock(return_value=[])

    rendered = MagicMock()
    rendered.to_string = MagicMock(return_value="")
    adapter._prompt_template = MagicMock()
    adapter._prompt_template.invoke = MagicMock(return_value=rendered)
    return adapter


def _make_state():
    return SimpleNamespace(
        chat_messages=[HumanMessage(content="task")],
        task_todos=None,
        sub_task=None,
        sub_task_app=None,
        api_intent_relevant_apps=None,
        cuga_lite_metadata=None,
        thread_id="test-thread",
    )


@pytest.mark.asyncio
async def test_weak_schema_tool_names_populated_when_shortlisting_inactive():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )

    weak_tool = _make_fake_tool("file_readfile", {})
    placeholder_tool = _make_fake_tool(
        "get_browser_state", {"success": {"type": "string"}, "_synthetic_placeholder": True}
    )
    known_tool = _make_fake_tool("get_weather", {"success": {"type": "object"}})
    adapter = _build_mock_adapter([weak_tool, placeholder_tool, known_tool])
    state = _make_state()

    configurable = {"enable_todos": False, "shortlisting_tool_threshold": 35}
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled",
        new=False,
    ):
        node = create_prepare_tools_and_apps_node(adapter, lc_bind_tools_meta={})
        await node(state, config={"configurable": configurable})

    assert adapter._weak_schema_tool_names == frozenset({"file_readfile", "get_browser_state"})


@pytest.mark.asyncio
async def test_weak_schema_tool_names_populated_when_find_tools_shortlisting_active():
    """Regression (issue #272): when the tool catalog exceeds shortlisting_tool_threshold,
    tools_for_prompt collapses to just the find_tools meta-tool (~prepare_node.py:230), but
    _weak_schema_tool_names must still reflect every weak-schema tool reachable through
    find_tools — not just what's literally listed in the prompt — or the downstream
    block-isolation enforcement and session shape-memory silently never engage.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )

    weak_tool = _make_fake_tool("file_readfile", {})
    placeholder_tool = _make_fake_tool(
        "get_browser_state", {"success": {"type": "string"}, "_synthetic_placeholder": True}
    )
    known_tool = _make_fake_tool("get_weather", {"success": {"type": "object"}})
    adapter = _build_mock_adapter([weak_tool, placeholder_tool, known_tool])
    state = _make_state()

    # threshold=1 with 3 tools forces enable_find_tools=True (tools_for_prompt -> [find_tool]).
    configurable = {"enable_todos": False, "shortlisting_tool_threshold": 1}
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled",
        new=False,
    ):
        node = create_prepare_tools_and_apps_node(adapter, lc_bind_tools_meta={})
        await node(state, config={"configurable": configurable})

    assert adapter._weak_schema_tool_names == frozenset({"file_readfile", "get_browser_state"})


@pytest.mark.asyncio
async def test_weak_schema_tool_names_empty_when_all_tools_have_real_schemas():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )

    known_tool = _make_fake_tool("get_weather", {"success": {"type": "object"}})
    adapter = _build_mock_adapter([known_tool])
    state = _make_state()

    configurable = {"enable_todos": False, "shortlisting_tool_threshold": 35}
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled",
        new=False,
    ):
        node = create_prepare_tools_and_apps_node(adapter, lc_bind_tools_meta={})
        await node(state, config={"configurable": configurable})

    assert adapter._weak_schema_tool_names == frozenset()
