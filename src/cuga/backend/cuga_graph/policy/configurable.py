"""LangGraph configurable integration for policy system."""

import os
from typing import Any, Dict, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig, ensure_config
from loguru import logger

from cuga.backend.cuga_graph.policy.agent import PolicyAgent, PolicyContext
from cuga.backend.cuga_graph.policy.models import PolicyMatch, PolicyType
from cuga.backend.cuga_graph.policy.storage import PolicyStorage
from cuga.backend.llm.models import LLMManager
from cuga.config import settings, DBS_DIR


class PolicyConfigurable:
    """
    Configurable policy system for LangGraph integration.

    This class provides a configurable interface for policy storage and retrieval
    that can be injected into LangGraph nodes via the config parameter.

    Usage in a LangGraph node:
        async def my_node(state: AgentState, config: RunnableConfig):
            policy_system = PolicyConfigurable.from_config(config)
            context = PolicyContext(
                user_input=state.intent,
                thread_id=config.get("configurable", {}).get("thread_id"),
                available_tools=[tool.name for tool in state.tools],
                ...
            )
            match = await policy_system.match_policy(context)
            if match.matched:
                # Handle policy action
                ...
    """

    _instance: Optional["PolicyConfigurable"] = None
    _initialized: bool = False

    def __init__(
        self,
        storage: Optional[PolicyStorage] = None,
        agent: Optional[PolicyAgent] = None,
        llm: Optional[BaseChatModel] = None,
        embedding_function: Optional[Any] = None,
    ):
        """
        Initialize PolicyConfigurable.

        Args:
            storage: PolicyStorage instance (will be created if not provided)
            agent: PolicyAgent instance (will be created if not provided)
            llm: Language model for semantic matching
            embedding_function: Function to generate embeddings
        """
        self.storage = storage
        self.agent = agent
        self.llm = llm
        self.embedding_function = embedding_function

    @classmethod
    def get_instance(cls) -> "PolicyConfigurable":
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def from_config(cls, config: RunnableConfig) -> "PolicyConfigurable":
        """
        Extract or create PolicyConfigurable from LangGraph config.

        Args:
            config: LangGraph RunnableConfig

        Returns:
            PolicyConfigurable instance
        """
        config = ensure_config(config)
        configurable = config.get("configurable", {})

        # Check if policy system is provided in config
        policy_system = configurable.get("policy_system")
        if policy_system and isinstance(policy_system, cls):
            return policy_system

        # Otherwise return singleton
        return cls.get_instance()

    async def initialize(
        self,
        policy_db_path: Optional[str] = None,
        collection_name: Optional[str] = None,
        embedding_dim: Optional[int] = None,
        embedding_provider: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_base_url: Optional[str] = None,
        embedding_api_key: Optional[str] = None,
    ):
        """
        Initialize the policy system (storage, agent, etc.).

        All parameters are optional and will fall back to settings.toml [policy] section.

        Args:
            policy_db_path: Optional path for policy DB; when set, uses this instead of storage.default
            collection_name: Collection name for policies
            embedding_dim: Embedding dimension
        """
        if self._initialized:
            logger.debug("Policy system already initialized")
            return

        try:
            policy_config = getattr(settings, "policy", None)
            final_collection_name = collection_name or (
                policy_config.collection_name if policy_config else "cuga_policies"
            )

            configured_path = (policy_db_path or getattr(policy_config, "policy_db_path", None) or "").strip()
            if configured_path:
                if not os.path.isabs(configured_path):
                    os.makedirs(DBS_DIR, exist_ok=True)
                    final_policy_db_path = os.path.join(DBS_DIR, configured_path)
                else:
                    final_policy_db_path = configured_path
            else:
                final_policy_db_path = None
            from cuga.backend.storage.embedding import get_embedding_config

            emb_cfg = get_embedding_config()
            final_embedding_dim = embedding_dim or emb_cfg["dim"]
            final_embedding_provider = embedding_provider or emb_cfg["provider"]
            final_embedding_model = embedding_model or emb_cfg["model"]
            final_embedding_base_url = embedding_base_url or emb_cfg["base_url"]
            final_embedding_api_key = embedding_api_key or emb_cfg["api_key"]

            logger.info("Initializing policy system with:")
            logger.info(f"  Collection: {final_collection_name}")
            logger.info(f"  Embedding: provider={final_embedding_provider}, dim={final_embedding_dim}")

            if self.storage is None:
                backend = None
                if final_policy_db_path:
                    from cuga.backend.storage.policy import LocalPolicyStore

                    backend = LocalPolicyStore(final_policy_db_path, final_collection_name)
                self.storage = PolicyStorage(
                    collection_name=final_collection_name,
                    backend=backend,
                    embedding_dim=final_embedding_dim,
                    embedding_provider=final_embedding_provider,
                    embedding_model=final_embedding_model,
                    embedding_base_url=final_embedding_base_url,
                    embedding_api_key=final_embedding_api_key,
                )
                # Use async initialization to also initialize embedding function
                await self.storage.initialize_async()
                logger.info("PolicyStorage initialized with embedding function")
            else:
                # If storage is provided, ensure embedding function is initialized
                if not self.storage._embedding_initialized:
                    await self.storage._initialize_embedding_function()
                    logger.info("Embedding function initialized for provided storage")

            # Initialize embedding function if not already set
            if self.embedding_function is None and self.storage._embedding_function:
                self.embedding_function = self.storage._embedding_function
                logger.info(
                    f"✅ Using embedding function from storage (provider: {self.storage.embedding_provider})"
                )
            elif self.embedding_function is None:
                logger.warning("⚠️  No embedding function available - vector search will be disabled")

            # Initialize LLM if not provided
            if self.llm is None:
                try:
                    llm_manager = LLMManager()
                    model_config = settings.agent.chat.model.copy()
                    self.llm = llm_manager.get_model(model_config)
                    logger.info("LLM initialized for policy matching")
                except Exception as e:
                    logger.warning(f"Failed to initialize LLM for policy matching: {e}")
                    self.llm = None

            # Initialize agent if not provided
            if self.agent is None:
                self.agent = PolicyAgent(
                    storage=self.storage,
                    llm=self.llm,
                    embedding_function=self.embedding_function,
                )
                logger.info("PolicyAgent initialized")

            self._initialized = True
            logger.info("✅ Policy system fully initialized")

        except Exception as e:
            logger.error(f"Failed to initialize policy system: {e}")
            raise

    async def match_policy(
        self, context: PolicyContext, target: str = "intent", policy_types: Optional[List[PolicyType]] = None
    ) -> PolicyMatch:
        """
        Match a policy based on context.

        Args:
            context: PolicyContext with current state
            target: Target field to evaluate (e.g., "intent", "sub_task", "agent_response").
                    Defaults to "intent".
            policy_types: Optional list of policy types to filter by (e.g., [PolicyType.OUTPUT_FORMATTER]).

        Returns:
            PolicyMatch result
        """
        if not self._initialized:
            await self.initialize()

        return await self.agent.match_policy(context, target=target, policy_types=policy_types)

    async def match_policies_by_type(
        self, context: PolicyContext, policy_type: PolicyType
    ) -> list[PolicyMatch]:
        """
        Match all policies of a specific type.

        Args:
            context: PolicyContext with current state
            policy_type: Type of policies to match

        Returns:
            List of PolicyMatch results
        """
        if not self._initialized:
            await self.initialize()

        return await self.agent.match_policies_by_type(context, policy_type)

    def to_config_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for inclusion in LangGraph config.

        Returns:
            Dictionary with policy_system key
        """
        return {"policy_system": self}

    @staticmethod
    def create_context_from_state(
        state: Any,
        config: RunnableConfig,
        additional_context: Optional[Dict[str, Any]] = None,
    ) -> PolicyContext:
        """
        Create PolicyContext from AgentState and config.

        Args:
            state: AgentState or similar state object
            config: LangGraph RunnableConfig
            additional_context: Additional context to include

        Returns:
            PolicyContext instance
        """
        configurable = config.get("configurable", {}) if config else {}

        # Extract common fields from state
        user_input = getattr(state, "intent", None) or getattr(state, "goal", None)
        chat_messages = getattr(state, "chat_messages", None)

        # If no user_input but we have chat_messages, extract the last user message
        # Skip messages that contain execution output (for OUTPUT_FORMATTER policies)
        if not user_input and chat_messages:
            from langchain_core.messages import HumanMessage

            # Find the last human message that does NOT contain execution output
            for msg in reversed(chat_messages):
                if isinstance(msg, HumanMessage) or (hasattr(msg, 'type') and msg.type == 'human'):
                    content = msg.content if hasattr(msg, 'content') else str(msg)
                    # Skip messages that contain execution output indicators
                    if content and not any(
                        indicator in content
                        for indicator in [
                            "Execution output",
                            "Execution output preview",
                            "Error during execution",
                        ]
                    ):
                        user_input = content
                        break

        if chat_messages:
            # Convert message objects to strings
            chat_messages = [msg.content if hasattr(msg, "content") else str(msg) for msg in chat_messages]

        # Extract tools
        available_tools = None
        if hasattr(state, "tools") and state.tools:
            available_tools = [tool.name if hasattr(tool, "name") else str(tool) for tool in state.tools]

        # Extract apps
        active_apps = configurable.get("apps_list") or getattr(state, "apps", None)

        # Extract current agent/node
        current_agent = getattr(state, "current_agent", None)
        current_node = getattr(state, "current_node", None)

        # Extract sub-task
        sub_task = getattr(state, "sub_task", None) or getattr(state, "current_task", None)

        # Extract last agent response
        agent_response = None
        # Prioritize final_answer (for OutputFormatter checks)
        if hasattr(state, "final_answer") and state.final_answer:
            agent_response = state.final_answer
        elif hasattr(state, "messages") and state.messages:
            last_msg = state.messages[-1]
            agent_response = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

        # Build state data
        state_data = {}
        if hasattr(state, "model_dump"):
            state_data = state.model_dump()
        elif hasattr(state, "__dict__"):
            state_data = state.__dict__.copy()

        # Merge additional context
        if additional_context:
            state_data.update(additional_context)

        return PolicyContext(
            user_input=user_input,
            thread_id=configurable.get("thread_id"),
            chat_messages=chat_messages,
            current_agent=current_agent,
            current_node=current_node,
            available_tools=available_tools,
            active_apps=active_apps,
            state_data=state_data,
            sub_task=sub_task,
            agent_response=agent_response,
            metadata=configurable,
        )


async def check_policy_in_node(state: Any, config: RunnableConfig) -> Optional[PolicyMatch]:
    """
    Convenience function to check policies in a LangGraph node.

    Args:
        state: Current agent state
        config: LangGraph config

    Returns:
        PolicyMatch if a policy matched, None otherwise
    """
    try:
        policy_system = PolicyConfigurable.from_config(config)
        context = PolicyConfigurable.create_context_from_state(state, config)
        match = await policy_system.match_policy(context)

        if match.matched:
            logger.info(f"Policy matched: {match.policy.name} (confidence: {match.confidence:.2f})")
            return match

        return None
    except Exception as e:
        logger.error(f"Error checking policy: {e}")
        return None
