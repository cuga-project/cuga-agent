import json
from typing import Literal

from opentelemetry import trace as otel_trace

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.cuga_graph.nodes.api.shortlister_agent.prompts.load_prompt import ShortListerOutput
from cuga.backend.cuga_graph.nodes.api.shortlister_agent.shortlister_agent import ShortlisterAgent
from cuga.backend.cuga_graph.nodes.shared.base_agent import create_partial
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from langchain_core.messages import AIMessage
from langgraph.types import Command
from cuga.backend.cuga_graph.state.api_planner_history import (
    FilteredApiEntry,
    ApiFilteringAgentHistoricalOutput,
)
from loguru import logger

tracker = ActivityTracker()


class ApiShortlister(BaseNode):
    def __init__(self, code_agent: ShortlisterAgent):
        super().__init__()
        self.name = code_agent.name
        self.agent = code_agent
        self.node = create_partial(
            ApiShortlister.node_handler,
            agent=self.agent,
            name=self.name,
        )

    @staticmethod
    def merge_apis(new_apis, all_apis):
        if all_apis is None:
            all_apis = {}

        for app_name, apis in new_apis.items():
            if app_name not in all_apis:
                all_apis[app_name] = []

            existing_names = {api.get('name') for api in all_apis[app_name] if api.get('name')}

            for api in apis:
                if api.get('name') and api['name'] not in existing_names:
                    all_apis[app_name].append(api)
                    existing_names.add(api['name'])

        return all_apis

    @staticmethod
    def get_reasoning_by_api_name(result: ShortListerOutput, api_name: str):
        for api in result.result:
            if api.name == api_name:
                return api.reasoning
        return None

    @staticmethod
    async def node_handler(
        state: AgentState, agent: ShortlisterAgent, name: str
    ) -> Command[Literal['APIPlannerAgent']]:
        logger.debug("Entered shortlisting")

        # First time visit
        res = await agent.run(state)
        # state.api_shortlister_planner_filtered_apis = res.content
        current_shortlisted: ShortListerOutput = ShortListerOutput(**json.loads(res.content))
        filtered_output_summary = []
        api_copy = ShortlisterAgent.build_api_results(
            state.sub_task_app, current_shortlisted.result, state.api_shortlister_all_filtered_apis
        )
        for app_name, apis_map in api_copy.items():
            for api in apis_map.values():
                filtered_output_summary.append(
                    FilteredApiEntry(
                        app_name=api["app_name"],
                        api_name=api['api_name'],
                        description=api.get('description'),
                        reasoning=ApiShortlister.get_reasoning_by_api_name(
                            current_shortlisted, api['api_name']
                        ),
                    )
                )
        span = otel_trace.get_current_span()
        span.set_attribute("cuga.api_shortlister.suggested_count", len(current_shortlisted.result))
        span.set_attribute("cuga.api_shortlister.resolved_count", len(filtered_output_summary))
        state.api_planner_history[-1].agent_output = ApiFilteringAgentHistoricalOutput(
            filtered_apis=filtered_output_summary
        )
        tracker.collect_step(step=Step(name=name, data=current_shortlisted.model_dump_json()))
        logger.debug("\n" + current_shortlisted.model_dump_json(indent=2))
        state.messages.append(AIMessage(content=current_shortlisted.model_dump_json()))
        return Command(update=state.model_dump(), goto="APIPlannerAgent")
