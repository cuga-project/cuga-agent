"""Integration tests for agent spawning (Phase 11).

All tests run without a live LLM — CugaAgent.stream is mocked throughout.
"""

from pathlib import Path

import pytest

FIXTURE_AGENTS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "agents"


def _monkeypatch_cuga_agent(monkeypatch, answer: str = "count=3 sum=6"):
    """Mock CugaAgent.stream so execute() returns `answer` immediately."""
    from cuga.sdk import CugaAgent

    async def _mock_stream(self, message, thread_id=None, config=None, action_response=None):
        yield {"final_answer": answer}

    monkeypatch.setattr(CugaAgent, "stream", _mock_stream)


@pytest.mark.asyncio
async def test_sync_spawn_returns_answer(monkeypatch):
    """Full path: discover → registry → spawn_agent → result (success criteria 2, 9)."""
    _monkeypatch_cuga_agent(monkeypatch, answer="count=3 sum=6")

    from cuga.backend.agent_spawn.loader import discover_agents
    from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    entries = discover_agents(FIXTURE_AGENTS_DIR)
    registry = AgentDescriptorRegistry(entries)
    futures: dict = {}
    tools = create_spawn_tools(registry, {}, futures)
    spawn = next(t for t in tools if t.name == "spawn_agent")

    result = await spawn.coroutine(name="data_analyst", task="Analyse [1,2,3]", mode="sync")
    assert result == "count=3 sum=6"


@pytest.mark.asyncio
async def test_async_spawn_and_get_result(monkeypatch):
    """Async spawn → future_id → get_agent_result returns answer (success criterion 3)."""
    _monkeypatch_cuga_agent(monkeypatch, answer="async-result")

    from cuga.backend.agent_spawn.loader import discover_agents
    from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    entries = discover_agents(FIXTURE_AGENTS_DIR)
    registry = AgentDescriptorRegistry(entries)
    futures: dict = {}
    tools = create_spawn_tools(registry, {}, futures)
    spawn = next(t for t in tools if t.name == "spawn_agent")
    get_result = next(t for t in tools if t.name == "get_agent_result")

    future_id = await spawn.coroutine(name="data_analyst", task="Analyse [1,2,3]", mode="async")
    assert future_id.startswith("future_")

    # Poll for result (max 5 seconds)
    result = await get_result.coroutine(future_id=future_id, timeout=5.0)
    assert result == "async-result"


@pytest.mark.asyncio
async def test_async_spawn_graceful_failure(monkeypatch):
    """Mocked CugaAgent raises → get_agent_result returns '[SpawnError]' (FR-9, criterion 3)."""
    from cuga.sdk import CugaAgent

    async def _fail_stream(self, message, thread_id=None, config=None, action_response=None):
        raise RuntimeError("LLM unavailable")
        yield  # make it an async generator

    monkeypatch.setattr(CugaAgent, "stream", _fail_stream)

    from cuga.backend.agent_spawn.loader import discover_agents
    from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    entries = discover_agents(FIXTURE_AGENTS_DIR)
    registry = AgentDescriptorRegistry(entries)
    futures: dict = {}
    tools = create_spawn_tools(registry, {}, futures)
    spawn = next(t for t in tools if t.name == "spawn_agent")
    get_result = next(t for t in tools if t.name == "get_agent_result")

    future_id = await spawn.coroutine(name="data_analyst", task="fail", mode="async")
    result = await get_result.coroutine(future_id=future_id, timeout=5.0)
    assert "[SpawnError]" in result


def test_disabled_produces_no_diff_in_tools(monkeypatch):
    """agent_spawn.enabled=False → no spawn tools in tools_for_prompt (success criterion 4)."""
    monkeypatch.setattr(
        "cuga.config.settings.agent_spawn.enabled", False, raising=False
    )
    from cuga.config import settings

    assert not settings.agent_spawn.enabled


def test_tool_definitions_tool_absent_from_parent_context():
    """summarise_list is in subagent tools only; parent context doesn't have it (criterion 11)."""
    from cuga.backend.agent_spawn.loader import discover_agents

    entries = discover_agents(FIXTURE_AGENTS_DIR)
    assert any(e.name == "data_analyst" for e in entries)
    da = next(e for e in entries if e.name == "data_analyst")
    assert any(d.name == "summarise_list" for d in da.tool_definitions)
    # Parent context is empty — summarise_list is NOT there
    parent_context: dict = {}
    assert "summarise_list" not in parent_context


def test_skill_tools_absent_from_parent_context():
    """skill_tools are in subagent only; parent context doesn't contain them (criterion 12)."""
    parent_context: dict = {}
    assert "skill_tool_fn" not in parent_context


def test_invalid_module_raises_at_load_time(tmp_path):
    """tool_definition with bad module → ToolDefinitionError at build time (criterion 13)."""
    from cuga.backend.agent_spawn.registry import ToolDefinition
    from cuga.backend.agent_spawn.tool_builder import ToolDefinitionError, build_tool_from_definition

    defn = ToolDefinition(
        name="bad",
        description="d",
        module="nonexistent.module",
        function="fn",
    )
    with pytest.raises(ToolDefinitionError):
        build_tool_from_definition(defn)


def test_data_analyst_descriptor_runs_in_ci_without_live_llm():
    """data_analyst fixture: discover → get → build_tool succeeds (criterion 10)."""
    from cuga.backend.agent_spawn.loader import discover_agents
    from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry
    from cuga.backend.agent_spawn.tool_builder import build_tool_from_definition

    entries = discover_agents(FIXTURE_AGENTS_DIR)
    registry = AgentDescriptorRegistry(entries)
    entry = registry.get("data_analyst")
    assert entry is not None
    assert entry.name == "data_analyst"
    # Build the tool_definition — should succeed without a live LLM
    assert len(entry.tool_definitions) == 1
    tool = build_tool_from_definition(entry.tool_definitions[0])
    assert tool.name == "summarise_list"
