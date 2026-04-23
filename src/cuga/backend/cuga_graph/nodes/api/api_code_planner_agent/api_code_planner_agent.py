from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.language_models import BaseChatModel
from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.nodes.api.api_code_planner_agent.prompts.load_prompt import parser
from cuga.backend.llm.models import LLMManager
from cuga.backend.llm.utils.helpers import load_prompt_simple
from cuga.config import settings
from langchain_core.tools import tool
from cuga.configurations.instructions_manager import InstructionsManager

instructions_manager = InstructionsManager()
llm_manager = LLMManager()
tracker = ActivityTracker()


@tool
def report_missing_api(message: str):
    """
    `report_missing_api(message: str)`: Use this tool **only** when the available tools are insufficient to achieve the user's goal. The message parameter should clearly describe what specific API or capability is missing and why it's needed to complete the task.
    """

    return message + ", I advise calling ApiShortlistingAgent. with refined task."


class APICodePlannerAgent(BaseAgent):
    def __init__(self, prompt_template: ChatPromptTemplate, llm: BaseChatModel, tools: Any = None):
        super().__init__()
        self.name = "APICodePlannerAgent"
        self.chain = prompt_template | llm.bind_tools([report_missing_api])

    @staticmethod
    def output_parser(result: AIMessage, name) -> Any:
        result = AIMessage(content=result.content, name=name)
        return result

    async def run(self, input_variables: AgentState) -> BaseMessage:
        context_variables = input_variables.coder_variables
        context_variables_preview = (
            input_variables.variables_manager.get_variables_summary(context_variables)
            if context_variables and len(context_variables) > 0
            else "N/A"
        )

        return await self.chain.ainvoke(
            input={
                "current_datetime": input_variables.current_datetime,
                "variables_preview": context_variables_preview,
                "coder_task": input_variables.coder_task,
                "instructions": instructions_manager.get_instructions(self.name),
                "api_shortlister_planner_filtered_apis": input_variables.api_shortlister_planner_filtered_apis,
            }
        )

    @staticmethod
    def create():
        dyna_model = settings.agent.code_planner.model
        # check if settings.feature.code_generation is fast
        if settings.features.code_generation == "fast":
            prompt_template = load_prompt_simple(
                "./prompts/system_fast.jinja2",
                "./prompts/user.jinja2",
                model_config=dyna_model,
                format_instructions=BaseAgent.get_format_instructions(parser),
            )
        else:
            prompt_template = load_prompt_simple(
                "./prompts/system.jinja2",
                "./prompts/user.jinja2",
                model_config=dyna_model,
                format_instructions=BaseAgent.get_format_instructions(parser),
            )
        return APICodePlannerAgent(
            prompt_template=prompt_template,
            llm=llm_manager.get_model(dyna_model),
        )
