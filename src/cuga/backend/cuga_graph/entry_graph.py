from typing import Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.constants import END, START
from langgraph.graph import StateGraph
from loguru import logger

from cuga.backend.cuga_graph.nodes.entry_router import EntryRouter
from cuga.backend.cuga_graph.nodes.answer.final_answer import FinalAnswerNode
from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.final_answer_agent import FinalAnswerAgent
from cuga.backend.cuga_graph.nodes.browser.action import ActionNode
from cuga.backend.cuga_graph.nodes.chat.chat import ChatNode
from cuga.backend.cuga_graph.nodes.browser.action_agent.action_agent import ActionAgent
from cuga.backend.cuga_graph.nodes.browser.browser_planner import PlannerNode
from cuga.backend.cuga_graph.nodes.browser.browser_planner_agent.browser_planner_agent import (
    BrowserPlannerAgent,
)
from cuga.backend.cuga_graph.nodes.browser.qa_agent.qa_agent import QaAgent
from cuga.backend.cuga_graph.nodes.browser.qa_agent_node import QaNode
from cuga.backend.cuga_graph.nodes.cuga_browser.cuga_browser_node import CugaBrowserNode
from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import create_cuga_lite_graph
from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node import CugaLiteNode
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.combined import CombinedToolProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ensure_toolguard_provider
from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_graph import create_cuga_supervisor_graph
from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_node import CugaSupervisorNode
from cuga.backend.cuga_graph.nodes.human_in_the_loop.suggest_actions import SuggestHumanActions
from cuga.backend.cuga_graph.nodes.human_in_the_loop.wait_for_response import WaitForResponse
from cuga.backend.cuga_graph.nodes.shared.interrupt_tool_node import InterruptToolNode
from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames
from cuga.backend.llm.models import LLMManager, create_llm_from_config
from cuga.config import settings


