import pytest

from cuga.backend.cuga_graph.entry_router import EntryRouter
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
