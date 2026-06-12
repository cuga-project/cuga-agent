import pytest
from langchain_core.messages import AIMessage

from cuga.backend.cuga_graph.nodes.browser.action import ActionNode
from cuga.backend.cuga_graph.nodes.browser.action_agent.tools.tools import setup_tools
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils import controller as controller_mod
from cuga.backend.cuga_graph.utils.controller import AgentRunner


class FakePage:
    url = "http://example.test/page"

    async def title(self):
        return "Example Page"


class FakePuAnswer:
    string_representation = "[1] button Example"
    focused_element_bid = None
    img = None
    page_content = "Example page text"


class FakePuProcessor:
    async def transform(self, transformer_params=None):
        return FakePuAnswer()


class FakeEnv:
    page = FakePage()
    pu_processor = FakePuProcessor()

    def get_url(self):
        return self.page.url


class FakeAnswerAgent:
    def run(self, _state):
        return AIMessage(
            content="",
            tool_calls=[{"name": "answer", "args": {"text": "15213"}, "id": "answer-1"}],
        )


async def fake_discover_tools(page):
    return [
        {
            "name": "lookup",
            "description": "Lookup a value",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }
    ]


def tool_names(mode: str, stage: str = "standard", monkeypatch=None) -> set[str]:
    if monkeypatch is not None:
        monkeypatch.setenv("CUGA_WEBMCP_MODE", mode)
    return set(setup_tools(stage).keys())


def test_tool_surface_by_webmcp_mode(monkeypatch):
    assert "webmcp_call" not in tool_names("none", monkeypatch=monkeypatch)

    naive_tools = tool_names("naive", monkeypatch=monkeypatch)
    assert "webmcp_call" in naive_tools
    assert {"click", "type", "answer"}.issubset(naive_tools)

    monkeypatch.setenv("CUGA_WEBMCP_MODE", "advanced")
    assert tool_names("advanced", "tool_stage") == {"answer", "observe_page", "webmcp_call"}
    assert "webmcp_call" not in tool_names("advanced", "page_fallback")
    assert {"click", "type", "answer"}.issubset(tool_names("advanced", "page_fallback"))


def test_answer_tool_uses_scalar_text_arg():
    state = AgentState(input="goal", url="")

    result = ActionNode.node_handler(state, agent=FakeAnswerAgent(), name="ActionAgent")

    assert result.sender == "END"
    assert result.messages[-1].content == "FINAL ANSWER \n 15213"


@pytest.mark.asyncio
async def test_browser_update_state_sets_webmcp_variant_stages(monkeypatch):
    monkeypatch.setattr(controller_mod, "discover_tools", fake_discover_tools)
    runner = AgentRunner()
    runner.env = FakeEnv()

    monkeypatch.setenv("CUGA_WEBMCP_MODE", "none")
    state = AgentState(input="goal", url="")
    await runner.browser_update_state(state)
    assert state.webmcp_prompt_stage == "standard"
    assert state.webmcp_tools == ""

    monkeypatch.setenv("CUGA_WEBMCP_MODE", "naive")
    state = AgentState(input="goal", url="")
    await runner.browser_update_state(state)
    assert state.webmcp_prompt_stage == "standard"
    assert "lookup" in state.webmcp_tools
    assert state.elements_as_string == "[1] button Example"

    monkeypatch.setenv("CUGA_WEBMCP_MODE", "advanced")
    state = AgentState(input="goal", url="")
    await runner.browser_update_state(state)
    assert state.webmcp_prompt_stage == "tool_stage"
    assert "lookup" in state.webmcp_tools

    state.webmcp_page_observed = True
    await runner.browser_update_state(state)
    assert state.webmcp_prompt_stage == "page_fallback"
    assert state.webmcp_tools == ""
    assert state.elements_as_string == "[1] button Example"
