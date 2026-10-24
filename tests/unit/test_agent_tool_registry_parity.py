"""Stored app scopes hold even when a YAML registry returns every application."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest

from cuga.backend.server import agent_registry, main
from cuga.backend.cuga_graph.nodes.cuga_lite.providers import combined
from cuga.backend.tools_env.registry.utils.types import AppDefinition

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "config,expected", [({}, None), ({"tools": []}, []), ({"tools": [{"name": "crm"}]}, ["crm"])]
)
def test_explicit_empty_app_scope_differs_from_legacy_registry(config, expected):
    assert agent_registry.configured_tool_apps(config) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [[], ["crm"]])
async def test_provider_filters_global_registry_and_rejects_unassigned_app(monkeypatch, scope):
    apps = [AppDefinition(name=name, url="http://test.invalid", type="api") for name in ["crm", "weather"]]
    monkeypatch.setattr(combined, "get_apps", AsyncMock(return_value=apps))
    monkeypatch.setattr(combined.tracker, "apps", [])
    monkeypatch.setattr(combined.settings.advanced_features, "registry", True)
    provider = combined.CombinedToolProvider(app_names=scope, agent_id="sales-agent")

    assert [app.name for app in await provider.get_apps()] == scope
    assert [app.name for app in await provider.get_apps()] == scope
    assert await provider.get_tools("weather") == []
    monkeypatch.setattr(combined, "get_apps", AsyncMock(return_value=[]))
    assert await provider.get_apps() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tools", [[], [{"name": "weather"}]])
async def test_rebuild_updates_tool_scope_through_runtime_guard(monkeypatch, tools):
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider
    from cuga.backend.server.manage_routes.draft_ops import rebuild_agent_from_config

    provider = combined.CombinedToolProvider(app_names=["crm"], agent_id="scope-agent")
    guarded = ToolGuardingToolProvider(provider, enabled=False)
    agent = SimpleNamespace(tool_provider=guarded, build_graph=AsyncMock())
    monkeypatch.setattr(agent_registry, "supervisor_graph_overrides", AsyncMock(return_value={}))

    await rebuild_agent_from_config(agent, {"tools": tools})

    assert provider.app_names == [tool["name"] for tool in tools]
    assert agent.tool_provider is guarded
    agent.build_graph.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("draft", [False, True])
async def test_tools_panel_matches_selected_config_and_include_filter(monkeypatch, draft):
    config = {"tools": [{"name": "crm", "include": ["get_customers"]}]}
    monkeypatch.setattr("cuga.backend.server.config_store.load_config", AsyncMock(return_value=(config, 1)))
    monkeypatch.setattr("cuga.backend.server.config_store.load_draft", AsyncMock(return_value=config))
    monkeypatch.setattr(
        "cuga.backend.tools_env.registry.utils.api_utils.get_apps",
        AsyncMock(
            return_value=[
                AppDefinition(name=name, url="http://test.invalid", type="api") for name in ["crm", "weather"]
            ]
        ),
    )
    get_apis = AsyncMock(
        return_value={
            "get_customers": {"operation_id": "get_customers"},
            "delete_customers": {"operation_id": "delete_customers"},
        }
    )
    monkeypatch.setattr("cuga.backend.tools_env.registry.utils.api_utils.get_apis", get_apis)
    monkeypatch.setattr(main, "_runtime_tools_flags", AsyncMock(return_value=(False, False)))

    response = await main.get_tools_list(
        SimpleNamespace(headers={}), agent_id="sales-agent", draft="1" if draft else "0"
    )

    data = json.loads(response.body)
    assert [tool["name"] for tool in data["tools"]] == ["get_customers"]
    assert [app["name"] for app in data["apps"]] == ["crm"]
    get_apis.assert_awaited_once_with("crm", agent_id="sales-agent--draft" if draft else "sales-agent")


@pytest.mark.asyncio
async def test_supervisor_tools_endpoint_does_not_advertise_global_apps(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.server.config_store.load_config",
        AsyncMock(return_value=({"agent": {"kind": "supervisor"}}, 1)),
    )
    apps = AsyncMock()
    monkeypatch.setattr("cuga.backend.tools_env.registry.utils.api_utils.get_apps", apps)

    response = await main.get_tools_list(SimpleNamespace(headers={}), agent_id="cuga", draft="0")

    assert json.loads(response.body) == {"tools": [], "apps": []}
    apps.assert_not_awaited()
