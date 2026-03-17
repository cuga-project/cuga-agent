"""Helper functions for E2E policy tests."""

import uuid
from typing import Dict, List, Any, Optional
from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.policy.storage import PolicyStorage
from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
from cuga.backend.cuga_graph.policy.models import Policy, ToolApproval, ToolGuide, AlwaysTrigger
from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
    create_cuga_lite_graph,
    CugaLiteState,
)
from cuga.backend.cuga_graph.graph import DynamicAgentGraph
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse
from cuga.backend.llm.models import LLMManager
from cuga.config import settings
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import ToolProviderInterface


async def setup_policy_storage(
    unique_name: Optional[str] = None,
    embedding_provider: str = "local",
    embedding_model: Optional[str] = None,
) -> PolicyStorage:
    """Create and initialize policy storage with unique database name.

    Args:
        unique_name: Optional custom name for the database, otherwise generates UUID
        embedding_provider: Embedding provider to use (default: "local" for tests)
        embedding_model: Optional model name for local embeddings

    Returns:
        Initialized PolicyStorage instance

    Raises:
        ValueError: If embedding function cannot be initialized
    """
    import time
    import os

    if unique_name is None:
        unique_name = f"e2e_test_{uuid.uuid4().hex[:8]}"

    # Add timestamp to ensure uniqueness even with same base name
    unique_name_with_timestamp = f"{unique_name}_{int(time.time() * 1000)}"

    # For tests, prefer local embeddings (more reliable, no API key needed)
    # But allow override via environment variable
    final_provider = os.getenv("POLICY_EMBEDDING_PROVIDER", embedding_provider)
    final_model = os.getenv("POLICY_EMBEDDING_MODEL", embedding_model) or "all-MiniLM-L6-v2"

    # Get correct embedding dimension for the model
    from cuga.backend.cuga_graph.policy.utils import get_embedding_dimension

    embedding_dim = get_embedding_dimension(final_provider, final_model)

    storage = PolicyStorage(
        collection_name=unique_name_with_timestamp,
        embedding_provider=final_provider,
        embedding_model=final_model,
        embedding_dim=embedding_dim,
    )
    await storage.initialize_async()

    # Verify embedding function was initialized
    if not storage._embedding_function:
        raise ValueError(
            f"Failed to initialize embedding function with provider '{final_provider}' and model '{final_model}'. "
            f"Please ensure:\n"
            f"  1. For 'local' provider: Install 'sentence-transformers' package\n"
            f"  2. For 'openai' provider: Set OPENAI_API_KEY environment variable\n"
            f"  3. For 'auto' provider: Either install 'sentence-transformers' or set OPENAI_API_KEY"
        )

    return storage


async def setup_llm_manager(model_type: str = "chat") -> Any:
    """Create and configure LLM manager with specified model type.

    Args:
        model_type: Either 'chat' or 'code' for different model configs

    Returns:
        Configured LLM model instance
    """
    llm_manager = LLMManager()

    if model_type == "chat":
        model_config = settings.agent.chat.model.copy()
    elif model_type == "code":
        model_config = settings.agent.code.model.copy()
    else:
        raise ValueError(f"Unknown model_type: {model_type}")

    model_config["streaming"] = False
    return llm_manager.get_model(model_config)


def setup_langfuse_tracing() -> Optional[Any]:
    """Setup Langfuse tracing callback handler.

    Returns:
        Langfuse callback handler if available, None otherwise
    """
    try:
        from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler
    except ImportError:
        try:
            from langfuse.callback.langchain import LangchainCallbackHandler as LangfuseCallbackHandler
        except ImportError:
            return None

    try:
        return LangfuseCallbackHandler()
    except Exception:
        return None


async def setup_policy_system(
    storage: PolicyStorage, llm: Any = None, policies: Optional[List[Policy]] = None
) -> PolicyConfigurable:
    """Create and initialize policy system with optional policies.

    Args:
        storage: Policy storage instance
        llm: Optional LLM for policy agent
        policies: Optional list of policies to add

    Returns:
        Initialized PolicyConfigurable instance
    """
    policy_system = PolicyConfigurable(storage=storage, llm=llm)
    await policy_system.initialize()

    # Add policies if provided (embeddings generated automatically)
    if policies:
        for policy in policies:
            await storage.add_policy(policy)

    return policy_system


async def setup_cuga_lite_graph(
    llm: Any,
    tool_provider: ToolProviderInterface,
    apps_list: Optional[List[str]] = None,
) -> Any:
    """Create and compile CugaLite graph.

    Args:
        llm: LLM model instance
        tool_provider: Tool provider instance
        apps_list: List of apps to enable

    Returns:
        Compiled CugaLite graph
    """
    if apps_list is None:
        apps_list = []

    graph = create_cuga_lite_graph(
        model=llm,
        tool_provider=tool_provider,
        apps_list=apps_list,
    )
    return graph.compile()