class CugaEntryGraph:
    """Thin entry graph: Chat -> EntryRouter -> Lite | Supervisor | Browser -> FinalAnswer."""

    def __init__(
        self,
        configurations,
        langfuse_handler=None,
        policy_system: Optional[PolicyConfigurable] = None,
        tool_provider: Optional[ToolProviderInterface] = None,
        cuga_folder: Optional[str] = None,
        filesystem_sync: Optional[bool] = None,
        enable_todos: Optional[bool] = None,
        reflection_enabled: Optional[bool] = None,
        shortlisting_tool_threshold: Optional[int] = None,
        cuga_lite_max_steps: Optional[int] = None,
        enable_filesystem_tools: Optional[bool] = None,
        llm_config: Optional[dict] = None,
        special_instructions: Optional[str] = None,
        supervisor_agents: Optional[dict] = None,
        supervisor_enabled: Optional[bool] = None,
        supervisor_plan_approval: bool = False,
        supervisor_id: Optional[str] = None,
    ):
        self.final_answer_agent = FinalAnswerNode(FinalAnswerAgent.create())
        self.followup = SuggestHumanActions()
        self.followup_response = WaitForResponse()
        self.chat: Optional[ChatNode] = None
        self.interrupt_tool_node = InterruptToolNode()
        self.entry_router = EntryRouter()
        self.cuga_lite = CugaLiteNode(langfuse_handler=langfuse_handler)
        self.cuga_supervisor = CugaSupervisorNode(langfuse_handler=langfuse_handler)
        self.cuga_browser = CugaBrowserNode()
        self.browser_planner = PlannerNode(
            BrowserPlannerAgent.create(), conclude_target="CugaBrowserCallback"
        )
        self.browser_action = ActionNode(ActionAgent.create())
        self.browser_qa = QaNode(QaAgent.create())
        self.langfuse_handler = langfuse_handler
        self.policy_system = policy_system or PolicyConfigurable.get_instance()
        self.tool_provider = tool_provider
        self.cuga_folder = cuga_folder if cuga_folder is not None else settings.policy.cuga_folder
        self.filesystem_sync = (
            filesystem_sync if filesystem_sync is not None else settings.policy.filesystem_sync
        )
        self.enable_todos = enable_todos
        self.reflection_enabled = reflection_enabled
        self.shortlisting_tool_threshold = shortlisting_tool_threshold
        self.cuga_lite_max_steps = cuga_lite_max_steps
        self.enable_filesystem_tools = enable_filesystem_tools
        self.llm_config: Optional[dict] = llm_config
        self.special_instructions: Optional[str] = special_instructions
        self.supervisor_agents: Optional[dict] = supervisor_agents
        self.supervisor_enabled: Optional[bool] = supervisor_enabled
        self.supervisor_plan_approval = supervisor_plan_approval
        self.supervisor_id = supervisor_id
        self.graph = None

    def _supervisor_is_enabled(self) -> bool:
        supervisor_enabled = getattr(self, "supervisor_enabled", None)
        if supervisor_enabled is not None:
            return supervisor_enabled
        return getattr(settings.supervisor, "enabled", False)

    def _should_inject_demo_supervisor_agents(self, resolved_agents: dict) -> bool:
        return not resolved_agents and getattr(self, "supervisor_agents", None) is None

    async def build_graph(self):
        graph = StateGraph(AgentState)
        await self.add_nodes(graph)
        self.add_edges(graph)

        self.graph = graph.compile(
            checkpointer=MemorySaver(),
            interrupt_after=[
                self.browser_action.action_agent.name,
                self.interrupt_tool_node.name,
            ],
        )
        self._policy_system = self.policy_system

    def get_config_with_policy(self, base_config: dict = None) -> dict:
        config = base_config or {}
        if "configurable" not in config:
            config["configurable"] = {}
        config["configurable"]["policy_system"] = self.policy_system
        if self.special_instructions:
            config["configurable"]["special_instructions"] = self.special_instructions
        return config

    async def _build_model_and_config(self):
        llm_manager = LLMManager()
        if self.llm_config:
            try:
                model = create_llm_from_config(self.llm_config)
            except Exception as err:
                logger.warning(f"build_graph: failed to create LLM from saved config: {err}")
                llm_manager._models.clear()
                fallback_config = settings.agent.code.model.copy()
                fallback_config["streaming"] = False
                model = llm_manager.get_model(fallback_config)
                self.llm_config = None
            base = settings.agent.code.model.copy() if settings.agent.code.model else {}
            model_config = {**base, "streaming": False}
            if self.llm_config:
                model_config["platform"] = self.llm_config.get("provider") or model_config.get(
                    "platform", "openai"
                )
                model_config["model"] = self.llm_config.get("model") or model_config.get("model")
                model_config["url"] = self.llm_config.get("base_url") or model_config.get("url")
                model_config["api_key"] = (
                    self.llm_config.get("api_key")
                    if "api_key" in self.llm_config
                    else model_config.get("api_key")
                )
                model_config["temperature"] = self.llm_config.get(
                    "temperature", model_config.get("temperature", 0.1)
                )
                model_config["disable_ssl"] = self.llm_config.get(
                    "disable_ssl", model_config.get("disable_ssl", False)
                )
                for key in (
                    "auth_type",
                    "auth_header_name",
                    "max_tokens",
                    "top_p",
                    "top_k",
                    "frequency_penalty",
                    "presence_penalty",
                    "stop",
                    "extra_params",
                ):
                    if key in self.llm_config and self.llm_config[key] is not None:
                        model_config[key] = self.llm_config[key]
                model_config.setdefault("max_tokens", 16000)
        else:
            llm_manager._models.clear()
            model_config = settings.agent.code.model.copy()
            model_config["streaming"] = False
            model = llm_manager.get_model(model_config)
        return model, model_config, llm_manager

    async def add_nodes(self, graph):
        self.chat = await ChatNode.create()
        graph.add_node(self.chat.chat_agent.name, self.chat.node)
        graph.add_node(self.followup.name, self.followup.node)
        graph.add_node(self.followup_response.name, self.followup_response.node)
        graph.add_node(self.final_answer_agent.final_answer_agent.name, self.final_answer_agent.node)
        graph.add_node(self.entry_router.name, self.entry_router.node)
        graph.add_node(self.interrupt_tool_node.name, self.interrupt_tool_node.node)
        graph.add_node(self.cuga_lite.name, self.cuga_lite.node)

        base_provider = self.tool_provider or CombinedToolProvider()
        policy_storage = (
            self.policy_system.storage
            if self.policy_system is not None and hasattr(self.policy_system, "storage")
            else None
        )
        tool_provider = ensure_toolguard_provider(
            base_provider,
            policy_storage=policy_storage,
            cuga_folder=self.cuga_folder,
            enabled=settings.policy.enabled,
        )
        self.tool_provider = tool_provider
        await tool_provider.initialize()

        apps = await tool_provider.get_apps()
        apps_list = [app.name for app in apps] if apps else None
        model, model_config, llm_manager = await self._build_model_and_config()

        cuga_lite_subgraph = create_cuga_lite_graph(
            model=model,
            prompt=None,
            tool_provider=tool_provider,
            apps_list=apps_list,
            callbacks=[self.langfuse_handler] if self.langfuse_handler else None,
            model_settings=model_config,
        )
        compiled_cuga_lite_subgraph = cuga_lite_subgraph.compile()
        graph.add_node("CugaLiteSubgraph", compiled_cuga_lite_subgraph)
        graph.add_node("CugaLiteCallback", self.cuga_lite.callback_node)

        graph.add_node(self.cuga_browser.name, self.cuga_browser.node)
        graph.add_node(self.browser_planner.browser_planner_agent.name, self.browser_planner.node)
        graph.add_node(self.browser_action.action_agent.name, self.browser_action.node)
        graph.add_node(self.browser_qa.qa_agent.name, self.browser_qa.node)
        graph.add_node("CugaBrowserCallback", self.cuga_browser.callback_node)

        if self._supervisor_is_enabled():
            graph.add_node(self.cuga_supervisor.name, self.cuga_supervisor.node)
            if self.supervisor_agents is not None:
                agents = self.supervisor_agents
                supervisor_special_instructions = self.special_instructions
            else:
                agents, supervisor_special_instructions = await self._load_supervisor_agents(
                    llm_manager, model_config
                )
            supervisor_model = llm_manager.get_model(model_config.copy())
            supervisor_subgraph = create_cuga_supervisor_graph(
                supervisor_model=supervisor_model,
                agents=agents,
                special_instructions=supervisor_special_instructions,
                plan_approval=self.supervisor_plan_approval,
                supervisor_id=self.supervisor_id,
            )
            compiled_supervisor_subgraph = supervisor_subgraph.compile()
            graph.add_node("CugaSupervisorSubgraph", compiled_supervisor_subgraph)
            self.cuga_supervisor.set_subgraph(compiled_supervisor_subgraph)
            graph.add_node("CugaSupervisorCallback", self.cuga_supervisor.callback_node)
        else:

            async def _cuga_supervisor_stub(state, config=None):
                from langgraph.types import Command

                return Command(update=state.model_dump(), goto=NodeNames.CUGA_LITE)

            graph.add_node(self.cuga_supervisor.name, _cuga_supervisor_stub)

    async def _load_supervisor_agents(self, llm_manager, model_config):
        import os

        from cuga.sdk import CugaAgent
        from langchain_core.tools import tool

        supervisor_config_path = getattr(settings.supervisor, "config_path", "")
        agents = {}
        supervisor_special_instructions = None

        if supervisor_config_path:
            from cuga.supervisor_utils.supervisor_config import load_supervisor_config

            config_path = os.path.join(os.getcwd(), supervisor_config_path)
            if os.path.exists(config_path):
                try:
                    supervisor_config = await load_supervisor_config(config_path)
                    agents = supervisor_config.agents
                    supervisor_special_instructions = supervisor_config.supervisor.get("special_instructions")
                except Exception as e:
                    logger.error(f"Failed to load supervisor config: {e}", exc_info=True)

        if self._should_inject_demo_supervisor_agents(agents):

            @tool
            def get_customers() -> str:
                """Get customer data from CRM"""
                return "Customer data: C001, C002, C003"

            @tool
            def send_email(to: str, subject: str, body: str = "") -> str:
                """Send email to recipient"""
                return f"Email sent to {to} with subject: {subject}"

            @tool
            def read_file(path: str) -> str:
                """Read file content"""
                return f"Content of {path}: [file content here]"

            agents = {
                "crm_agent": CugaAgent(tools=[get_customers]),
                "email_agent": CugaAgent(tools=[send_email]),
                "filesystem_agent": CugaAgent(tools=[read_file]),
            }

        return agents, supervisor_special_instructions

    def add_edges(self, graph):
        graph.add_edge(START, self.chat.chat_agent.name)
        graph.add_edge(self.final_answer_agent.final_answer_agent.name, END)
        graph.add_edge("CugaLiteSubgraph", "CugaLiteCallback")
        graph.add_edge(
            self.browser_action.action_agent.name,
            self.browser_planner.browser_planner_agent.name,
        )
        graph.add_edge(
            self.browser_qa.qa_agent.name,
            self.browser_planner.browser_planner_agent.name,
        )
        if self._supervisor_is_enabled():
            graph.add_edge("CugaSupervisorSubgraph", "CugaSupervisorCallback")


# Backwards-compatible alias while callers migrate.
DynamicAgentGraph = CugaEntryGraph
