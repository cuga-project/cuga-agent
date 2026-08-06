"""prepare_tools_and_apps installs the direct-tool recorder correctly.

Covers the production wiring that a wrapper-level test cannot: provider
detection through the ToolGuard decorator, the direct-tools-only filter, and
not double-recording tools that already carry @tracked_tool.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import DirectLangChainToolsProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker, tracked_tool

pytestmark = pytest.mark.unit


class _EchoArgs(BaseModel):
    x: int


async def _echo(x: int) -> dict:
    return {"v": x}


def _echo_sync(x: int) -> dict:
    return {"v": x}


def _structured(func, name: str) -> StructuredTool:
    if getattr(func, "__code__", None) and func.__code__.co_flags & 0x80:  # coroutine
        return StructuredTool(name=name, description="d", args_schema=_EchoArgs, coroutine=func)
    return StructuredTool(name=name, description="d", args_schema=_EchoArgs, func=func)


def _build_adapter(provider) -> MagicMock:
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
    adapter._base_tool_provider = provider
    rendered = MagicMock()
    rendered.to_string = MagicMock(return_value="")
    adapter._prompt_template = MagicMock()
    adapter._prompt_template.invoke = MagicMock(return_value=rendered)
    return adapter


def _state():
    return SimpleNamespace(
        chat_messages=[HumanMessage(content="task")],
        task_todos=None,
        sub_task=None,
        sub_task_app=None,
        api_intent_relevant_apps=None,
        cuga_lite_metadata=None,
        thread_id="test-thread",
    )


async def _prepare(provider):
    """Run prepare_tools_and_apps and return the adapter's tool context."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )

    adapter = _build_adapter(provider)
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled",
        new=False,
    ):
        node = create_prepare_tools_and_apps_node(adapter, lc_bind_tools_meta={})
        await node(_state(), config={"configurable": {"enable_todos": False}})
    return adapter._tools_context


async def _record_one_call(tool_callable, **kwargs):
    ToolCallTracker.start_tracking(enabled=True)
    await tool_callable(**kwargs)
    return ToolCallTracker.stop_tracking()


@pytest.mark.asyncio
async def test_direct_provider_detected_through_toolguard_decorator():
    """CugaAgent always wraps the provider in ToolGuard, so detection must unwrap."""
    provider = DirectLangChainToolsProvider(tools=[_structured(_echo, "echo")])
    guarded = ToolGuardingToolProvider(provider, enabled=False)

    context = await _prepare(guarded)
    calls = await _record_one_call(context["echo"], x=3)

    assert [c["name"] for c in calls] == ["echo"]
    assert calls[0]["app_name"] == "runtime_tools"


@pytest.mark.asyncio
async def test_positional_args_recorded_under_real_parameter_names():
    provider = DirectLangChainToolsProvider(tools=[_structured(_echo, "echo")])

    context = await _prepare(provider)
    ToolCallTracker.start_tracking(enabled=True)
    await context["echo"](7)  # positional
    calls = ToolCallTracker.stop_tracking()

    assert calls[0]["arguments"] == {"x": 7}


@pytest.mark.asyncio
async def test_decorated_async_tool_recorded_once_not_twice():
    """@tracked_tool already records async tools — the wrapper must not double up."""
    decorated = tracked_tool(app_name="demo")(_echo)
    provider = DirectLangChainToolsProvider(tools=[_structured(decorated, "echo")])

    context = await _prepare(provider)
    calls = await _record_one_call(context["echo"], x=3)

    assert len(calls) == 1, f"expected a single record, got {calls}"
    assert calls[0]["app_name"] == "demo"  # the decorator's record survives


@pytest.mark.asyncio
async def test_decorated_sync_tool_still_recorded_once():
    """@tracked_tool's sync path runs in an executor thread where the tracker's
    contextvars are invisible, so the wrapper is what makes sync tools appear."""
    decorated = tracked_tool(app_name="demo")(_echo_sync)
    provider = DirectLangChainToolsProvider(tools=[_structured(decorated, "echo_sync")])

    context = await _prepare(provider)
    calls = await _record_one_call(context["echo_sync"], x=3)

    assert len(calls) == 1, f"expected a single record, got {calls}"
    assert calls[0]["app_name"] == "runtime_tools"  # from the wrapper


@pytest.mark.asyncio
async def test_non_direct_provider_tools_are_not_wrapped():
    """Registry/combined tools record inside their own wrappers — no double-record."""
    provider = MagicMock()
    provider.get_all_tools = AsyncMock(return_value=[_structured(_echo, "echo")])
    provider.get_apps = AsyncMock(return_value=[])
    provider.get_tools = AsyncMock(return_value=[])

    context = await _prepare(provider)
    calls = await _record_one_call(context["echo"], x=3)

    assert calls == []