def create_initial_state(
    user_query: str,
    thread_id: str,
    pi: str = "",
    sub_task_app: str = "",
) -> CugaLiteState:
    """Create initial CugaLiteState for testing.

    Args:
        user_query: The user's query/message
        thread_id: Unique thread identifier
        pi: Personal information string
        sub_task_app: Sub-task app name

    Returns:
        CugaLiteState instance
    """
    return CugaLiteState(
        chat_messages=[HumanMessage(content=user_query)],
        pi=pi,
        thread_id=thread_id,
        sub_task_app=sub_task_app,
    )


def create_graph_config(
    thread_id: str,
    policy_system: PolicyConfigurable,
    apps_list: Optional[List[str]] = None,
    langfuse_handler: Optional[Any] = None,
) -> Dict[str, Any]:
    """Create graph configuration with policy system and optional Langfuse tracing.

    Args:
        thread_id: Thread identifier
        policy_system: Policy system instance
        apps_list: List of apps
        langfuse_handler: Optional Langfuse callback handler

    Returns:
        Graph configuration dictionary
    """
    if apps_list is None:
        apps_list = []

    callbacks = []
    if langfuse_handler:
        callbacks.append(langfuse_handler)

    return {
        "callbacks": callbacks,  # For the graph itself
        "configurable": {
            "thread_id": thread_id,
            "apps_list": apps_list,
            "policy_system": policy_system,
            "callbacks": callbacks,  # For the nodes (LLM calls)
        },
    }


async def run_graph_execution(
    graph: Any,
    initial_state: CugaLiteState,
    config: Dict[str, Any],
    langfuse_handler: Optional[Any] = None,
) -> Dict[str, Any]:
    """Execute graph and optionally print Langfuse trace URL.

    Args:
        graph: Compiled CugaLite graph
        initial_state: Initial state
        config: Graph configuration
        langfuse_handler: Optional Langfuse handler for trace URL

    Returns:
        Graph execution result
    """
    result = await graph.ainvoke(initial_state, config=config)

    # Get Langfuse trace URL if available
    if langfuse_handler and hasattr(langfuse_handler, 'get_trace_url'):
        try:
            trace_url = langfuse_handler.get_trace_url()
            if trace_url:
                print(f"\n  📊 Langfuse trace: {trace_url}")
        except Exception as e:
            print(f"  ⚠️  Error getting Langfuse trace URL: {e}")

    return result


class MinimalToolProvider(ToolProviderInterface):
    """Minimal tool provider for testing."""

    async def initialize(self):
        pass

    async def get_apps(self):
        return []

    async def get_all_tools(self):
        return []

    async def get_tools(self, app_name):
        return []


# Full Agent Graph Helpers


async def setup_full_agent_graph(
    policy_system: PolicyConfigurable,
    langfuse_handler: Optional[Any] = None,
    tool_provider: Optional[Any] = None,
) -> DynamicAgentGraph:
    """Create and build the full agent graph with policy system.

    Args:
        policy_system: Policy system instance
        langfuse_handler: Optional Langfuse callback handler
        tool_provider: Optional custom tool provider (for testing)

    Returns:
        Built DynamicAgentGraph instance
    """
    graph = DynamicAgentGraph(
        configurations={},
        langfuse_handler=langfuse_handler,
        policy_system=policy_system,
        tool_provider=tool_provider,
    )
    await graph.build_graph()
    return graph


async def add_tool_approval_policy(
    policy_system: PolicyConfigurable,
    tools: Optional[List[str]] = None,
    apps: Optional[List[str]] = None,
    name: str = "Test Tool Approval",
    description: str = "Test policy for tool approval",
) -> ToolApproval:
    """Helper to add a tool approval policy to the policy system.

    Args:
        policy_system: Policy system instance
        tools: List of tool names that require approval (default: ["*"])
        apps: List of app names that require approval (default: [])
        name: Policy name
        description: Policy description

    Returns:
        Created ToolApproval policy
    """
    policy = ToolApproval(
        id=f"tool_approval_{uuid.uuid4().hex[:8]}",
        name=name,
        description=description,
        approval_message="This tool requires your approval before execution.",
        required_tools=tools or ["*"],
        required_apps=apps or [],
        show_code_preview=True,
        auto_approve_after=None,
    )

    # Add to storage with simple embedding
    storage = policy_system.storage
    await storage.add_policy(policy)
    # Reset PolicyConfigurable to reload policies
    policy_system._initialized = False
    await policy_system.initialize()

    return policy


