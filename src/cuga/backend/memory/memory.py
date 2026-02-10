from cuga.backend.memory.agentic_memory import MemoryClient, Fact, Run, RecordedFact, Namespace, MemoryEvent
from typing import List, Dict, Optional, TYPE_CHECKING, Any
import json
from cuga.config import settings


if TYPE_CHECKING:
    from cuga.backend.cuga_graph.state.agent_state import AgentState


class Memory:
    _instance = None
    _initialized = False

    def __new__(cls, memory_config=None):
        if cls._instance is None:
            cls._instance = super(Memory, cls).__new__(cls)
        return cls._instance

    def __init__(self, memory_config=None):
        if not self._initialized:
            # Check if memory is enabled before initializing
            if not settings.advanced_features.enable_memory and not settings.advanced_features.enable_fact:
                raise RuntimeError(
                    "Memory is disabled in settings. Set enable_memory = true in settings.toml to use memory features."
                )
            self.memory_client = MemoryClient(config=None)
            self.user_id = None
            Memory._initialized = True

    def health_check(self) -> bool:
        return self.memory_client.ready()

    def create_namespace(
        self,
        namespace_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
    ) -> Namespace:
        """Create a new namespace for facts to exist in."""
        return self.memory_client.create_namespace(
            namespace_id=namespace_id, user_id=user_id, agent_id=agent_id, app_id=app_id
        )

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        """Get details about a specific namespace."""
        return self.memory_client.get_namespace_details(namespace_id=namespace_id)

    def search_namespaces(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
        limit: int = 10,
    ) -> list[Namespace]:
        """Search namespace with filters."""
        return self.memory_client.search_namespaces(
            user_id=user_id, agent_id=agent_id, app_id=app_id, limit=limit
        )

    def delete_namespace(self, namespace_id: str):
        """Delete a namespace."""
        self.memory_client.delete_namespace(namespace_id=namespace_id)

    def create_and_store_fact(
        self,
        namespace_id: str,
        content: str,
        metadata: Optional[Dict] = None,
        enable_conflict_resolution: bool = True,
    ) -> list[MemoryEvent]:
        """Add a single fact to a namespace."""
        return self.memory_client.create_and_store_fact(
            namespace_id=namespace_id,
            fact=Fact(content=content, metadata=metadata),
            enable_conflict_resolution=enable_conflict_resolution,
        )

    def search_for_facts(
        self, namespace_id: str, query: Optional[str] = None, filters: dict | None = None, limit: int = 10
    ) -> List[RecordedFact]:
        """Search for facts in a namespace."""
        return self.memory_client.search_for_facts(
            namespace_id=namespace_id, query=query, filters=filters, limit=limit
        )

    def get_all_facts(self, namespace_id: str, limit: int = 100) -> List[RecordedFact]:
        return self.memory_client.get_all_facts(namespace_id=namespace_id, limit=limit)

    def get_matching_tips(
        self,
        namespace_id: str,
        agent_id: str,
        query: str,
        limit: int = 3,
    ) -> list[str]:
        """Get matching facts and return them as JSON string.

        This provides backward compatibility with the old get_matching_facts function
        while using the new V1MemoryClient internally.
        """
        recorded_facts = self.search_for_facts(
            namespace_id=namespace_id, query=query, limit=limit, filters={"agent": agent_id, "user_id": "100"}
        )

        # Extract facts from the response (assuming similar structure to old implementation)
        facts = [fact.content for fact in recorded_facts]

        # Print debug info (maintaining original behavior)
        print(query)
        print("------ICLs--------")
        for f in facts:
            print(f)

        return facts

    def create_run(self, namespace_id: str, run_id: str | None = None) -> Run:
        """Create a new run to track Agent steps."""
        return self.memory_client.create_run(namespace_id, run_id)

    def get_run(self, namespace_id: str, run_id: str) -> Run:
        """Get an existing run."""
        return self.memory_client.get_run(namespace_id, run_id)

    def delete_run(self, namespace_id: str, run_id: str):
        """Delete an existing run."""
        return self.memory_client.delete_run(namespace_id, run_id)

    def search_runs(
        self, namespace_id: str, query: str | None = None, filters: dict[str, str] | None = None
    ) -> Run | None:
        """Search a namespace for a run based on it's step which best matches a query."""
        return self.memory_client.search_runs(namespace_id, query, filters)

    async def end_run(self, namespace_id: str, run_id: str):
        """End an existing run."""
        return await self.memory_client.end_run(namespace_id, run_id)

    def add_step(self, namespace_id: str, run_id: str, step: dict, prompt: str) -> MemoryEvent:
        """Add a new step into a run."""
        return self.memory_client.add_step(namespace_id, run_id, step, prompt)

    def list_runs(self, namespace_id: str, limit: int = 10) -> list[Run]:
        """Retrieve the list of runs in a namespace."""
        return self.memory_client.list_runs(namespace_id, limit)

    # ========== User Preference Methods ==========

    async def store_user_message_for_preferences(
        self,
        namespace_id: str,
        message: str,
        user_id: str,
    ) -> List[MemoryEvent]:
        """Store user message and extract facts with categorization.
        
        This method now uses categorization-aware extraction instead of
        raw fact storage. Facts are categorized automatically by the LLM.

        Args:
            namespace_id: The namespace to store facts in
            message: The raw user message
            user_id: The user ID

        Returns:
            List of memory events from storing facts
        """
        from cuga.backend.memory.agentic_memory.schema import Message
        
        messages = [Message(role="user", content=message)]
        return await self.memory_client.extract_facts_from_messages_async(
            namespace_id=namespace_id,
            messages=messages,
            metadata={"user_id": user_id}
        )

    def get_user_preferences(
        self,
        namespace_id: str,
        user_id: str,
        query: Optional[str] = None,
        limit: int = 20
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Retrieve relevant facts using semantic search, organized by category.
        
        Args:
            namespace_id: The namespace to search
            user_id: The user ID
            query: Semantic search query (user's current utterance)
            limit: Maximum number of facts to retrieve
            
        Returns:
            Dictionary with categories as keys, lists of facts as values
            Example: {
                "personal_details": [{"content": "Name is Alice", "key": "name", "value": "Alice"}],
                "food": [{"content": "Likes pizza", "key": "food_preference", "value": "pizza"}]
            }
        """
        filters = {"user_id": user_id}
        
        # Semantic search to get relevant facts
        facts = self.search_for_facts(
            namespace_id=namespace_id,
            query=query,  # Use user's utterance for semantic matching
            filters=filters,
            limit=limit
        )
        
        # Organize facts by category
        categorized_preferences = {}
        for fact in facts:
            if fact.content:
                category = fact.category or "misc"
                
                if category not in categorized_preferences:
                    categorized_preferences[category] = []
                
                categorized_preferences[category].append({
                    "id": fact.id,
                    "content": fact.content,
                    "key": getattr(fact, 'key', None),
                    "value": getattr(fact, 'value', None),
                })
        
        return categorized_preferences

    def update_preference(
        self, namespace_id: str, user_id: str, category: str, key: str, value: Any
    ) -> List[MemoryEvent]:
        """Update a specific user preference.

        Args:
            namespace_id: The namespace
            user_id: The user ID
            category: Preference category
            key: Preference key
            value: New value

        Returns:
            Memory events from the update
        """
        from datetime import datetime

        # Delete existing preference with same key
        existing = self.search_for_facts(
            namespace_id=namespace_id,
            filters={"type": "user_preference", "user_id": user_id, "category": category, "key": key},
            limit=1,
        )

        for fact in existing:
            self.memory_client.delete_fact_by_id(namespace_id, fact.id)

        # Create new preference
        content = f"User's {key.replace('_', ' ')} ({category.replace('_', ' ')}) is {value}"
        metadata = {
            "type": "user_preference",
            "category": category,
            "key": key,
            "value": value,
            "user_id": user_id,
            "confidence": 1.0,
            "source": "explicit",
            "last_updated": datetime.utcnow().isoformat(),
        }

        return self.create_and_store_fact(
            namespace_id=namespace_id, content=content, metadata=metadata, enable_conflict_resolution=False
        )

    def delete_preference(self, namespace_id: str, user_id: str, category: str, key: str) -> None:
        """Delete a specific user preference.

        Args:
            namespace_id: The namespace
            user_id: The user ID
            category: Preference category
            key: Preference key
        """
        facts = self.search_for_facts(
            namespace_id=namespace_id,
            filters={"type": "user_preference", "user_id": user_id, "category": category, "key": key},
            limit=10,
        )

        for fact in facts:
            self.memory_client.delete_fact_by_id(namespace_id, fact.id)

    def _get_user_id(self, state: "AgentState") -> str:
        """Extract or generate user ID for memory scoping"""
        # Use the pi field from AgentState
        if hasattr(state, 'pi') and state.pi:
            pi_dict = json.loads(state.pi)
            state.user_id = str(f"{pi_dict["first_name"]}_{pi_dict["last_name"]}_{pi_dict["phone_number"]}")
        else:
            state.user_id = "default"
        self.user_id = state.user_id
        return state.user_id
