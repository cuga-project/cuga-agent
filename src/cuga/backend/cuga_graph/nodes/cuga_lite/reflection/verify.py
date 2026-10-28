from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.llm.utils.helpers import load_prompt_simple


def verify_prompt() -> ChatPromptTemplate:
    return load_prompt_simple(
        "./prompts/verify_system.jinja2",
        "./prompts/verify_user.jinja2",
    )


def verify_task(llm, enable_format=False) -> Runnable:
    return BaseAgent.get_chain(verify_prompt(), llm, wx_json_mode="no_format")
