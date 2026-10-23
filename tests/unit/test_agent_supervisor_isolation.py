"""Stored roles must agree with execution even when global supervision is enabled."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cuga.backend.cuga_graph.entry_graph import CugaEntryGraph
from cuga.backend.server import agent_registry
from cuga.backend.server.manage_routes.draft_ops import rebuild_agent_from_config
from cuga.config import settings

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [None, "single"])
async def test_default_agent_role_does_not_inherit_global_roster(monkeypatch, kind):
    monkeypatch.setattr(agent_registry, "is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(settings.supervisor, "enabled", True)
    config = {"agent": {"name": "Digital Sales Assistant"}}
    if kind is not None:
        config["agent"]["kind"] = kind

    overrides = await agent_registry.supervisor_graph_overrides(config)
    graph = SimpleNamespace(**overrides)

    assert CugaEntryGraph._supervisor_is_enabled(graph) is False
    assert graph.supervisor_agents is None


@pytest.mark.asyncio
@pytest.mark.parametrize("use_draft", [False, True])
async def test_stored_supervisor_uses_only_its_own_members(monkeypatch, use_draft):
    monkeypatch.setattr(agent_registry, "is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(settings.supervisor, "enabled", True)
    members = [{"kind": "internal", "ref": "weatherbot"}]
    built = {"weatherbot": object()}
    builder = AsyncMock(return_value=built)
    monkeypatch.setattr("cuga.supervisor_utils.supervisor_config.build_agents_from_stored_subagents", builder)
    config = {
        "agent": {"kind": "supervisor"},
        "supervisor": {"subAgents": members, "planApproval": True},
    }

    overrides = await agent_registry.supervisor_graph_overrides(config, use_draft=use_draft)

    assert overrides["supervisor_enabled"] is True
    assert overrides["supervisor_agents"] is built
    assert overrides["supervisor_plan_approval"] is True
    builder.assert_awaited_once_with(members, use_draft=use_draft)


@pytest.mark.asyncio
async def test_registry_disabled_retains_legacy_yaml_supervisor(monkeypatch):
    monkeypatch.setattr(agent_registry, "is_agent_registry_enabled", lambda: False)
    monkeypatch.setattr(settings.supervisor, "enabled", True)

    overrides = await agent_registry.supervisor_graph_overrides({"agent": {"name": "Legacy"}})

    assert overrides == {}
    assert CugaEntryGraph._supervisor_is_enabled(SimpleNamespace(supervisor_enabled=None)) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("use_draft", [False, True])
async def test_rebuild_removes_previous_supervisor_access(monkeypatch, use_draft):
    monkeypatch.setattr(agent_registry, "is_agent_registry_enabled", lambda: True)
    graph = SimpleNamespace(
        supervisor_enabled=True,
        supervisor_agents={"weatherbot": object()},
        supervisor_plan_approval=True,
        build_graph=AsyncMock(),
    )

    await rebuild_agent_from_config(graph, {"agent": {"kind": "single"}}, use_draft=use_draft)

    assert graph.supervisor_enabled is False
    assert graph.supervisor_agents is None
    assert graph.supervisor_plan_approval is False
    graph.build_graph.assert_awaited_once()
