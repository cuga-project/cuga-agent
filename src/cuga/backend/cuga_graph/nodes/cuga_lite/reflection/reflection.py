from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.llm.models import LLMManager
from cuga.backend.llm.utils.helpers import load_prompt_simple

llm_manager = LLMManager()


def reflection_prompt() -> ChatPromptTemplate:
    return load_prompt_simple(
        "./prompts/reflection_system.jinja2",
        "./prompts/reflection_user.jinja2",
    )


def reflection_task(llm, enable_format=False) -> Runnable:
    return BaseAgent.get_chain(reflection_prompt(), llm, wx_json_mode="no_format")
