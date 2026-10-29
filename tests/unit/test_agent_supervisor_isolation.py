"""Stored roles must agree with execution even when global supervision is enabled."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio

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


@pytest.mark.parametrize(
    "config,expected",
    [
        ({"agent": {"kind": "supervisor"}, "supervisor": {"special_instructions": "nested"}}, "nested"),
        (
            {
                "agent": {"kind": "supervisor"},
                "special_instructions": "edited",
                "supervisor": {"special_instructions": "old"},
            },
            "edited",
        ),
        (
            {
                "agent": {"kind": "supervisor"},
                "special_instructions": "",
                "supervisor": {"special_instructions": "old"},
            },
            None,
        ),
        (
            {
                "agent": {"kind": "single", "description": "sales"},
                "supervisor": {"special_instructions": "wrong role"},
            },
            "sales",
        ),
    ],
)
def test_instruction_precedence_preserves_edits_and_explicit_clear(config, expected):
    assert agent_registry.agent_special_instructions(config) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("use_draft", [False, True])
async def test_rebuild_applies_new_instructions_before_build(monkeypatch, use_draft):
    monkeypatch.setattr(agent_registry, "is_agent_registry_enabled", lambda: True)
    seen = []
    graph = SimpleNamespace(special_instructions="old")

    async def build():
        seen.append(graph.special_instructions)

    graph.build_graph = build
    await rebuild_agent_from_config(graph, {"special_instructions": "new"}, use_draft=use_draft)
    await rebuild_agent_from_config(graph, {"special_instructions": ""}, use_draft=use_draft)

    assert seen == ["new", None]


@pytest.mark.asyncio
async def test_direct_edit_waits_for_member_triggered_rebuild(monkeypatch):
    from cuga.backend.server.manage_routes import helpers

    monkeypatch.setattr(agent_registry, "is_agent_registry_enabled", lambda: True)
    started, release = asyncio.Event(), asyncio.Event()
    seen = []
    graph = SimpleNamespace()

    async def build():
        seen.append(graph.special_instructions)
        if len(seen) == 1:
            started.set()
            await release.wait()

    graph.build_graph = build
    state = SimpleNamespace(agent=graph, agent_graphs_cache={}, agent_graph_generations={})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(app_state=state)))
    monkeypatch.setattr(helpers, "_referrer_ids_for_agent", AsyncMock(return_value=["cuga-default"]))
    monkeypatch.setattr(
        "cuga.backend.server.config_store.load_config",
        AsyncMock(return_value=({"special_instructions": "old"}, 1)),
    )
    refresh = asyncio.create_task(
        helpers.invalidate_agent_graph_cache(request, "weatherbot", draft=False, published=True)
    )
    await started.wait()
    publish = asyncio.create_task(rebuild_agent_from_config(graph, {"special_instructions": "published"}))
    await asyncio.sleep(0)
    assert seen == ["old"]
    release.set()
    await asyncio.gather(refresh, publish)

    assert seen == ["old", "published"]
    assert graph.special_instructions == "published"
