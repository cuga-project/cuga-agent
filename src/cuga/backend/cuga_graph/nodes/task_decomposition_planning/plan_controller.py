import json
import uuid
from typing import Literal

from langchain_core.messages import AIMessage
from langgraph.types import Command
from loguru import logger
from langchain_core.runnables.config import RunnableConfig
from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.tools_env.registry.utils.api_utils import get_apis, get_apps
from cuga.backend.cuga_graph.nodes.shared.base_agent import create_partial
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState, SubTaskHistory, AnalyzeTaskAppsOutput
from cuga.config import settings
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.plan_controller_agent.plan_controller_agent import (
    PlanControllerAgent,
)
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.plan_controller_agent.prompts.load_prompt import (
    PlanControllerOutput,
)

tracker = ActivityTracker()


def find_substring(string_array, target_string):
    """
    Check if any string from string_array is contained within target_string.
    Returns the first matching string found, or None if no matches.

    Args:
        string_array (list): List of strings to search for
        target_string (str): String to search within

    Returns:
        str or None: First matching string found, or None if no matches
    """
    for substring in string_array:
        if substring in target_string:
            return substring
    return None


class PlanControllerNode(BaseNode):
    def __init__(self, plan_controller_agent: PlanControllerAgent):
        super().__init__()
        self.plan_controller_agent = plan_controller_agent
        self.node = create_partial(
            PlanControllerNode.node_handler,
            agent=self.plan_controller_agent,
            name=self.plan_controller_agent.name,
        )

    @staticmethod
    async def node_handler(
        state: AgentState, agent: PlanControllerAgent, name: str, config: RunnableConfig
    ) -> Command[
        Literal[
            "BrowserPlannerAgent",
            "APIPlannerAgent",
            "FinalAnswerAgent",
            "PlanControllerAgent",
            "InterruptToolNode",
        ]
    ]:
        ignore_controller = (
            len(state.task_decomposition.task_decomposition) == 1
            or len(state.task_decomposition.task_decomposition) == 0
        )
        # API Agent must return list of natural language progress, summarize the plan relative to the output of code
        # Examples for 3 modes
        # Final answer ifs

        if state.sender == "TaskDecompositionAgent":
            # Add forced apps to api_intent_relevant_apps when arriving from task decomposition
            force_lite_apps = getattr(settings.advanced_features, 'force_lite_mode_apps', [])
            if force_lite_apps:
                try:
                    all_apps = await get_apps()
                except Exception:
                    all_apps = []
                if state.api_intent_relevant_apps is None:
                    state.api_intent_relevant_apps = []
                existing_app_names = {app.name for app in state.api_intent_relevant_apps}
                for forced_app_name in force_lite_apps:
                    if forced_app_name not in existing_app_names:
                        app_info = next((app for app in all_apps if app.name == forced_app_name), None)
                        if app_info:
                            state.api_intent_relevant_apps.append(
                                AnalyzeTaskAppsOutput(
                                    name=app_info.name,
                                    description=app_info.description,
                                    url=app_info.url,
                                    type='api',
                                )
                            )
                            logger.info(
                                f"Added forced lite app '{forced_app_name}' to api_intent_relevant_apps"
                            )

            if ignore_controller:
                state.sub_task = state.task_decomposition.task_decomposition[0].task
                state.sub_task_app = state.task_decomposition.task_decomposition[0].app
                state.sub_task_type = state.task_decomposition.task_decomposition[0].type
                if state.sub_task_type == "api":
                    state.api_intent_relevant_apps_current = [
                        app
                        for app in state.api_intent_relevant_apps
                        if app.name == state.task_decomposition.task_decomposition[0].app
                    ]
                    if not state.api_intent_relevant_apps_current:
                        # Check if it's a forced lite app
                        force_lite_apps = getattr(settings.advanced_features, 'force_lite_mode_apps', [])
                        if state.task_decomposition.task_decomposition[0].app in force_lite_apps:
                            all_apps = await get_apps()
                            app_info = next(
                                (
                                    app
                                    for app in all_apps
                                    if app.name == state.task_decomposition.task_decomposition[0].app
                                ),
                                None,
                            )
                            if app_info:
                                state.api_intent_relevant_apps_current = [
                                    AnalyzeTaskAppsOutput(
                                        name=app_info.name,
                                        description=app_info.description,
                                        url=app_info.url,
                                        type='api',
                                    )
                                ]
                                logger.info(
                                    f"Added forced lite app '{state.task_decomposition.task_decomposition[0].app}' to api_intent_relevant_apps_current"
                                )

                    if state.api_shortlister_all_filtered_apis is None:
                        state.api_shortlister_all_filtered_apis = {}
                    # Skip API fetching if it's a forced lite app
                    if state.api_intent_relevant_apps_current:
                        force_lite_apps = getattr(settings.advanced_features, 'force_lite_mode_apps', [])
                        if state.api_intent_relevant_apps_current[0].name not in force_lite_apps:
                            state.api_shortlister_all_filtered_apis[
                                state.api_intent_relevant_apps_current[0].name
                            ] = await get_apis(state.api_intent_relevant_apps_current[0].name)
                state.messages.append(
                    AIMessage(
                        content=PlanControllerOutput(
                            thoughts=[],
                            next_subtask=state.sub_task,
                            subtasks_progress=[],
                            conclude_task=False,
                            conclude_final_answer="",
                            next_subtask_app=state.sub_task_app,
                            next_subtask_type=state.sub_task_type,
                        ).model_dump_json()
                    )
                )
                if state.sub_task_type == 'web':
                    return Command(update=state.model_dump(), goto="BrowserPlannerAgent")
                else:
                    state.api_planner_history = []
                    return Command(update=state.model_dump(), goto="APIPlannerAgent")
        state.sender = name

        # Else is loop return
        logger.debug("returning from planner or api agent")
        if ignore_controller and state.last_planner_answer:
            state.messages.append(
                AIMessage(
                    content=PlanControllerOutput(
                        thoughts=[],
                        next_subtask=state.sub_task,
                        subtasks_progress=[],
                        conclude_task=True,
                        conclude_final_answer=state.last_planner_answer or "",
                        next_subtask_app=state.sub_task_app,
                        next_subtask_type=state.sub_task_type,
                    ).model_dump_json()
                )
            )
            logger.debug("ignore controller use last planner or api answer")
            return Command(update=state.model_dump(), goto="FinalAnswerAgent")

        result: AIMessage = await agent.run(state)
        plan_controller_output = PlanControllerOutput(**json.loads(result.content))
        tracker.collect_step(step=Step(name=name, data=plan_controller_output.model_dump_json()))
        state.messages.append(result)

        state.sub_tasks_progress = plan_controller_output.subtasks_progress

        if plan_controller_output.conclude_task or (
            all(status == "completed" for status in plan_controller_output.subtasks_progress)
            and plan_controller_output.next_subtask == ""
        ):
            state.last_planner_answer = plan_controller_output.conclude_final_answer
            return Command(update=state.model_dump(), goto="FinalAnswerAgent")
        else:
            if "open application" in plan_controller_output.next_subtask:
                app = find_substring(
                    ["reddit", "map", "wikipedia", "gitlab", "shopping", "shopping_admin"],
                    plan_controller_output.next_subtask.lower(),
                )
                state.tool_call = {"name": "open_app", "args": {"app_name": app}, "id": str(uuid.uuid4())}
                state.stm_all_history.append(
                    SubTaskHistory(
                        sub_task=plan_controller_output.next_subtask,
                        steps=[f"Navigated to {app}"],
                        final_answer="The application opened successfully",
                    )
                )
                return Command(update=state.model_dump(), goto="InterruptToolNode")

            # Updates current sub task for UI, API Planners
            state.sub_task = plan_controller_output.next_subtask
            state.sub_task_app = plan_controller_output.next_subtask_app
            state.sub_task_type = plan_controller_output.next_subtask_type
            logger.info(f"PlanController setting state.sub_task to: {state.sub_task}")
            logger.info(f"PlanController setting state.sub_task_app to: {state.sub_task_app}")
            logger.info(f"PlanController setting state.sub_task_type to: {state.sub_task_type}")
            if plan_controller_output.next_subtask_type == "api":
                # Clear chat agent messages when switching to API tasks
                state.chat_messages = []
                if not plan_controller_output.next_subtask_app:
                    logger.error(
                        f"PlanControllerAgent returned next_subtask_type='api' but next_subtask_app is empty. "
                        f"This violates the output schema. next_subtask: {plan_controller_output.next_subtask}"
                    )
                    raise ValueError(
                        "PlanControllerAgent must specify next_subtask_app when next_subtask_type is 'api'"
                    )
                state.api_intent_relevant_apps_current = [
                    app
                    for app in state.api_intent_relevant_apps
                    if app.name == plan_controller_output.next_subtask_app
                ]
                if not state.api_intent_relevant_apps_current:
                    # Check if it's a forced lite app
                    force_lite_apps = getattr(settings.advanced_features, 'force_lite_mode_apps', [])
                    if plan_controller_output.next_subtask_app in force_lite_apps:
                        # Get app info and create AnalyzeTaskAppsOutput
                        all_apps = await get_apps()
                        app_info = next(
                            (app for app in all_apps if app.name == plan_controller_output.next_subtask_app),
                            None,
                        )
                        if app_info:
                            state.api_intent_relevant_apps_current = [
                                AnalyzeTaskAppsOutput(
                                    name=app_info.name,
                                    description=app_info.description,
                                    url=app_info.url,
                                    type='api',
                                )
                            ]
                            logger.info(
                                f"Added forced lite app '{plan_controller_output.next_subtask_app}' to api_intent_relevant_apps_current"
                            )
                            # Skip API fetching for forced lite apps
                        else:
                            logger.warning(
                                f"Forced lite app '{plan_controller_output.next_subtask_app}' not found in available apps"
                            )
                    else:
                        logger.error(
                            f"No matching app found for next_subtask_app='{plan_controller_output.next_subtask_app}'. "
                            f"Available apps: {[app.name for app in state.api_intent_relevant_apps]}"
                        )
                        raise ValueError(
                            f"App '{plan_controller_output.next_subtask_app}' not found in api_intent_relevant_apps"
                        )

                # Only fetch APIs if we have a valid app and it's not a forced lite app
                if state.api_intent_relevant_apps_current:
                    force_lite_apps = getattr(settings.advanced_features, 'force_lite_mode_apps', [])
                    if state.api_intent_relevant_apps_current[0].name not in force_lite_apps:
                        state.api_shortlister_all_filtered_apis = {}
                        state.api_shortlister_all_filtered_apis[
                            state.api_intent_relevant_apps_current[0].name
                        ] = await get_apis(state.api_intent_relevant_apps_current[0].name)

            state.previous_steps = []
            if state.sites and len(state.sites) > 0:
                pass
            else:
                state.stm_steps_history = []

            if plan_controller_output.next_subtask_type == 'web':
                return Command(update=state.model_dump(), goto="BrowserPlannerAgent")
            else:
                state.api_planner_history = []
                return Command(update=state.model_dump(), goto="APIPlannerAgent")
