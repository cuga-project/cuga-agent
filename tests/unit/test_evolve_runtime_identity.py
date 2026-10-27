from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from cuga.backend.server import main as m
from cuga.backend.cuga_graph.state.agent_state import AgentState

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_http_identity(monkeypatch):
    seen = []

    def probe(state: AgentState, config: RunnableConfig):
        seen.append(
            {
                'scope': state.service_scope,
                'user_id': state.user_id,
                'config_agent_id': config['configurable'].get('agent_id'),
                'config_keys': [k for k in config['configurable'] if not k.startswith('__')],
            }
        )
        return {'final_answer': 'Identity captured'}

    builder = StateGraph(AgentState)
    builder.add_node('FinalAnswerAgent', probe)
    builder.add_edge(START, 'FinalAnswerAgent')
    builder.add_edge('FinalAnswerAgent', END)
    runtime = SimpleNamespace(
        graph=builder.compile(checkpointer=MemorySaver()), policy_system=None, chat=None
    )
    monkeypatch.setenv('DYNACONF_SERVICE__INSTANCE_ID', 'deployment-sales')
    monkeypatch.setattr(m.agent_registry, 'is_agent_registry_enabled', lambda: True)
    monkeypatch.setattr(m, '_resolve_stream_agent', AsyncMock(return_value=runtime))
    monkeypatch.setattr(m, '_knowledge_enabled_for_app_state', lambda _: False)
    monkeypatch.setattr(m, '_rehydrate_citation_ledger', AsyncMock())
    monkeypatch.setattr(m, '_dispatch_slash_for_stream', AsyncMock(return_value=None))
    monkeypatch.setattr(m, 'get_attachment_snapshot', AsyncMock(return_value=None))
    monkeypatch.setattr(m.app_state, 'agent', runtime)
    monkeypatch.setattr(m.app_state, 'stop_events', {})
    monkeypatch.setattr(m.app_state, 'current_llm', None)
    monkeypatch.setattr(m.app_state, 'output_format', None)
    monkeypatch.setattr(m.settings.evolve, 'enabled', False)
    monkeypatch.setattr(m.settings.advanced_features, 'langfuse_tracing', False)
    app = FastAPI()
    app.add_api_route('/stream', m.stream, methods=['POST'])
    app.dependency_overrides[m.require_chat_access] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://probe') as client:
        for agent in ['sales-east', 'sales-west']:
            response = await client.post(
                '/stream',
                json={'query': 'hello'},
                headers={'X-Agent-ID': agent, 'X-Thread-ID': f'probe-{agent}', 'X-Disable-History': 'true'},
            )
            assert response.status_code == 200, response.text
    assert [s['scope']['agent_id'] for s in seen] == ['sales-east', 'sales-west']
    assert all(s['scope']['instance_id'] == 'deployment-sales' for s in seen)
    assert all(s['config_agent_id'] is None for s in seen)


@pytest.mark.asyncio
async def test_resumed_graph_preserves_service_scope_agent_identity():
    from langgraph.types import interrupt
    from cuga.backend.activity_tracker.tracker import ActivityTracker
    from cuga.backend.cuga_graph.utils.agent_loop import AgentLoop
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse

    seen = []

    def pause(state: AgentState, config: RunnableConfig):
        interrupt("approve")
        seen.append(state.service_scope["agent_id"])
        return {"final_answer": "done"}

    builder = StateGraph(AgentState)
    builder.add_node("FinalAnswerAgent", pause)
    builder.add_edge(START, "FinalAnswerAgent")
    builder.add_edge("FinalAnswerAgent", END)
    graph = builder.compile(checkpointer=MemorySaver())
    loop = AgentLoop(
        thread_id="resume-identity",
        langfuse_handler=None,
        graph=graph,
        tracker=ActivityTracker(),
    )
    state = AgentState(service_scope={"agent_id": "sales-east"})
    async for _ in loop.get_stream(state):
        pass
    async for _ in loop.get_stream(
        None,
        resume=ActionResponse(
            action_id="approve",
            confirmed=True,
            response_type="confirmation",
            timestamp="2026-09-15T00:00:00Z",
        ),
    ):
        pass
    assert seen == ["sales-east"]
