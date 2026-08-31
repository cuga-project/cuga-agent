import json

import pytest

from cuga.backend.cuga_graph.nodes.entry_router import entry_router as entry_router_module
from cuga.backend.cuga_graph.nodes.entry_router import EntryRouter
from cuga.backend.cuga_graph.state.agent_state import AgentState, default_state
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
async def test_entry_router_routes_to_browser_in_web_mode(monkeypatch):
    monkeypatch.setattr(settings.features, "chat", False)
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "mode", "web")

    state = AgentState(input="open the dashboard")
    command = await EntryRouter.node_handler(state, NodeNames.ENTRY_ROUTER)

    assert command.goto == NodeNames.CUGA_BROWSER


@pytest.mark.unit
@pytest.mark.asyncio
async def test_entry_router_routes_hybrid_api_phase_to_lite(monkeypatch):
    monkeypatch.setattr(settings.features, "chat", True)
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "mode", "hybrid")

    state = AgentState(
        input="get the top account",
        sub_task_type="hybrid",
        hybrid_phase="api",
    )
    command = await EntryRouter.node_handler(state, NodeNames.ENTRY_ROUTER)

    assert command.goto == NodeNames.CUGA_LITE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_entry_router_routes_hybrid_web_phase_to_browser(monkeypatch):
    monkeypatch.setattr(settings.features, "chat", True)
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "mode", "hybrid")

    state = AgentState(
        input="add the account to the current page",
        sub_task_type="hybrid",
        hybrid_phase="web",
    )
    command = await EntryRouter.node_handler(state, NodeNames.ENTRY_ROUTER)

    assert command.goto == NodeNames.CUGA_BROWSER


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_disabled_explicit_hybrid_starts_with_api_phase(monkeypatch):
    monkeypatch.setattr(settings.features, "chat", False)
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "mode", "hybrid")

    state = AgentState(
        input="get the top account and add it to this page",
        sub_task="stale browser request",
        sub_task_app="stale-browser-app",
        sub_task_type="hybrid",
    )
    command = await EntryRouter.node_handler(state, NodeNames.ENTRY_ROUTER)

    assert command.goto == NodeNames.CUGA_LITE
    assert state.hybrid_phase == "api"
    assert state.hybrid_api_task == state.input
    assert state.hybrid_web_task == state.input
    assert state.sub_task is None
    assert state.sub_task_app is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_disabled_hybrid_mode_keeps_web_only_request_in_browser(monkeypatch):
    monkeypatch.setattr(settings.features, "chat", False)
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "mode", "hybrid")

    state = AgentState(input="click submit", sub_task_type="web")
    command = await EntryRouter.node_handler(state, NodeNames.ENTRY_ROUTER)

    assert command.goto == NodeNames.CUGA_BROWSER
    assert state.hybrid_phase is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_disabled_hybrid_mode_keeps_api_only_request_in_lite(monkeypatch):
    monkeypatch.setattr(settings.features, "chat", False)
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "mode", "hybrid")

    state = AgentState(
        input="list accounts",
        sub_task="stale browser request",
        sub_task_app="stale-browser-app",
        sub_task_type="api",
    )
    command = await EntryRouter.node_handler(state, NodeNames.ENTRY_ROUTER)

    assert command.goto == NodeNames.CUGA_LITE
    assert state.hybrid_phase is None
    assert state.sub_task is None
    assert state.sub_task_app is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_entry_router_routes_to_browser_when_url_and_app_are_set(monkeypatch):
    monkeypatch.setattr(settings.features, "chat", False)
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "mode", "api")

    state = AgentState(input="click submit", url="https://example.test/app", current_app="example")
    command = await EntryRouter.node_handler(state, NodeNames.ENTRY_ROUTER)

    assert command.goto == NodeNames.CUGA_BROWSER


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_default_state_without_page_routes_to_lite_in_api_mode(monkeypatch):
    monkeypatch.setattr(settings.features, "chat", False)
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "mode", "api")

    state = default_state(page=None, observation=None, goal="")
    state.input = "list all my accounts, how many are there?"

    command = await EntryRouter.node_handler(state, NodeNames.ENTRY_ROUTER)

    assert state.sub_task_type is None
    assert command.goto == NodeNames.CUGA_LITE


@pytest.mark.unit
def test_default_state_with_page_marks_web_subtask():
    page = type("Page", (), {"url": "https://example.test/app"})()
    state = default_state(page=page, observation=None, goal="open the dashboard")
    assert state.sub_task_type == "web"
    assert state.sub_task == "open the dashboard"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_entry_router_collects_route_step_for_intent_analytics(monkeypatch):
    collected_steps = []
    monkeypatch.setattr(settings.features, "chat", True)
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "mode", "api")
    monkeypatch.setattr(entry_router_module.tracker, "collect_step", collected_steps.append)

    command = await EntryRouter.node_handler(
        AgentState(input="list all accounts"),
        NodeNames.ENTRY_ROUTER,
    )

    assert command.goto == NodeNames.CUGA_LITE
    assert len(collected_steps) == 1
    assert collected_steps[0].name == NodeNames.ENTRY_ROUTER
    assert json.loads(collected_steps[0].data) == {"route": NodeNames.CUGA_LITE}
