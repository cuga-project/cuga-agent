import json
from typing import Literal, List, Optional, Tuple
from difflib import SequenceMatcher

import httpx
from pydantic import BaseModel
from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.tools_env.registry.utils.types import AppDefinition
from cuga.backend.cuga_graph.nodes.shared.base_agent import create_partial
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState, AnalyzeTaskAppsOutput
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_analyzer_agent.task_analyzer_agent import (
    TaskAnalyzerAgent,
    AnalyzeTaskOutput,
)
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_analyzer_agent.tasks.app_matcher import (
    AppMatch,
)
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames
from cuga.config import settings
from langgraph.types import Command
from loguru import logger
from cuga.backend.tools_env.registry.utils.api_utils import get_apps, count_total_tools, get_registry_base_url
from langchain_core.messages import AIMessage


tracker = ActivityTracker()


class TaskAnalyzer(BaseNode):
    def __init__(self, task_analyzer_agent: TaskAnalyzerAgent):
        super().__init__()
        self.name = task_analyzer_agent.name
        self.agent = task_analyzer_agent
        self.node = create_partial(
            TaskAnalyzer.node_handler,
            agent=self.agent,
            name=self.name,
        )

    @staticmethod
    def find_by_attribute(items: List[BaseModel], attr_name: str, attr_value) -> Optional[BaseModel]:
        """Find a Pydantic object by attribute value."""
        try:
            return next(item for item in items if getattr(item, attr_name) == attr_value)
        except StopIteration:
            return None

    @staticmethod
    def resolve_relevant_apps(
        requested_apps: List[str],
        available_apps: List[AppDefinition],
        typo_match_cutoff: float = 0.8,
        max_typo_length_delta: int = 2,
        min_typo_score_margin: float = 0.05,
    ) -> List[str]:
        """Resolve LLM-selected app names to known app names (with strict typo correction)."""
        by_lower_name = {app.name.lower(): app.name for app in available_apps if app and app.name}
        resolved: List[str] = []
        seen = set()

        for requested in requested_apps:
            normalized = requested.strip() if isinstance(requested, str) else ""
            if not normalized:
                continue

            exact = by_lower_name.get(normalized.lower())
            if exact:
                if exact not in seen:
                    seen.add(exact)
                    resolved.append(exact)
                continue

            normalized_lower = normalized.lower()
            scored_matches: List[Tuple[float, str]] = []
            for known_lower in by_lower_name.keys():
                if abs(len(known_lower) - len(normalized_lower)) > max_typo_length_delta:
                    continue
                score = SequenceMatcher(None, normalized_lower, known_lower).ratio()
                if score >= typo_match_cutoff:
                    scored_matches.append((score, known_lower))

            scored_matches.sort(key=lambda item: item[0], reverse=True)
            if scored_matches:
                best_score, best_match = scored_matches[0]
                close_top_matches = [
                    known_lower
                    for score, known_lower in scored_matches
                    if best_score - score < min_typo_score_margin
                ]
                if len(close_top_matches) > 1:
                    continue

                corrected = by_lower_name[best_match]
                logger.warning(f"Correcting unmatched app '{normalized}' to closest known app '{corrected}'")
                if corrected not in seen:
                    seen.add(corrected)
                    resolved.append(corrected)
                continue

            logger.warning(f"Dropping unmatched app '{normalized}' - no known connected app match found")

        return resolved

    @staticmethod
    async def match_apps(
        agent: TaskAnalyzerAgent,
        state: AgentState,
        mode: Literal['api', 'web', 'hybrid'],
        web_app_name: Optional[str] = "N/A",
        web_description: Optional[str] = "N/A",
    ) -> Tuple[Optional[List[AnalyzeTaskAppsOutput]], AppMatch]:
        """
        Match apps based on user intent and specified mode.

        Args:
            state: Current agent state
            intent: User intent to match against apps
            mode: Operation mode - 'api', 'web', or 'hybrid'

        Returns:
            Matched applications based on mode and intent
        """
        intent = state.input
        # Common initialization
        if mode == 'api' or mode == 'hybrid':
            apps = await get_apps()
            if mode == 'api' and len(apps) == 1:
                return [
                    AnalyzeTaskAppsOutput(
                        name=apps[0].name, description=apps[0].description, url=apps[0].url, type='api'
                    )
                ], AppMatch(relevant_apps=[apps[0].name], thoughts="")
            if mode == 'hybrid' and len(apps) == 1:
                return [
                    AnalyzeTaskAppsOutput(
                        name=apps[0].name, description=apps[0].description, url=apps[0].url, type='api'
                    ),
                    AnalyzeTaskAppsOutput(name=web_app_name, description=web_description, url="", type='web'),
                ], AppMatch(relevant_apps=[apps[0].name, web_app_name], thoughts="")
            # logger.debug(f"All available apps: {[p for p in apps]}")
            if len(settings.features.forced_apps) == 0:
                res: AppMatch = await agent.match_apps_task.ainvoke(
                    input={
                        "inp": {
                            "intent": intent,
                            "available_apps": [{"name": p.name, "description": p.description} for p in apps],
                        },
                    }
                )
            else:
                res = AppMatch(thoughts="", relevant_apps=settings.features.forced_apps)
            resolved_apps = TaskAnalyzer.resolve_relevant_apps(res.relevant_apps, apps)
            res = AppMatch(thoughts=res.thoughts, relevant_apps=resolved_apps)
            logger.debug(f"Matched apps: {res.relevant_apps}")
            result = []
            for p in res.relevant_apps:
                app: AppDefinition = TaskAnalyzer.find_by_attribute(apps, 'name', p)
                if not app:
                    logger.warning(f"Skipping unresolved app '{p}' after matching")
                    continue
                result.append(
                    AnalyzeTaskAppsOutput(name=p, description=app.description, url=app.url, type='api')
                )
            if mode == 'hybrid':
                result.append(
                    AnalyzeTaskAppsOutput(name=web_app_name, description=web_description, url="", type='web')
                )
            return result, res
        elif mode == 'web':
            return [
                AnalyzeTaskAppsOutput(name=web_app_name, description=web_description, url="", type='web')
            ], AppMatch(relevant_apps=[web_app_name], thoughts="")

    @staticmethod
    async def call_authenticate_apps(apps: List[str]):
        payload = {"apps": apps}  # JSON body
        async with httpx.AsyncClient() as client:
            registry_base = get_registry_base_url()
            response = await client.post(  # Changed from GET to POST
                f"{registry_base}/api/authenticate_apps",
                json=payload,  # Send as JSON body
            )
            print(response.status_code)
            print(response.json())

    @staticmethod
    async def should_use_supervisor_mode(state: AgentState) -> bool:
        """Determine if supervisor mode should be used.

        Args:
            state: Current agent state

        Returns:
            True if supervisor mode should be used
        """
        # Check if supervisor mode is enabled in settings
        supervisor_mode = getattr(settings.supervisor, 'enabled', False)

        if supervisor_mode:
            logger.info("Supervisor mode enabled - routing to CugaSupervisor")
            return True
        return False

    @staticmethod
    async def should_use_fast_mode_early(state: AgentState) -> bool:
        """Determine if fast mode (CugaLite) should be used before any LLM calls.

        Args:
            state: Current agent state

        Returns:
            True if fast mode should be used
        """
        # Use state lite_mode if set, otherwise fallback to settings
        lite_mode = state.lite_mode if state.lite_mode is not None else settings.advanced_features.lite_mode

        # Check if fast mode is enabled
        if lite_mode and settings.advanced_features.mode == 'api':
            total_tools = await count_total_tools()
            threshold = getattr(
                settings.advanced_features,
                'lite_mode_tool_threshold',
                settings.advanced_features.lite_mode_tool_threshold,
            )

            if total_tools < threshold:
                logger.info(
                    f"Fast mode enabled (state={state.lite_mode}, settings={settings.advanced_features.lite_mode}), mode is API, and total tools ({total_tools}) < threshold ({threshold}) - routing to CugaLite"
                )
                return True
            else:
                logger.info(
                    f"Fast mode enabled but total tools ({total_tools}) >= threshold ({threshold}) - not using fast mode"
                )
                return False
        return False

    @staticmethod
    async def node_handler(
        state: AgentState, agent: TaskAnalyzerAgent, name: str
    ) -> Command[Literal['TaskDecompositionAgent', 'CugaLite', 'CugaSupervisor', 'FinalAnswerAgent']]:
        # if not settings.features.chat:
        # state.variables_manager.reset()

        # Apply context management to message history at the start of task analysis
        await state.manage_message_context()

        # Check supervisor mode first (takes priority over fast mode)
        if await TaskAnalyzer.should_use_supervisor_mode(state):
            logger.info("Supervisor mode enabled - routing to CugaSupervisor")
            return Command(update=state.model_dump(), goto="CugaSupervisor")

        if await TaskAnalyzer.should_use_fast_mode_early(state):
            logger.info("Fast mode enabled - checking tool threshold")
            return Command(update=state.model_dump(), goto="CugaLite")

        if not settings.features.chat:
            state.variables_manager.reset()
        if not state.sender or state.sender == "ChatAgent":
            # Check fast mode early to skip LLM calls
            # Normal flow - do full task analysis
            state.api_intent_relevant_apps, app_matches = await TaskAnalyzer.match_apps(
                agent,
                state,
                settings.advanced_features.mode,
                state.current_app,
                state.current_app_description,
            )
            logger.debug(f"all apps are: {state.api_intent_relevant_apps}")

            if not state.api_intent_relevant_apps or len(state.api_intent_relevant_apps) == 0:
                logger.debug("No apps matched, routing to FinalAnswerAgent")
                try:
                    all_apps = await get_apps()
                    connected_apps = []
                    for app in all_apps:
                        app_info = f"- **{app.name}**"
                        if app.description:
                            description = app.description
                            max_length = 300
                            if len(description) > max_length:
                                description = description[:max_length] + '...'
                            app_info += f": {description}"
                        app_info += " (API)"
                        connected_apps.append(app_info)

                    if state.current_app:
                        web_app_name = state.current_app
                        web_app_description = state.current_app_description or "Web application"
                        connected_apps.append(f"- **{web_app_name}**: {web_app_description} (WEB)")

                    apps_list = (
                        "\n".join(connected_apps) if connected_apps else "No apps are currently connected."
                    )

                    message = (
                        "I wasn't able to find any applications that match your request. "
                        "This might be because the task doesn't match any of the available applications.\n\n"
                        f"**Connected Applications:**\n{apps_list}\n\n"
                        "Please try rephrasing your request or check if the necessary applications are connected."
                    )

                    state.final_answer = message
                    state.sender = name
                    tracker.collect_step(
                        step=Step(
                            name=name,
                            data=json.dumps(
                                {"message": "No apps matched", "connected_apps_count": len(connected_apps)}
                            ),
                        )
                    )
                    return Command(update=state.model_dump(), goto=NodeNames.FINAL_ANSWER_AGENT)
                except Exception as e:
                    logger.warning(f"Failed to get all apps: {e}")
                    message = (
                        "I wasn't able to find any applications that match your request. "
                        "Please try rephrasing your request or check if the necessary applications are connected."
                    )
                    state.final_answer = message
                    state.sender = name
                    return Command(update=state.model_dump(), goto=NodeNames.FINAL_ANSWER_AGENT)
            data_representation = json.dumps([p.model_dump() for p in state.api_intent_relevant_apps])
            try:
                if settings.advanced_features.benchmark == "appworld":
                    await TaskAnalyzer.call_authenticate_apps(app_matches.relevant_apps)
            except Exception as e:
                logger.warning("Failed to authenticate upfront all apps")
                logger.warning(e)
            state.messages.append(AIMessage(content=data_representation))
            tracker.collect_step(Step(name=name, data=data_representation))
            res = await agent.run(state)
            task_analyzer_output = AnalyzeTaskOutput(**json.loads(res.content))

            state.task_analyzer_output = task_analyzer_output
            if state.task_analyzer_output.paraphrased_intent:
                state.input = state.task_analyzer_output.paraphrased_intent
            if (
                settings.advanced_features.use_location_resolver
                and state.task_analyzer_output.attrs.requires_location_search
                and state.current_app == "map"
                and (state.sites and len(state.sites) == 1)
            ):
                logger.debug("Intent has implicit locations")
                return Command(update=state.model_dump(), goto="LocationResolver")

            return Command(update=state.model_dump(), goto="TaskDecompositionAgent")
        # We arrived from LocationResolver
        if state.sender == "LocationResolver" and state.task_analyzer_output.resolved_intent:
            state.input = state.task_analyzer_output.resolved_intent
            return Command(update=state.model_dump(), goto="TaskDecompositionAgent")
        return Command(update=state.model_dump(), goto="TaskDecompositionAgent")
