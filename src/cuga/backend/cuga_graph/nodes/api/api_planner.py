import json
import re
from typing import Literal


from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.cuga_graph.nodes.api.api_planner_agent.api_planner_agent import APIPlannerAgent
from cuga.backend.cuga_graph.nodes.api.api_planner_agent.prompts.load_prompt import (
    APIPlannerOutput,
    ActionName,
    APIPlannerInput,
)
from cuga.backend.cuga_graph.nodes.api.shortlister_agent.shortlister_agent import ShortlisterAgent
from cuga.backend.cuga_graph.nodes.shared.base_agent import create_partial
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState, SubTaskHistory
from langgraph.types import Command
from cuga.backend.cuga_graph.state.api_planner_history import HistoricalAction
from loguru import logger
from cuga.backend.tools_env.registry.utils.api_utils import get_apis

from langchain_core.tools import tool

from cuga.backend.llm.models import LLMManager
from cuga.config import settings
from cuga.configurations.instructions_manager import InstructionsManager
from cuga.backend.cuga_graph.nodes.api.tasks.reflection import reflection_task
from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
    FollowUpAction,
    ActionType,
)
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames, ActionIds


instructions_manager = InstructionsManager()
tracker = ActivityTracker()
llm_manager = LLMManager()


# --- Minimal tolerant planner parser (handles double-encoded JSON, code fences, minor key typos) ---
def _parse_planner_output_or_raise(raw: str) -> APIPlannerOutput:
    """
    Robust to:
      - plain JSON object
      - double-encoded JSON (a JSON string containing JSON)
      - code fences (```json ... ```) or extra text around JSON
    Pure parsing retries only; does not re-ask the LLM.
    """
    s = (raw or "").strip()

    # Strip code fences if present
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()

    last_err = None
    for _ in range(3):
        try:
            obj = json.loads(s)
        except Exception as e:
            last_err = e
            # Try to slice the outermost {...}
            first, last = s.find("{"), s.rfind("}")
            if first != -1 and last > first:
                s = s[first : last + 1].strip()
                continue
            break

        # If first loads produced a JSON string, decode again (double-encoded case)
        if isinstance(obj, str) and obj.strip().startswith("{"):
            s = obj.strip()
            continue

        return APIPlannerOutput(**obj)

    raise last_err or ValueError("Planner output could not be parsed")


@tool
def think(thought: str):
    """
    Use this tool to reflect and reason strategically.
    :param thought:
    :return:
    """
    return thought


