"""Phase 7 — Supervisor tool_provider API + wiring tests.

Pins two behaviors:
1. ``create_cuga_supervisor_graph`` accepts ``tool_provider`` (backward-compat
   default ``None`` + a real provider object).
2. When a tool_provider is supplied, ``get_all_tools()`` results are wired:
   - as async callables in the agent_tools_context (via make_tool_awaitable)
   - as prompt dicts for the Jinja template (via prompt_tool_dicts)

The nested closure inside ``_create_supervisor_conversational_graph`` cannot be
isolated without full graph construction, so we test the *mechanism* in
isolation: the same ``make_tool_awaitable`` + ``prompt_tool_dicts`` path that
the wiring will use.
"""

from __future__ import annotations

import inspect
from typing import List
from unittest.mock import MagicMock

import pytest
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import make_tool_awaitable
from cuga.backend.cuga_graph.nodes.cuga_agent_core.tools.runtime_tools import prompt_tool_dicts
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface


# ── helpers ────────────────────────────────────────────────────────────────


def _make_structured_tool(name: str) -> StructuredTool:
    async def _fn(query: str) -> str:
        return f"{name}: {query}"

    return StructuredTool.from_function(
        coroutine=_fn,
        name=name,
        description=f"Tool {name}",
    )


class FakeToolProvider(ToolProviderInterface):
    """Minimal in-process provider that returns a fixed list of tools."""

    def __init__(self, tools: List[StructuredTool]) -> None:
        self._tools = tools
        self.get_all_tools_call_count = 0

    async def initialize(self) -> None:
        pass

    async def get_apps(self):
        return []

    async def get_tools(self, app_name: str):
        return self._tools

    async def get_all_tools(self) -> List[StructuredTool]:
        self.get_all_tools_call_count += 1
        return list(self._tools)


# ── API surface ────────────────────────────────────────────────────────────


def test_create_cuga_supervisor_graph_accepts_tool_provider_none():
    """tool_provider=None (default) must not raise at graph construction time."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_graph import (
        create_cuga_supervisor_graph,
    )

    model = MagicMock()
    model.bind_tools = MagicMock(return_value=model)
    agents = {}

    # Should not raise — backward-compatible default
    graph = create_cuga_supervisor_graph(model, agents, tool_provider=None)
    assert graph is not None


def test_create_cuga_supervisor_graph_accepts_tool_provider_instance():
    """Passing a FakeToolProvider must not raise at graph construction time."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_graph import (
        create_cuga_supervisor_graph,
    )

    model = MagicMock()
    model.bind_tools = MagicMock(return_value=model)
    agents = {}
    provider = FakeToolProvider([_make_structured_tool("search")])

    graph = create_cuga_supervisor_graph(model, agents, tool_provider=provider)
    assert graph is not None


def test_create_cuga_supervisor_graph_accepts_callbacks():
    """callbacks parameter must not raise at graph construction time."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_graph import (
        create_cuga_supervisor_graph,
    )

    model = MagicMock()
    graph = create_cuga_supervisor_graph(model, {}, callbacks=[MagicMock()])
    assert graph is not None


# ── mechanism: make_tool_awaitable wraps provider tools ───────────────────


@pytest.mark.asyncio
async def test_provider_tool_becomes_awaitable_via_make_tool_awaitable():
    """StructuredTool from provider is callable as an async function after wrapping."""
    tool = _make_structured_tool("lookup")
    tool_func = tool.coroutine or tool.func
    wrapped = make_tool_awaitable(tool_func)
    assert inspect.iscoroutinefunction(wrapped)
    result = await wrapped(query="hello")
    assert result == "lookup: hello"


def test_provider_tools_produce_valid_prompt_dicts():
    """prompt_tool_dicts converts provider StructuredTools to Supervisor template format."""
    tools = [_make_structured_tool("search"), _make_structured_tool("fetch")]
    dicts = prompt_tool_dicts(tools)

    assert len(dicts) == 2
    for d in dicts:
        assert "name" in d
        assert "description" in d
        assert "params_str" in d
        assert "params_doc" in d
        assert "response_doc" in d


def test_provider_tools_are_keyed_by_name_in_context():
    """When tools are loaded from provider, agent_tools_context uses tool.name as key."""
    tools = [_make_structured_tool("alpha"), _make_structured_tool("beta")]
    agent_tools_context = {}

    for t in tools:
        tool_func = t.coroutine if getattr(t, "coroutine", None) else getattr(t, "func", None)
        if tool_func:
            agent_tools_context[t.name] = make_tool_awaitable(tool_func)

    assert "alpha" in agent_tools_context
    assert "beta" in agent_tools_context
    assert inspect.iscoroutinefunction(agent_tools_context["alpha"])
    assert inspect.iscoroutinefunction(agent_tools_context["beta"])


# ── mechanism: get_all_tools is awaited ───────────────────────────────────


@pytest.mark.asyncio
async def test_fake_provider_get_all_tools_returns_tools():
    """FakeToolProvider.get_all_tools() returns the injected list (sanity check)."""
    tools = [_make_structured_tool("ping")]
    provider = FakeToolProvider(tools)
    result = await provider.get_all_tools()
    assert len(result) == 1
    assert result[0].name == "ping"
    assert provider.get_all_tools_call_count == 1
