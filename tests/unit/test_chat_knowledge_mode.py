import json

import pytest
from langchain_core.messages import AIMessage

from cuga.backend.cuga_graph.nodes.chat.chat import ChatNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames
from cuga.config import settings

pytestmark = pytest.mark.unit


class FakeChatAgent:
    def __init__(self):
        self.invoke_count = 0
        self.executed_tools = []

    @staticmethod
    def should_auto_execute_tool(tool_name):
        return bool(tool_name and tool_name.startswith("knowledge_"))

    @staticmethod
    def requires_human_approval(tool_name):
        return not FakeChatAgent.should_auto_execute_tool(tool_name)

    @staticmethod
    def _serialize_tool_result(result):
        return json.dumps(result)

    async def invoke(self, chat_messages, state):
        self.invoke_count += 1
        if self.invoke_count == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_knowledge_1",
                        "name": "knowledge_search_knowledge",
                        "args": {"query": "Where is the SLA?", "scope": "session"},
                    }
                ],
            )
        return AIMessage(content="The SLA is in the session knowledge base.", tool_calls=[])

    async def execute_tool(self, tool_call):
        self.executed_tools.append(tool_call["name"])
        return {"results": [{"text": "The SLA is in the session knowledge base."}]}


class DummyHitlHandler:
    pass


class FakeHybridChatAgent:
    @staticmethod
    def should_auto_execute_tool(tool_name):
        return False

    async def invoke(self, chat_messages, state):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_hybrid_1",
                    "name": "execute_task",
                    "args": {
                        "task": "get the top account and add it to this page",
                        "relevant_variables": [],
                        "task_type": "hybrid",
                        "api_task": "get the top account",
                        "web_task": "add the top account to this page",
                    },
                }
            ],
        )


@pytest.mark.asyncio
async def test_chat_node_auto_executes_knowledge_tools_without_hitl(monkeypatch):
    monkeypatch.setattr(settings.features, "chat", True)

    agent = FakeChatAgent()
    state = AgentState(input="Where is the SLA?", url="", thread_id="thread-123")

    command = await ChatNode.node_handler(
        state=state,
        agent=agent,
        hitl_handler=DummyHitlHandler(),
        name=NodeNames.CHAT_AGENT,
    )

    assert command.goto == NodeNames.FINAL_ANSWER_AGENT
    assert state.final_answer == "The SLA is in the session knowledge base."
    assert agent.executed_tools == ["knowledge_search_knowledge"]
    assert len(state.chat_agent_messages) == 4
    assert state.chat_agent_messages[-1].content == "The SLA is in the session knowledge base."


@pytest.mark.asyncio
async def test_chat_node_prepares_hybrid_api_and_web_phases(monkeypatch):
    monkeypatch.setattr(settings.features, "chat", True)
    monkeypatch.setattr(settings.advanced_features, "mode", "hybrid")
    state = AgentState(input="get the top account and add it to this page")

    command = await ChatNode.node_handler(
        state=state,
        agent=FakeHybridChatAgent(),
        hitl_handler=DummyHitlHandler(),
        name=NodeNames.CHAT_AGENT,
    )

    assert command.goto == NodeNames.ENTRY_ROUTER
    assert state.sub_task_type == "hybrid"
    assert state.hybrid_phase == "api"
    assert state.hybrid_api_task == "get the top account"
    assert state.hybrid_web_task == "add the top account to this page"
    assert state.input == "get the top account"


def test_prepare_execution_task_defaults_missing_type_to_api_and_clears_stale_browser_task(
    monkeypatch,
):
    monkeypatch.setattr(settings.advanced_features, "mode", "hybrid")
    state = AgentState(
        input="old browser request",
        sub_task="old browser request",
        sub_task_app="old-browser-app",
        sub_task_type="web",
    )

    ChatNode._prepare_execution_task(
        state,
        {
            "task": "list accounts",
            "relevant_variables": [],
        },
    )

    assert state.input == "list accounts"
    assert state.sub_task_type == "api"
    assert state.sub_task is None
    assert state.sub_task_app is None
    assert state.hybrid_phase is None