class ApiPlanner(BaseNode):
    def __init__(self, router_agent: APIPlannerAgent):
        super().__init__()
        self.name = router_agent.name
        self.guidance = reflection_task(llm=llm_manager.get_model(settings.agent.planner.model))
        self.agent = router_agent
        self.node = create_partial(
            ApiPlanner.node_handler,
            agent=self.agent,
            strategic_agent=self.guidance,
            name=self.name,
        )

    @staticmethod
    def collect_history(state: AgentState, action: str, step: APIPlannerInput):
        obj = HistoricalAction(action_taken=action, input_to_agent=step, agent_output=None)
        state.api_planner_history.append(obj)

    @staticmethod
    def should_use_fast_mode_early(state: AgentState) -> bool:
        """Determine if fast mode (CugaLite) should be used before any LLM calls.

        Args:
            state: Current agent state

        Returns:
            True if fast mode should be used
        """
        # Use state lite_mode if set, otherwise fallback to settings
        lite_mode = state.lite_mode if state.lite_mode is not None else settings.advanced_features.lite_mode

        if lite_mode and settings.advanced_features.mode in ['api', 'hybrid']:
            logger.info(
                f"Fast mode enabled (state={state.lite_mode}, settings={settings.advanced_features.lite_mode}) and mode is API or Hybrid - routing to CugaLite from APIPlannerAgent"
            )
            return True
        return False

    @staticmethod
    async def count_tools_for_app(app_name: str) -> int:
        """Count total number of tools for a specific app.

        Args:
            app_name: Name of the app to count tools for

        Returns:
            Total number of tools for the specified app
        """
        try:
            apis = await get_apis(app_name)
            if apis:
                return len(apis.keys())
            return 0
        except Exception as e:
            logger.debug(f"Could not count tools for app {app_name}: {e}")
            return 0

    @staticmethod
    async def node_handler(
        state: AgentState, agent: APIPlannerAgent, strategic_agent, name: str
    ) -> Command[
        Literal[
            'APICodePlannerAgent',
            'ShortlisterAgent',
            'PlanControllerAgent',
            'SuggestHumanActions',
            'CugaLite',
        ]
    ]:
        # Check fast mode early to skip LLM calls
        if ApiPlanner.should_use_fast_mode_early(state):
            logger.info("Fast mode enabled - checking tool threshold for current app")

            # Get current app from state.sub_task_app (API planner assumes single app)
            if state.sub_task_app:
                current_app_name = state.sub_task_app
                tool_count = await ApiPlanner.count_tools_for_app(current_app_name)
                threshold = settings.advanced_features.lite_mode_tool_threshold
                logger.info(f"Current app '{current_app_name}' tools: {tool_count}, Threshold: {threshold}")
                if tool_count < threshold:
                    logger.info(
                        f"Tool count ({tool_count}) below threshold ({threshold}) - routing to CugaLite"
                    )
                    logger.info(f"APIPlannerAgent routing with state.sub_task: {state.sub_task}")
                    logger.info(f"APIPlannerAgent routing with state.sub_task_app: {state.sub_task_app}")
                    return Command(update=state.model_dump(), goto="CugaLite")

        # Handle human consultation response (only if HITL is enabled)
        if settings.advanced_features.api_planner_hitl:
            if state.sender == NodeNames.WAIT_FOR_RESPONSE and state.hitl_response:
                if state.hitl_response.action_id == ActionIds.CONSULT_WITH_HUMAN:
                    human_response = (
                        state.hitl_response.text_response
                        or state.hitl_response.selected_values
                        or "No response provided"
                    )
                    consultation_record = {
                        "question": state.api_planner_human_consultations[-1].get("question", "")
                        if state.api_planner_human_consultations
                        else "",
                        "response": human_response,
                        "timestamp": state.hitl_response.timestamp,
                    }
                    state.api_planner_human_consultations.append(consultation_record)
                    logger.debug(f"Human consultation response received: {human_response}")
                    state.sender = name

        # First time visit
        if (
            state.api_last_step
            and state.api_last_step == ActionName.CODER_AGENT
            and settings.features.code_output_reflection
        ):
            res_2 = await strategic_agent.ainvoke(
                {
                    "instructions": instructions_manager.get_instructions("api_reflection"),
                    "current_task": state.sub_task,
                    "agent_history": str(state.api_planner_history),
                    "shortlister_agent_output": "N/A",  # This would need to be populated from actual shortlister output
                    "coder_agent_output": f"Variables history: {state.variables_manager.get_variables_summary(last_n=5)}\n\nUser information ( User already logged in ): {state.pi}\n\nCurrent datetime: {tracker.current_date}",
                }
            )
            summary = res_2.content
            state.guidance = summary
            tracker.collect_step(step=Step(name=name, data=summary))
            logger.debug(f"Guidance:\n{summary}")

        res = await agent.run(state)
        state.guidance = None
        state.messages.append(res)
        try:
            res = APIPlannerOutput(**json.loads(res.content))
        except Exception as e1:
            logger.warning(f"Strict parse failed: {e1}; trying tolerant parse...")
            res = _parse_planner_output_or_raise(res.content)

        tracker.collect_step(step=Step(name=name, data=res.model_dump_json()))
        logger.debug("api_planner output:\n {}".format(res.model_dump_json(indent=4)))

        if res.action == ActionName.CODER_AGENT:
            state.api_last_step = ActionName.CODER_AGENT
            logger.debug("Current task is: code")
            state.coder_task = res.action_input_coder_agent.task_description
            state.coder_variables = res.action_input_coder_agent.context_variables_from_history
            state.coder_relevant_apis = res.action_input_coder_agent.relevant_apis
            state.api_shortlister_planner_filtered_apis = json.dumps(
                ShortlisterAgent.filter_by_api_names(
                    state.api_shortlister_all_filtered_apis,
                    [api.api_name for api in res.action_input_coder_agent.relevant_apis],
                ),
                indent=2,
            )

            ApiPlanner.collect_history(
                state=state, action=res.action.value, step=res.action_input_coder_agent
            )

            return Command(update=state.model_dump(), goto="APICodePlannerAgent")

        if res.action == ActionName.API_FILTERING_AGENT:
            state.api_last_step = ActionName.API_FILTERING_AGENT
            logger.debug("Current task is: shortlisting")
            ApiPlanner.collect_history(
                state=state, action=res.action.value, step=res.action_input_shortlisting_agent
            )

            state.shortlister_relevant_apps = [res.action_input_shortlisting_agent.app_name]
            state.shortlister_query = f"**Input task**: {res.action_input_shortlisting_agent.task_description}\n\nTask context:{state.sub_task}"
            logger.debug(state.model_dump())
            return Command(update=state.model_dump(), goto="ShortlisterAgent")

        if res.action == ActionName.CONCLUDE_TASK:
            state.api_last_step = ActionName.CONCLUDE_TASK
            state.guidance = None
            logger.debug("Current task is: conclude")
            ApiPlanner.collect_history(
                state=state, action=res.action.value, step=res.action_input_conclude_task
            )
            state.stm_all_history.append(
                SubTaskHistory(
                    sub_task=state.format_subtask(),
                    steps=[],
                    final_answer=res.action_input_conclude_task.final_response,
                )
            )
            state.last_planner_answer = res.action_input_conclude_task.final_response
            state.sender = "APIPlannerAgent"
            return Command(update=state.model_dump(), goto="PlanControllerAgent")

        if settings.advanced_features.api_planner_hitl and res.action == ActionName.CONSULT_WITH_HUMAN:
            state.api_last_step = ActionName.CONSULT_WITH_HUMAN
            logger.debug("Current task is: consult with human")
            ApiPlanner.collect_history(
                state=state, action=res.action.value, step=res.action_input_consult_with_human
            )

            consultation_input = {
                "question": res.action_input_consult_with_human.question,
                "context": res.action_input_consult_with_human.context,
                "suggested_options": res.action_input_consult_with_human.suggested_options,
            }
            state.api_planner_human_consultations.append(consultation_input)

            options = None
            action_type = ActionType.NATURAL_LANGUAGE
            if res.action_input_consult_with_human.suggested_options:
                from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
                    SelectOption,
                )

                action_type = ActionType.SELECT
                options = [
                    SelectOption(value=opt, label=opt)
                    for opt in res.action_input_consult_with_human.suggested_options
                ]

            state.hitl_action = FollowUpAction(
                action_id=ActionIds.CONSULT_WITH_HUMAN,
                action_name="Human Consultation",
                description=res.action_input_consult_with_human.question,
                type=action_type,
                callback_url="/consult",
                placeholder="Please provide your response...",
                options=options,
            )
            state.sender = name
            return Command(update=state.model_dump(), goto="SuggestHumanActions")

        return Command(update=state.model_dump(), goto="APICodePlannerAgent")

        # state.api_planner_codeagent_filtered_schemas_plan = res.content
        # return state
