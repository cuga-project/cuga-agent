from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cuga.backend.cuga_graph.entry_graph import CugaEntryGraph
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
