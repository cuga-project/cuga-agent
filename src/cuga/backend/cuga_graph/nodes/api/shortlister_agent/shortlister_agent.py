import json
from typing import Any, List

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.language_models import BaseChatModel
from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.nodes.api.shortlister_agent.prompts.load_prompt import (
    ShortListerOutput,
    APIDetails,
    parser,
    ShortListerOutputLite,
)
from cuga.backend.llm.models import LLMManager
from cuga.backend.llm.utils.helpers import load_prompt_simple
from cuga.config import settings
from cuga.configurations.instructions_manager import InstructionsManager

instructions_manager = InstructionsManager()
tracker = ActivityTracker()
llm_manager = LLMManager()


# Example usage:
# response = get_petstore_apis()
# print(json.dumps(response, indent=2))
class ShortlisterAgent(BaseAgent):
    def __init__(
        self,
        prompt_template: ChatPromptTemplate,
        llm: BaseChatModel,
        tools: Any = None,
    ):
        super().__init__()
        self.name = "ShortlisterAgent"
        schema = ShortListerOutputLite if not settings.features.thoughts else ShortListerOutput
        self.chain = BaseAgent.get_chain(prompt_template, llm, schema)

    @staticmethod
    def get_function_names(res, apis):
        function_names = []
        for r in res.result:
            for ap in apis:
                if 'function' in ap and ap['function']['name'] == r.name:
                    function_names.append(ap)
                    break
                elif 'name' in ap and ap['name'] == r.name:
                    function_names.append(ap)
                    break
        return function_names

    @staticmethod
    def output_parser(result: ShortListerOutput, name) -> Any:
        result = AIMessage(content=result.model_dump(), name=name)
        return result

    @staticmethod
    def filter_by_api_names(data: dict, target_api_names: list) -> dict:
        """
        Filter the nested dictionary by matching api_name values.

        Args:
            data (dict): Structure like {app_name: {api_id: {app_name, api_name, ...}}}
            target_api_names (list): List of api_name strings to match.

        Returns:
            dict: Same structure but only with matching api_name entries.
        """
        result = {}

        for app_name, apis in data.items():
            matched_apis = {
                api_id: api_details
                for api_id, api_details in apis.items()
                if api_details.get("api_name") in target_api_names
            }
            if matched_apis:
                result[app_name] = matched_apis

        return result

    async def run(self, input_variables: AgentState) -> AIMessage:
        """Main execution method for API shortlisting"""
        if not settings.features.thoughts:
            shortlisted_apis: ShortListerOutputLite = await self.get_shortlisted_apis(
                input_variables,
                input_variables.sub_task_app,
                input_variables.api_shortlister_all_filtered_apis,
            )
            res = ShortListerOutput(thoughts=[], result=shortlisted_apis.result)
        else:
            res: ShortListerOutput = await self.get_shortlisted_apis(
                input_variables,
                input_variables.sub_task_app,
                input_variables.api_shortlister_all_filtered_apis,
            )
        return AIMessage(content=res.model_dump_json())

    async def get_shortlisted_apis(self, input_variables: AgentState, app_name: str, apis: dict):
        """Get shortlisted APIs for a specific app"""
        res = await self.chain.ainvoke(
            {
                "input": input_variables.shortlister_query,
                "instructions": instructions_manager.get_instructions(self.name),
                "api_shortlister_current_app": app_name,
                "api_shortlister_app_description": "",
                "api_shortlister_current_app_apis": json.dumps(apis, indent=2),
            }
        )
        return res

    @staticmethod
    def build_api_results(app_name: str, shortlisted_apis: List[APIDetails], apis: dict) -> dict:
        """Build the result dictionary for shortlisted APIs"""
        return ShortlisterAgent.filter_by_api_names(
            apis, target_api_names=[ap.name for ap in shortlisted_apis]
        )

    @staticmethod
    def create():
        dyna_model = settings.agent.shortlister.model
        return ShortlisterAgent(
            prompt_template=load_prompt_simple(
                "./prompts/system.jinja2",
                "./prompts/user.jinja2",
                model_config=dyna_model,
                format_instructions=BaseAgent.get_format_instructions(parser),
            ),
            llm=llm_manager.get_model(dyna_model),
        )