async def add_tool_guide_policy(
    policy_system: PolicyConfigurable,
    guide_content: str,
    target_tools: Optional[List[str]] = None,
    target_apps: Optional[List[str]] = None,
    name: str = "Test Tool Guide",
    description: str = "Test policy for tool guide",
    prepend: bool = False,
) -> ToolGuide:
    """Helper to add a tool guide policy to the policy system.

    Args:
        policy_system: Policy system instance
        guide_content: Markdown content to add to tool descriptions
        target_tools: List of tool names to enrich (default: ["*"])
        target_apps: List of app names to enrich tools for (default: [])
        name: Policy name
        description: Policy description
        prepend: Whether to prepend content instead of appending

    Returns:
        Created ToolGuide policy
    """
    policy = ToolGuide(
        id=f"tool_guide_{uuid.uuid4().hex[:8]}",
        name=name,
        description=description,
        triggers=[AlwaysTrigger()],
        target_tools=target_tools or ["*"],
        target_apps=target_apps or [],
        guide_content=guide_content,
        prepend=prepend,
    )

    # Add to storage with simple embedding
    storage = policy_system.storage
    await storage.add_policy(policy)

    # Reset PolicyConfigurable to reload policies
    policy_system._initialized = False
    await policy_system.initialize()

    return policy


def create_agent_initial_state(
    user_input: str,
    thread_id: str,
    user_id: str = "test_user",
    lite_mode: bool = True,
    url: str = "https://example.com",
) -> AgentState:
    """Create initial AgentState for full agent graph testing.

    Args:
        user_input: The user's query/message
        thread_id: Unique thread identifier
        user_id: User identifier
        lite_mode: Whether to use lite mode
        url: Initial URL for the agent state

    Returns:
        AgentState instance
    """
    return AgentState(
        user_id=user_id,
        thread_id=thread_id,
        input=user_input,
        lite_mode=lite_mode,
        url=url,
    )


async def run_graph_until_interrupt(
    graph: DynamicAgentGraph, initial_state: AgentState, thread_id: str
) -> Any:
    """Run the graph until it interrupts for human input.

    Args:
        graph: DynamicAgentGraph instance
        initial_state: Initial state
        thread_id: Thread identifier

    Returns:
        State snapshot when interrupted
    """
    config = graph.get_config_with_policy({"configurable": {"thread_id": thread_id}})

    # Stream events until interrupt
    async for event in graph.graph.astream(initial_state.model_dump(), config, stream_mode="updates"):
        if isinstance(event, tuple):
            namespace, state_dict = event
            if "__interrupt__" in state_dict or state_dict.get("__interrupt__"):
                break
        elif "__interrupt__" in event:
            break

    # Get the current state from the graph
    state_snapshot = graph.graph.get_state(config)
    return state_snapshot


async def resume_graph_with_response(
    graph: DynamicAgentGraph, thread_id: str, response: ActionResponse
) -> Any:
    """Resume the graph with a human response.

    Args:
        graph: DynamicAgentGraph instance
        thread_id: Thread identifier
        response: ActionResponse with human input

    Returns:
        Final state snapshot after resuming
    """
    config = graph.get_config_with_policy({"configurable": {"thread_id": thread_id}})

    # Update the state with the response
    current_state = graph.graph.get_state(config)
    updated_state = AgentState(**current_state.values)
    updated_state.hitl_response = response

    # Stream events until completion or next interrupt
    async for event in graph.graph.astream(None, config, stream_mode="updates"):
        if isinstance(event, tuple):
            namespace, state_dict = event
            if "__interrupt__" in state_dict or state_dict.get("__interrupt__"):
                break
        elif "__interrupt__" in event:
            break

    # Get the final state
    state_snapshot = graph.graph.get_state(config)
    return state_snapshot


async def run_full_graph_to_completion(
    graph: DynamicAgentGraph, initial_state: AgentState, thread_id: str
) -> AgentState:
    """Run the full agent graph to completion and return the final state.

    Args:
        graph: DynamicAgentGraph instance
        initial_state: Initial AgentState
        thread_id: Thread identifier

    Returns:
        Final AgentState after completion
    """
    from langchain_core.runnables import RunnableConfig

    # Use graph's method to get config with policy_system included
    # get_config_with_policy expects a dict, so we pass the configurable dict
    base_config_dict = {"configurable": {"thread_id": thread_id}}
    config_dict = graph.get_config_with_policy(base_config_dict)
    config = RunnableConfig(**config_dict)

    # Stream events until completion
    async for event in graph.graph.astream(initial_state.model_dump(), config=config):
        pass  # Just iterate through all events

    # Get final state after all events are processed
    state_snapshot = graph.graph.get_state({"configurable": {"thread_id": thread_id}})
    if state_snapshot.values:
        final_state = AgentState(**state_snapshot.values)
    else:
        final_state = initial_state

    return final_state
