from langgraph.types import Command
from loguru import logger

from cuga.backend.cuga_graph.nodes.shared.base_agent import create_partial
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames
from cuga.config import settings


def should_use_browser(state: AgentState) -> bool:
    mode = getattr(settings.advanced_features, "mode", "api")
    if mode in ("web", "hybrid"):
        return True
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
    async def node_handler(state: AgentState, name: str) -> Command:
        await state.manage_message_context()

        # Non-chat mode treats each *thread* as the conversation scope. Only
        # reset variables at the start of a thread; follow-up turns must keep
        # sandbox variables created on earlier turns (load tests, demo UI).
        if not settings.features.chat and not state.variables_storage:
            state.variables_manager.reset()

        if getattr(settings.supervisor, "enabled", False):
            logger.info("Supervisor enabled - routing to CugaSupervisor")
            return Command(update=state.model_dump(), goto=NodeNames.CUGA_SUPERVISOR)

        if should_use_browser(state):
            logger.info("Browser mode - routing to CugaBrowser")
            return Command(update=state.model_dump(), goto=NodeNames.CUGA_BROWSER)

        logger.info("Routing to CugaLite")
        return Command(update=state.model_dump(), goto=NodeNames.CUGA_LITE)
