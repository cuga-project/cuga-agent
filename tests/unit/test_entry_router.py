import pytest

from cuga.backend.cuga_graph.nodes.entry_router import EntryRouter
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames
from cuga.config import settings


@pytest.mark.unit
@pytest.mark.asyncio
async def test_entry_router_preserves_variables_on_followup_when_chat_disabled(monkeypatch):
    monkeypatch.setattr(settings.features, "chat", False)
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "mode", "api")

    state = AgentState(input="how many accounts did we retrieve?")
    state.variables_manager.add_variable(50, "account_count", "Number of accounts")

    command = await EntryRouter.node_handler(state, NodeNames.ENTRY_ROUTER)

    assert command.goto == NodeNames.CUGA_LITE
    assert state.variables_manager.get_variable("account_count") == 50


@pytest.mark.unit
@pytest.mark.asyncio
async def test_entry_router_resets_empty_variables_when_chat_disabled(monkeypatch):
    monkeypatch.setattr(settings.features, "chat", False)
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "mode", "api")

    state = AgentState(input="list all my accounts")

    command = await EntryRouter.node_handler(state, NodeNames.ENTRY_ROUTER)

    assert command.goto == NodeNames.CUGA_LITE
    assert state.variables_storage == {}


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["web", "hybrid"])
async def test_entry_router_routes_to_browser_when_mode_is_web_or_hybrid(monkeypatch, mode):
    monkeypatch.setattr(settings.features, "chat", False)
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "mode", mode)

    state = AgentState(input="open the dashboard")
    command = await EntryRouter.node_handler(state, NodeNames.ENTRY_ROUTER)

    assert command.goto == NodeNames.CUGA_BROWSER


@pytest.mark.unit
@pytest.mark.asyncio
async def test_entry_router_routes_to_browser_when_url_and_app_are_set(monkeypatch):
    monkeypatch.setattr(settings.features, "chat", False)
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "mode", "api")

    state = AgentState(input="click submit", url="https://example.test/app", current_app="example")
    command = await EntryRouter.node_handler(state, NodeNames.ENTRY_ROUTER)

    assert command.goto == NodeNames.CUGA_BROWSER
