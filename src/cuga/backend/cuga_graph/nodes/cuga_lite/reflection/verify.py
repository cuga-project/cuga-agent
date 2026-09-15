from langchain_core.runnables import Runnable
from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.llm.utils.helpers import load_prompt_simple


def verify_task(llm, enable_format=False) -> Runnable:
    prompt_template = load_prompt_simple(
        "./prompts/verify_system.jinja2",
        "./prompts/verify_user.jinja2",
    )
    return BaseAgent.get_chain(prompt_template, llm, wx_json_mode="no_format")
