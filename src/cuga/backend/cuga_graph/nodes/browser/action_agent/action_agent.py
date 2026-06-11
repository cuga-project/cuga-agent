from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.prompts import ChatPromptTemplate

from cuga.backend.cuga_graph.nodes.browser.action_agent.tools.tools import setup_tools
from cuga.backend.cuga_graph.nodes.browser.action_agent.tools.webmcp import webmcp_advanced_enabled
from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.llm.models import LLMManager
from cuga.backend.llm.utils.helpers import load_prompt_simple
from cuga.config import settings

llm_manager = LLMManager()


class ActionAgent(BaseAgent):
    def __init__(self, prompt_template: ChatPromptTemplate, llm: BaseChatModel, tools: Any = None):
        super().__init__()
        self.name = "ActionAgent"
        self.prompt_template = prompt_template
        self.llm = llm
        self.chain = self._build_chain(tools)
        self.tool_stage_chain = None
        self.page_fallback_chain = None
        if webmcp_advanced_enabled():
            self.tool_stage_chain = self._build_chain(setup_tools("tool_stage"))
            self.page_fallback_chain = self._build_chain(setup_tools("page_fallback"))

    def _build_chain(self, tools: Any):
        prompt = self.prompt_template.partial(
            tool_names=", ".join([tool.name for key, tool in tools.items()])
        )
        return prompt | self.llm.bind_tools(tools.values())

    @staticmethod
    def output_parser(result: BaseMessage, name) -> BaseMessage:
        result.name = name
        return result

    def run(self, input_variables: AgentState) -> AIMessage:
        data = input_variables.model_dump()
        if settings.advanced_features.mode == "hybrid":
            data["variables_history"] = input_variables.variables_manager.get_variables_summary(last_n=1)
        else:
            data["variables_history"] = ""

        chain = self.chain
        if webmcp_advanced_enabled():
            prompt_stage = getattr(input_variables, "webmcp_prompt_stage", "standard")
            if prompt_stage == "tool_stage" and self.tool_stage_chain is not None:
                chain = self.tool_stage_chain
            elif prompt_stage == "page_fallback" and self.page_fallback_chain is not None:
                chain = self.page_fallback_chain

        return chain.invoke(data)

    @staticmethod
    def create():
        dyna_model = settings.agent.action.model
        return ActionAgent(
            prompt_template=load_prompt_simple(
                "./prompts/system.jinja2",
                "./prompts/user.jinja2",
                model_config=dyna_model,
            ),
            llm=llm_manager.get_model(dyna_model),
            tools=setup_tools(),
        )
