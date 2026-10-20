from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver

from cuga.backend.cuga_graph.entry_graph import CugaEntryGraph
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface
from cuga.backend.llm.models import LLMManager
from cuga.config import settings


@pytest.mark.unit
@pytest.mark.asyncio
async def test_load_supervisor_agents_preserves_yaml_special_instructions(monkeypatch):
    configured_agent = object()
    load_supervisor_config = AsyncMock(
        return_value=SimpleNamespace(
            agents={"configured_agent": configured_agent},
            supervisor={"special_instructions": "Coordinate the configured specialists."},
        )
    )
    monkeypatch.setattr(settings.supervisor, "config_path", "supervisor.yaml")
    monkeypatch.setattr("os.path.exists", lambda path: True)
    monkeypatch.setattr(
        "cuga.supervisor_utils.supervisor_config.load_supervisor_config",
        load_supervisor_config,
    )
    graph = object.__new__(CugaEntryGraph)

    agents, special_instructions = await graph._load_supervisor_agents(None, {})

    assert agents == {"configured_agent": configured_agent}
    assert special_instructions == "Coordinate the configured specialists."
    load_supervisor_config.assert_awaited_once()


async def _build_entry_graph(**build_kwargs) -> CugaEntryGraph:
    tool_provider = MagicMock(spec=ToolProviderInterface)
    tool_provider.initialize = AsyncMock()
    tool_provider.get_apps = AsyncMock(return_value=[])
    with patch.object(LLMManager, "get_model", return_value=MagicMock()):
        agent = CugaEntryGraph(None, tool_provider=tool_provider, supervisor_enabled=False)
        await agent.build_graph(**build_kwargs)
    return agent


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_graph_uses_passed_checkpointer():
    saver = MemorySaver()

    agent = await _build_entry_graph(checkpointer=saver)

    assert agent.graph.checkpointer is saver


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_graph_defaults_to_fresh_memory_saver():
    first = await _build_entry_graph()
    second = await _build_entry_graph()

    assert isinstance(first.graph.checkpointer, MemorySaver)
    assert first.graph.checkpointer is not second.graph.checkpointer
