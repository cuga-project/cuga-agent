"""Policy storage using pluggable backend (SQLite+sqlite-vec local, Postgres+pgvector prod)."""

import json
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from loguru import logger

from cuga.backend.storage.embedding import create_embedding_function

from cuga.backend.cuga_graph.policy.models import (
    Policy,
    PolicyType,
    Playbook,
    IntentGuard,
    ToolGuide,
    ToolApproval,
    OutputFormatter,
    CustomPolicy,
    NaturalLanguageTrigger,
)

if TYPE_CHECKING:
    from cuga.backend.storage.policy.base import PolicyStoreBackend


class PolicyStorage:
    """Storage and retrieval of policies using pluggable backend (storage layer)."""

    def __init__(
        self,
        collection_name: str = "cuga_policies",
        backend: Optional["PolicyStoreBackend"] = None,
        embedding_dim: Optional[int] = None,
        embedding_provider: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_base_url: Optional[str] = None,
        embedding_api_key: Optional[str] = None,
    ):
        self.collection_name = collection_name
        if backend is not None:
            self._backend = backend
        else:
            from cuga.backend.storage import get_storage

            self._backend = get_storage().get_policy_store_backend(collection_name)
        self.embedding_dim = embedding_dim
        self._embedding_provider = embedding_provider
        self._embedding_model = embedding_model
        self._embedding_base_url = embedding_base_url
        self._embedding_api_key = embedding_api_key
        self._connected = False
        self._embedding_function: Optional[Callable] = None
        self._embedding_initialized = False

    async def connect(self) -> None:
        await self._backend.connect()
        self._connected = True

    async def disconnect(self) -> None:
        await self._backend.disconnect()
        self._connected = False

    async def _create_collection(self) -> None:
        await self._backend.create_schema(self.embedding_dim)

    @property
    def embedding_provider(self) -> str:
        from cuga.backend.storage.embedding import get_embedding_config

        c = get_embedding_config()
        return self._embedding_provider or c["provider"]

    async def _initialize_embedding_function(self):
        """Initialize embedding function from storage.embedding config (with optional overrides)."""
        if self._embedding_initialized:
            return

        try:
            fn, dim = await create_embedding_function(
                provider=self._embedding_provider,
                model=self._embedding_model,
                base_url=self._embedding_base_url,
                api_key=self._embedding_api_key,
                dim=self.embedding_dim,
            )

            if fn:
                self._embedding_function = fn
                self.embedding_dim = dim
                logger.info(
                    f"✅ Embedding function initialized (provider: {self.embedding_provider}, dim: {self.embedding_dim})"
                )
            else:
                error_msg = (
                    "Failed to initialize embedding function. Please ensure:\n"
                    "  1. For 'local' provider: Install 'sentence-transformers' package\n"
                    "  2. For 'openai' provider: Set OPENAI_API_KEY or storage.embedding.api_key\n"
                    "  3. For 'auto' provider: Either install 'sentence-transformers' or set OPENAI_API_KEY"
                )
                logger.error(error_msg)
                raise ValueError(error_msg)

            self._embedding_initialized = True

        except Exception as e:
            logger.error(f"Failed to initialize embedding function: {e}")
            self._embedding_function = None
            self._embedding_initialized = True

    async def initialize_async(self):
        """Initialize the storage asynchronously (includes embedding function initialization)."""
        if not self._connected:
            await self.connect()

        # Initialize embedding function first (needed to determine correct dimension)
        await self._initialize_embedding_function()

        # Create collection with correct dimension
        await self._create_collection()

    async def _generate_policy_embedding(self, policy: Policy) -> List[float]:
        """
        Generate embedding for a policy using its description and relevant content.

        This is an internal method that handles all embedding generation logic
        within the storage layer.

        Args:
            policy: Policy object to generate embedding for

        Returns:
            List of floats representing the embedding vector

        Raises:
            ValueError: If embedding function is not available
        """
        # ToolApproval policies don't need embeddings - they're matched by tool name, not semantic search
        if isinstance(policy, ToolApproval):
            logger.debug(
                f"Skipping embedding generation for ToolApproval policy '{policy.name}' (not needed for matching)"
            )
            return [0.0] * self.embedding_dim

        if not self._embedding_function:
            raise ValueError(
                f"No embedding function available for policy '{policy.name}'. "
                f"Either set OPENAI_API_KEY or install 'sentence-transformers' for local embeddings."
            )

        try:
            text_parts = [policy.description]

            # Extract natural language triggers from all policies
            nl_triggers = []
            if hasattr(policy, 'triggers'):
                for trigger in policy.triggers:
                    if isinstance(trigger, NaturalLanguageTrigger):
                        if isinstance(trigger.value, list):
                            nl_triggers.extend(trigger.value)
                        elif isinstance(trigger.value, str):
                            nl_triggers.append(trigger.value)

            if nl_triggers:
                text_parts.append(f"Natural language triggers: {', '.join(nl_triggers[:10])}")

            if isinstance(policy, Playbook):
                if policy.markdown_content:
                    text_parts.append(policy.markdown_content[:500])

            elif isinstance(policy, IntentGuard):
                # IntentGuard-specific content (response message can help with matching)
                if policy.response and policy.response.content:
                    text_parts.append(policy.response.content[:300])

            elif isinstance(policy, ToolGuide):
                if policy.guide_content:
                    text_parts.append(policy.guide_content[:300])
                if policy.target_tools and "*" not in policy.target_tools:
                    text_parts.append(f"Tools: {', '.join(policy.target_tools[:10])}")

            elif isinstance(policy, OutputFormatter):
                # OutputFormatter-specific content
                # Include triggers (keywords and natural language) in embedding for better matching
                keyword_triggers = []
                if hasattr(policy, 'triggers'):
                    for trigger in policy.triggers:
                        from cuga.backend.cuga_graph.policy.models import KeywordTrigger

                        if isinstance(trigger, KeywordTrigger):
                            if isinstance(trigger.value, list):
                                keyword_triggers.extend(trigger.value)
                            elif isinstance(trigger.value, str):
                                keyword_triggers.append(trigger.value)

                if keyword_triggers:
                    text_parts.append(f"Keywords: {', '.join(keyword_triggers[:20])}")

                text_parts.append(f"Format type: {policy.format_type}")
                if policy.format_config:
                    # Include a snippet of format_config for better semantic matching
                    # For JSON schema, include structure info; for markdown, include instructions
                    if policy.format_type == "json_schema":
                        try:
                            import json

                            schema = json.loads(policy.format_config)
                            if isinstance(schema, dict) and "properties" in schema:
                                props = list(schema["properties"].keys())[:5]
                                text_parts.append(f"JSON schema fields: {', '.join(props)}")
                        except (json.JSONDecodeError, ValueError):
                            text_parts.append(policy.format_config[:200])
                    else:
                        # Markdown instructions
                        text_parts.append(policy.format_config[:300])

            text_to_embed = " | ".join(text_parts)

            embedding = await self._embedding_function(text_to_embed)

            if isinstance(embedding, list) and len(embedding) == self.embedding_dim:
                logger.debug(f"Generated embedding for policy '{policy.name}' (dim={self.embedding_dim})")
                return embedding
            else:
                logger.error(
                    f"Invalid embedding format for policy '{policy.name}': "
                    f"type={type(embedding)}, "
                    f"len={len(embedding) if hasattr(embedding, '__len__') else 'N/A'}, "
                    f"expected_dim={self.embedding_dim}"
                )
                raise ValueError(
                    f"Invalid embedding dimension for policy '{policy.name}': "
                    f"expected {self.embedding_dim}, got {len(embedding) if isinstance(embedding, list) else 'N/A'}"
                )

        except Exception as e:
            logger.error(f"Failed to generate embedding for policy '{policy.name}': {e}")
            raise ValueError(
                f"Failed to generate embedding for policy '{policy.name}'. "
                f"Please ensure embedding function is properly configured."
            ) from e

    def _policy_to_dict(self, policy: Policy) -> Dict:
        """Convert policy to dictionary for storage."""
        policy_dict = policy.model_dump()
        return {
            "id": policy_dict["id"],
            "policy_type": policy_dict["type"],
            "name": policy_dict["name"],
            "description": policy_dict["description"],
            "policy_json": json.dumps(policy_dict),
            "priority": policy_dict.get("priority", 0),
            "enabled": policy_dict.get("enabled", True),
            "metadata_json": json.dumps(policy_dict.get("metadata", {})),
        }

    def _dict_to_policy(self, data: Dict) -> Policy:
        """Convert stored dictionary back to Policy object."""
        policy_dict = json.loads(data["policy_json"])
        policy_type = policy_dict["type"]
        policy_name = policy_dict.get("name", "unknown")

        # Enhanced debug logging for triggers
        logger.debug(f"📦 Loading policy '{policy_name}' (type: {policy_type}):")
        if "triggers" in policy_dict:
            triggers_raw = policy_dict["triggers"]
            logger.debug(f"  - Raw triggers from JSON: {triggers_raw}")
            logger.debug(
                f"  - Number of triggers: {len(triggers_raw) if isinstance(triggers_raw, list) else 'not a list'}"
            )

            for i, trigger in enumerate(triggers_raw if isinstance(triggers_raw, list) else []):
                trigger_type = trigger.get("type", "unknown")
                logger.debug(f"  - Trigger {i + 1}: type='{trigger_type}', data={trigger}")
                if trigger_type == "keyword":
                    logger.info(f"📦 Loading policy '{policy_name}' with keyword trigger:")
                    logger.info(f"   Keywords: {trigger.get('value')}")
                    logger.info(f"   Operator: {trigger.get('operator', 'NOT SET')}")
                elif trigger_type == "natural_language":
                    logger.info(f"📦 Loading policy '{policy_name}' with NL trigger:")
                    logger.info(f"   NL Trigger: {trigger.get('value')}")
                    logger.info(f"   Target: {trigger.get('target', 'intent')}")
                    logger.info(f"   Threshold: {trigger.get('threshold', 0.7)}")
        else:
            logger.debug("  - ⚠️  No 'triggers' key found in policy_dict")

        # Normalize natural_language trigger values to always be lists (for backward compatibility)
        # Note: ToolApproval and ToolGuide may have triggers in stored data but don't use them
        if "triggers" in policy_dict and isinstance(policy_dict["triggers"], list):
            for trigger in policy_dict["triggers"]:
                if trigger.get("type") == "natural_language" and "value" in trigger:
                    value = trigger["value"]
                    if not isinstance(value, list):
                        # Convert string to list for backward compatibility
                        if isinstance(value, str):
                            trigger["value"] = [value]
                            logger.debug(
                                f"  - 🔄 Normalized NL trigger value from string to list: '{value}' -> {[value]}"
                            )
                        else:
                            trigger["value"] = []
                            logger.warning(
                                f"  - ⚠️  Invalid NL trigger value type: {type(value)}, converting to empty list"
                            )

        # Remove triggers from policy_dict for policy types that don't support them
        # (ToolApproval doesn't have triggers - it's checked after code generation)
        if policy_type == PolicyType.TOOL_APPROVAL and "triggers" in policy_dict:
            logger.debug("  - Removing 'triggers' field from ToolApproval policy (not supported)")
            policy_dict.pop("triggers", None)

        # Reconstruct policy object
        try:
            if policy_type == PolicyType.PLAYBOOK:
                policy = Playbook(**policy_dict)
            elif policy_type == PolicyType.INTENT_GUARD:
                policy = IntentGuard(**policy_dict)
            elif policy_type == PolicyType.TOOL_GUIDE:
                policy = ToolGuide(**policy_dict)
            elif policy_type == PolicyType.TOOL_APPROVAL:
                policy = ToolApproval(**policy_dict)
            elif policy_type == PolicyType.OUTPUT_FORMATTER:
                policy = OutputFormatter(**policy_dict)
            elif policy_type == PolicyType.CUSTOM:
                policy = CustomPolicy(**policy_dict)
            else:
                raise ValueError(f"Unknown policy type: {policy_type}")

            # Verify triggers were deserialized correctly (only for policies that have triggers)
            logger.debug(f"  - Policy reconstructed: {policy.name}")
            if hasattr(policy, 'triggers'):
                logger.debug(f"  - Triggers after deserialization: {len(policy.triggers)} trigger(s)")
                for i, trigger in enumerate(policy.triggers):
                    trigger_type_name = type(trigger).__name__
                    logger.debug(
                        f"    - Trigger {i + 1}: {trigger_type_name}, value={getattr(trigger, 'value', 'N/A')}"
                    )
            else:
                logger.debug(f"  - Policy type '{policy_type}' does not have triggers attribute")

            return policy
        except Exception as e:
            logger.error(f"❌ Failed to reconstruct policy '{policy_name}': {e}")
            logger.debug(f"  - Policy dict keys: {list(policy_dict.keys())}")
            logger.debug(f"  - Policy dict: {policy_dict}")
            raise

    async def add_policy(self, policy: Policy, embedding: Optional[List[float]] = None):
        if not self._embedding_initialized:
            await self._initialize_embedding_function()
        if not self._connected:
            await self.initialize_async()

        if embedding is None:
            embedding = await self._generate_policy_embedding(policy)

        policy_data = self._policy_to_dict(policy)
        policy_data["embedding"] = embedding

        try:
            await self._backend.add_policy(policy_data)
            logger.info(f"Added policy {policy.id} to storage")
        except Exception as e:
            logger.error(f"Failed to add policy {policy.id}: {e}")
            raise

    async def update_policy(self, policy: Policy, embedding: Optional[List[float]] = None):
        await self.delete_policy(policy.id)
        await self.add_policy(policy, embedding)

    async def delete_policy(self, policy_id: str):
        if not self._connected:
            await self.initialize_async()
        try:
            await self._backend.delete_policy(policy_id)
            logger.info(f"Deleted policy {policy_id}")
        except Exception as e:
            logger.error(f"Failed to delete policy {policy_id}: {e}")
            raise

    async def get_policy(self, policy_id: str) -> Optional[Policy]:
        if not self._connected:
            await self.initialize_async()
        try:
            data = await self._backend.get_policy(policy_id)
            if data:
                return self._dict_to_policy(data)
            return None
        except Exception as e:
            logger.error(f"Failed to get policy {policy_id}: {e}")
            return None

    async def search_policies(
        self,
        query_embedding: List[float],
        limit: int = 5,
        policy_type: Optional[PolicyType] = None,
        enabled_only: bool = True,
    ) -> List[tuple[Policy, float]]:
        if not self._connected:
            await self.initialize_async()
        try:
            rows = await self._backend.search_policies(query_embedding, limit, policy_type, enabled_only)
            policies = []
            for row in rows:
                _, policy_json, distance = row[0], row[1], row[2]
                policy = self._dict_to_policy({"policy_json": policy_json})
                similarity_score = 1.0 - float(distance)
                policies.append((policy, similarity_score))
            policies.sort(key=lambda x: (x[0].priority, x[1]), reverse=True)
            return policies
        except Exception as e:
            logger.error(f"Failed to search policies: {e}")
            return []

    async def list_policies(
        self,
        policy_type: Optional[PolicyType] = None,
        enabled_only: bool = True,
        limit: int = 100,
    ) -> List[Policy]:
        if not self._connected:
            await self.initialize_async()
        try:
            rows = await self._backend.list_policies(policy_type, enabled_only, limit)
            policies = [self._dict_to_policy(r) for r in rows]
            policies.sort(key=lambda x: x.priority, reverse=True)
            return policies
        except Exception as e:
            logger.error(f"Failed to list policies: {e}")
            return []

    async def count_policies(self, policy_type: Optional[PolicyType] = None) -> int:
        if not self._connected:
            await self.initialize_async()
        try:
            return await self._backend.count_policies(policy_type)
        except Exception as e:
            logger.error(f"Failed to count policies: {e}")
            return 0
