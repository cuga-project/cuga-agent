from typing import Any, Optional

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.language_models import BaseChatModel
from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.nodes.api.api_planner_agent.prompts.load_prompt import (
    APIPlannerOutput,
    APIPlannerOutputLite,
    APIPlannerOutputNoHITL,
    APIPlannerOutputLiteNoHITL,
)
from cuga.backend.llm.models import LLMManager
from cuga.backend.llm.utils.helpers import load_prompt_simple
from cuga.config import settings
from cuga.configurations.instructions_manager import InstructionsManager

try:
    from langchain_openai import ChatOpenAI
except ImportError:
    ChatOpenAI = None

try:
    from langchain_groq import ChatGroq
except ImportError:
    ChatGroq = None

try:
    from langchain_ibm import ChatWatsonx
except ImportError:
    ChatWatsonx = None

instructions_manager = InstructionsManager()
tracker = ActivityTracker()
llm_manager = LLMManager()


def _get_model_identifier(llm: BaseChatModel) -> Optional[str]:
    """
    Safely extract model identifier from different LLM classes.

    Supports:
    - ChatWatsonx: uses model_id attribute
    - ChatOpenAI: uses model_name attribute
    - ChatGroq: uses model attribute
    - Other BaseChatModel subclasses: tries model_id, model_name, model in that order

    Args:
        llm: The language model instance

    Returns:
        Model identifier string or None if not found
    """
    if ChatWatsonx is not None and isinstance(llm, ChatWatsonx):
        return getattr(llm, 'model_id', None)
    elif ChatOpenAI is not None and isinstance(llm, ChatOpenAI):
        return getattr(llm, 'model_name', None)
    elif ChatGroq is not None and isinstance(llm, ChatGroq):
        return getattr(llm, 'model', None)
    else:
        # Try common attribute names in order of preference
        for attr in ['model_id', 'model_name', 'model']:
            if hasattr(llm, attr):
                value = getattr(llm, attr)
                if value:
                    return str(value)
    return None


class APIPlannerAgent(BaseAgent):
    def __init__(self, prompt_template: ChatPromptTemplate, llm: BaseChatModel, tools: Any = None):
        super().__init__()
        self.name = "APIPlannerAgent"

        model_id = _get_model_identifier(llm)
        self.thoughts_enabled = not (model_id and "oss" in model_id) and settings.features.thoughts

        if settings.advanced_features.api_planner_hitl:
            schema = APIPlannerOutputLite if not self.thoughts_enabled else APIPlannerOutput
        else:
            schema = APIPlannerOutputLiteNoHITL if not self.thoughts_enabled else APIPlannerOutputNoHITL

        self.chain = BaseAgent.get_chain(prompt_template=prompt_template, llm=llm, schema=schema)

    def output_parser(result: AIMessage, name) -> Any:
        result = AIMessage(content=result.content, name=name)
        return result

    async def run(self, input_variables: AgentState) -> AIMessage:
        data = input_variables.model_dump()
        data['variables_summary'] = input_variables.variables_manager.get_variables_summary()
        data["instructions"] = instructions_manager.get_instructions(self.name)
        res = await self.chain.ainvoke(data)

        if not self.thoughts_enabled:
            lite_res = res
            if settings.advanced_features.api_planner_hitl:
                full_res = APIPlannerOutput(
                    thoughts=[],
                    action=lite_res.action,
                    action_input_shortlisting_agent=lite_res.action_input_shortlisting_agent,
                    action_input_coder_agent=lite_res.action_input_coder_agent,
                    action_input_conclude_task=lite_res.action_input_conclude_task,
                    action_input_consult_with_human=lite_res.action_input_consult_with_human,
                )
            else:
                full_res = APIPlannerOutput(
                    thoughts=[],
                    action=lite_res.action,
                    action_input_shortlisting_agent=lite_res.action_input_shortlisting_agent,
                    action_input_coder_agent=lite_res.action_input_coder_agent,
                    action_input_conclude_task=lite_res.action_input_conclude_task,
                    action_input_consult_with_human=None,
                )
            return AIMessage(content=full_res.model_dump_json())
        else:
            if not settings.advanced_features.api_planner_hitl:
                if hasattr(res, 'action_input_consult_with_human'):
                    res_dict = res.model_dump()
                    res_dict['action_input_consult_with_human'] = None
                    full_res = APIPlannerOutput(**res_dict)
                    return AIMessage(content=full_res.model_dump_json())
            return AIMessage(content=res.model_dump_json())

    @staticmethod
    def create():
        dyna_model = settings.agent.planner.model

        if settings.advanced_features.api_planner_hitl:
            system_prompt = "./prompts/system_hitl.jinja2"
            user_prompt = "./prompts/user_hitl.jinja2"
        else:
            system_prompt = "./prompts/system.jinja2"
            user_prompt = "./prompts/user.jinja2"

        return APIPlannerAgent(
            prompt_template=load_prompt_simple(
                system_prompt,
                user_prompt,
                model_config=dyna_model,
            ),
            llm=llm_manager.get_model(dyna_model),
        )
