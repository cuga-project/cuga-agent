import json

import pytest
from langchain_core.messages import AIMessage

from cuga.backend.cuga_graph.nodes.chat import chat as chat_module
from cuga.backend.cuga_graph.nodes.chat.chat import ChatNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames
from cuga.config import settings


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


@pytest.mark.asyncio
async def test_chat_node_auto_executes_knowledge_tools_without_hitl(monkeypatch):
    monkeypatch.setattr(chat_module, "ENABLE_SAVE_REUSE", True)
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
