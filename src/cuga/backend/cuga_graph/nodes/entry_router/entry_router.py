import json

from langgraph.types import Command
from loguru import logger

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.cuga_graph.nodes.shared.base_agent import create_partial
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames
from cuga.config import settings

tracker = ActivityTracker()


def should_use_browser(state: AgentState) -> bool:
    mode = getattr(settings.advanced_features, "mode", "api")
    if mode == "web":
        return True
    if mode == "hybrid":
        if state.hybrid_phase is not None:
            return state.hybrid_phase == "web"
        return getattr(state, "sub_task_type", None) == "web"
    if getattr(state, "sub_task_type", None) == "web":
        return True
    if state.current_app and state.url:
        return True
    return False


class EntryRouter(BaseNode):
    def __init__(self):
        super().__init__()
        self.name = NodeNames.ENTRY_ROUTER
        self.node = create_partial(EntryRouter.node_handler, name=self.name)

    @staticmethod
    def _route(state: AgentState, name: str, target: str, message: str) -> Command:
        logger.info(message)
        tracker.collect_step(Step(name=name, data=json.dumps({"route": target})))
        return Command(update=state.model_dump(), goto=target)

    @staticmethod
    async def node_handler(state: AgentState, name: str) -> Command:
        await state.manage_message_context()

        # Non-chat mode treats each *thread* as the conversation scope. Only
        # reset variables at the start of a thread; follow-up turns must keep
        # sandbox variables created on earlier turns (load tests, demo UI).
        if not settings.features.chat and not state.variables_storage:
            state.variables_manager.reset()
        if not settings.features.chat:
            state.sub_task = None
            state.sub_task_app = None

        if getattr(settings.supervisor, "enabled", False):
            return EntryRouter._route(
                state,
                name,
                NodeNames.CUGA_SUPERVISOR,
                "Supervisor enabled - routing to CugaSupervisor",
            )

        mode = getattr(settings.advanced_features, "mode", "api")
        if (
            mode == "hybrid"
            and not settings.features.chat
            and state.sub_task_type == "hybrid"
            and state.hybrid_phase is None
        ):
            state.hybrid_original_task = state.input
            state.hybrid_api_task = state.input
            state.hybrid_web_task = state.input
            state.hybrid_phase = "api"
            state.sub_task_type = "hybrid"

        if should_use_browser(state):
            return EntryRouter._route(
                state,
                name,
                NodeNames.CUGA_BROWSER,
                "Browser mode - routing to CugaBrowser",
            )

        return EntryRouter._route(
            state,
            name,
            NodeNames.CUGA_LITE,
            "Routing to CugaLite",
        )
