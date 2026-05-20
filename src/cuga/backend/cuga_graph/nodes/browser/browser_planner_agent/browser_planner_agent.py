import json
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_core.language_models import BaseChatModel
from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.nodes.browser.browser_planner_agent.prompts.load_prompt import (
    NextAgentPlan,
    parser,
)
from cuga.backend.llm.models import LLMManager
from cuga.backend.llm.utils.helpers import load_prompt_with_image, model_supports_vision
from cuga.config import settings

llm_manager = LLMManager()
tracker = ActivityTracker()


class BrowserPlannerAgent(BaseAgent):
    def __init__(
        self,
        prompt_template: ChatPromptTemplate,
        llm: BaseChatModel,
        tools: Any = None,
        use_vision_effective: bool = True,
    ):
        super().__init__()
        self.name = "BrowserPlannerAgent"
        parser = RunnableLambda(BrowserPlannerAgent.output_parser)
        self.chain = BaseAgent.get_chain(prompt_template, llm, NextAgentPlan) | (parser.bind(name=self.name))
        # Mirror the gate applied to the prompt template so the system prompt's
        # ``{% if use_vision %}`` block and the runtime image attachment stay in
        # sync with whether the resolved model accepts multimodal content.
        self.use_vision_effective = use_vision_effective

    @staticmethod
    def output_parser(result: NextAgentPlan, name) -> Any:
        result = AIMessage(content=json.dumps(result.model_dump()), name=name)
        return result

    async def run(self, input_variables: AgentState) -> AIMessage:
        if (
            (input_variables.current_app == "gitlab" or input_variables.current_app == "shopping_admin")
            and len(input_variables.stm_steps_history) == 0
            and len(input_variables.task_decomposition.task_decomposition) == 1
        ):
            pass
        data = input_variables.model_dump()
        data.update({"use_vision": self.use_vision_effective})
        if settings.advanced_features.mode == "hybrid":
            data["variables_history"] = input_variables.variables_manager.get_variables_summary(last_n=1)
        else:
            data["variables_history"] = ""
        if self.use_vision_effective and getattr(tracker, "images", None):
            # Only attach an image if one has been captured
            if len(tracker.images) > 0:
                data['img'] = tracker.images[-1]
        return await self.chain.ainvoke(data)

    @staticmethod
    def create():
        dyna_model = settings.agent.planner.model
        return BrowserPlannerAgent(
            prompt_template=load_prompt_with_image(
                "./prompts/system.jinja2",
                "./prompts/user.jinja2",
                model_config=dyna_model,
                format_instructions=BaseAgent.get_format_instructions(parser),
            ),
            llm=llm_manager.get_model(dyna_model),
            use_vision_effective=settings.advanced_features.use_vision and model_supports_vision(dyna_model),
        )
