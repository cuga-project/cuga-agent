import json
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI

from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_decomposition_agent.prompts.load_prompt import (
    TaskDecompositionPlan,
    TaskDecompositionMultiOutput,
    parser,
)
from cuga.backend.llm.models import LLMManager
from cuga.backend.llm.utils.helpers import load_prompt_simple
from cuga.config import settings
from cuga.configurations.instructions_manager import InstructionsManager


instructions_manager = InstructionsManager()
llm_manager = LLMManager()


class TaskDecompositionAgent(BaseAgent):
    def __init__(self, prompt_template: ChatPromptTemplate, llm: ChatOpenAI, tools: Any = None):
        super().__init__()
        self.name = "TaskDecompositionAgent"
        # enable_format = settings.agent.task_decomposition.model.enable_format
        parser = RunnableLambda(TaskDecompositionAgent.output_parser)
        dyna_model = settings.agent.task_decomposition.model

        # multi_parser = PydanticOutputParser(pydantic_object=TaskDecompositionMultiOutput)
        self.chain_multi = BaseAgent.get_chain(
            prompt_template=load_prompt_simple(
                system_path="prompts/system_multi.jinja2",
                user_path="prompts/user_multi.jinja2",
                model_config=dyna_model,
            ),
            llm=llm,
            schema=TaskDecompositionMultiOutput,
        )
        self.chain = BaseAgent.get_chain(prompt_template, llm, TaskDecompositionPlan) | (
            parser.bind(name=self.name)
        )

    @staticmethod
    def output_parser(result: TaskDecompositionPlan, name) -> Any:
        result = AIMessage(content=json.dumps(result.model_dump()), name=name)
        return result

    async def run(self, input_variables: AgentState) -> AIMessage:
        data = input_variables.model_dump()
        data["instructions"] = instructions_manager.get_instructions(self.name)
        data["decomposition_strategy"] = settings.advanced_features.decomposition_strategy

        if input_variables.sites is not None and len(input_variables.sites) > 1:
            out = await self.chain_multi.ainvoke(data)
            result = AIMessage(content=json.dumps(out.model_dump()), name=self.name)
            return result
        else:
            return await self.chain.ainvoke(data)

    @staticmethod
    def create():
        dyna_model = settings.agent.task_decomposition.model
        return TaskDecompositionAgent(
            prompt_template=load_prompt_simple(
                system_path="prompts/system.jinja2",
                user_path="prompts/user_msg.jinja2",
                model_config=dyna_model,
                format_instructions=parser,
            ),
            llm=llm_manager.get_model(dyna_model),
        )
