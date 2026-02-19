"""Policy storage using Milvus for vector indexing and retrieval."""

import json
import os
import uuid
from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

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
from cuga.config import DBS_DIR


class PolicyStorage:
    """Storage and retrieval of policies using Milvus vector database."""

    # Class-level cache for embedding models (shared across all instances)
    _embedding_model_cache: Dict[str, Any] = {}

    def __init__(
        self,
        collection_name: str = "cuga_policies",
        host: str = "localhost",
        port: str = "19530",
        milvus_uri: Optional[str] = None,
        embedding_dim: Optional[int] = None,
        embedding_provider: str = "auto",
        embedding_model: Optional[str] = None,
    ):
        """
        Initialize PolicyStorage.

        Args:
            collection_name: Name of the Milvus collection
            host: Milvus server host
            port: Milvus server port
            milvus_uri: Milvus Lite URI for embedded mode (e.g., "./milvus_policies.db")
            embedding_dim: Dimension of embedding vectors (optional, will be auto-detected during initialization)
            embedding_provider: "openai", "local", or "auto" (default: auto)
            embedding_model: Optional model name for local embeddings
        """
        self.collection_name = collection_name
        self.host = host
        self.port = port

        # Resolve milvus_uri relative to DBS_DIR if not provided or if relative path
        if milvus_uri is None:
            os.makedirs(DBS_DIR, exist_ok=True)
            self.milvus_uri = os.path.join(DBS_DIR, "milvus_policies.db")
        elif not milvus_uri.startswith(("http://", "https://", "/")):
            # Relative path - resolve to DBS_DIR
            os.makedirs(DBS_DIR, exist_ok=True)
            self.milvus_uri = os.path.join(DBS_DIR, milvus_uri)
        else:
            # Absolute path or URL - use as is
            self.milvus_uri = milvus_uri

        # embedding_dim will be auto-detected during initialize_async() if None
        # Default to 1536 (OpenAI) as fallback, but will be updated based on actual model
        self.embedding_dim = embedding_dim or 1536
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model
        self.collection: Optional[Collection] = None
        self._connected = False
        self._embedding_function: Optional[Callable] = None
        self._embedding_initialized = False
        # Use unique connection alias to avoid conflicts when multiple instances exist
        self._connection_alias = f"policy_storage_{uuid.uuid4().hex[:8]}"

    def connect(self):
        """Connect to Milvus server or use Milvus Lite for local testing."""
        # If milvus_uri is a .db file, skip server connection and use Milvus Lite directly
        # This saves ~10 seconds per connection by avoiding server connection timeout
        if self.milvus_uri and self.milvus_uri.endswith('.db'):
            logger.info(f"Using Milvus Lite (embedded mode) at {self.milvus_uri}")
            try:
                connections.connect(
                    alias=self._connection_alias,
                    uri=self.milvus_uri,
                )
                self._connected = True
                logger.info(
                    f"Connected to Milvus Lite at {self.milvus_uri} (alias: {self._connection_alias})"
                )
                return
            except Exception as lite_error:
                logger.error(f"Failed to connect to Milvus Lite: {lite_error}")
                raise

        # Otherwise, try to connect to Milvus server first
        try:
            connections.connect(
                alias=self._connection_alias,
                host=self.host,
                port=self.port,
            )
            self._connected = True
            logger.info(f"Connected to Milvus at {self.host}:{self.port} (alias: {self._connection_alias})")
        except Exception as e:
            # Fall back to Milvus Lite (embedded) for testing
            logger.warning(f"Failed to connect to Milvus server: {e}")
            logger.info(f"Attempting to use Milvus Lite (embedded mode) at {self.milvus_uri}")
            try:
                connections.connect(
                    alias=self._connection_alias,
                    uri=self.milvus_uri,
                )
                self._connected = True
                logger.info(
                    f"Connected to Milvus Lite at {self.milvus_uri} (alias: {self._connection_alias})"
                )
            except Exception as lite_error:
                logger.error(f"Failed to connect to Milvus Lite: {lite_error}")
                raise

    def disconnect(self):
        """Disconnect from Milvus server."""
        if self._connected:
            connections.disconnect(alias=self._connection_alias)
            self._connected = False
            logger.info(f"Disconnected from Milvus (alias: {self._connection_alias})")

    def _create_collection(self):
        """Create the Milvus collection schema."""
        if utility.has_collection(self.collection_name, using=self._connection_alias):
            logger.info(f"Collection {self.collection_name} already exists")
            self.collection = Collection(self.collection_name, using=self._connection_alias)

            # Check if the existing collection has the correct embedding dimension
            for field in self.collection.schema.fields:
                if field.name == "embedding" and field.params.get("dim") != self.embedding_dim:
                    logger.warning(
                        f"⚠️  Collection '{self.collection_name}' has embedding dim={field.params.get('dim')}, "
                        f"but storage expects dim={self.embedding_dim}. Dropping and recreating collection."
                    )
                    utility.drop_collection(self.collection_name, using=self._connection_alias)
                    logger.info(f"Dropped collection '{self.collection_name}'")
                    break
            else:
                # Collection exists and has correct dimensions
                return

        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=256),
            FieldSchema(name="policy_type", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="name", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="description", dtype=DataType.VARCHAR, max_length=2048),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.embedding_dim),
            FieldSchema(name="policy_json", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="priority", dtype=DataType.INT64),
            FieldSchema(name="enabled", dtype=DataType.BOOL),
            FieldSchema(name="metadata_json", dtype=DataType.VARCHAR, max_length=8192),
        ]

        schema = CollectionSchema(fields=fields, description="CUGA Policy Storage")
        self.collection = Collection(name=self.collection_name, schema=schema, using=self._connection_alias)

        # Create index for vector search
        index_params = {
            "metric_type": "COSINE",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        }
        self.collection.create_index(field_name="embedding", index_params=index_params)
        logger.info(f"Created collection {self.collection_name}")

    async def _create_openai_embedding_function(self):
        """Create an OpenAI embedding function using the API key from environment."""
        try:
            from langchain_openai import OpenAIEmbeddings

            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                logger.warning("OPENAI_API_KEY not found in environment")
                return None

            embeddings = OpenAIEmbeddings(
                model="text-embedding-3-small",
                openai_api_key=api_key,
            )

            async def embed_text(text: str) -> List[float]:
                result = await embeddings.aembed_query(text)
                return result

            logger.info("✅ OpenAI embedding function created (text-embedding-3-small, 1536 dims)")
            return embed_text

        except ImportError:
            logger.warning("langchain_openai not installed, cannot create OpenAI embeddings")
            return None
        except Exception as e:
            logger.error(f"Failed to create OpenAI embedding function: {e}")
            return None

    async def _create_local_embedding_function(self, model_name: str = "all-MiniLM-L6-v2"):
        """Create a local embedding function using PyMilvus's built-in model support with caching."""
        try:
            from pymilvus import model
            import torch

            # Check if model is already cached
            cache_key = f"{model_name}"
            if cache_key in PolicyStorage._embedding_model_cache:
                logger.info(f"Using cached embedding model: {model_name}")
                embedding_fn = PolicyStorage._embedding_model_cache[cache_key]
                embedding_dim = embedding_fn.dim
            else:
                logger.info(f"Loading local embedding model: {model_name}")
                device = "cuda" if torch.cuda.is_available() else "cpu"

                # Create PyMilvus sentence transformer embedding function
                embedding_fn = model.dense.SentenceTransformerEmbeddingFunction(
                    model_name=model_name, device=device
                )

                embedding_dim = embedding_fn.dim

                # Cache the model for future use
                PolicyStorage._embedding_model_cache[cache_key] = embedding_fn

                logger.info(f"✅ Local embedding model loaded on {device}")
                logger.info(f"   Model: {model_name}")
                logger.info(f"   Dimensions: {embedding_dim}")
                logger.info("   Cached for reuse across PolicyStorage instances")

            async def embed_text(text: str) -> List[float]:
                # PyMilvus encode_queries is synchronous, so we run it in executor
                import asyncio

                loop = asyncio.get_event_loop()
                # Use encode_queries for single text (returns list of embeddings)
                embeddings = await loop.run_in_executor(None, lambda: embedding_fn.encode_queries([text]))
                return embeddings[0].tolist()

            return embed_text

        except ImportError as e:
            logger.warning(f"pymilvus[model] not installed: {e}")
            logger.warning("Install with: pip install 'pymilvus[model]'")
            return None
        except Exception as e:
            logger.error(f"Failed to create local embedding function: {e}")
            return None

    async def _create_embedding_function(self, provider: str = "auto", model_name: Optional[str] = None):
        """Create an embedding function based on provider preference."""
        if provider == "openai":
            return await self._create_openai_embedding_function()

        elif provider == "local":
            return await self._create_local_embedding_function(model_name or "all-MiniLM-L6-v2")

        elif provider == "auto":
            # Try OpenAI first
            openai_func = await self._create_openai_embedding_function()
            if openai_func:
                return openai_func

            # Fall back to local
            logger.info("OpenAI not available, trying local embedding model...")
            return await self._create_local_embedding_function(model_name or "all-MiniLM-L6-v2")

        else:
            logger.error(f"Unknown embedding provider: {provider}")
            return None

    async def _initialize_embedding_function(self):
        """Initialize the embedding function based on provider settings."""
        if self._embedding_initialized:
            return

        try:
            from cuga.backend.cuga_graph.policy.utils import get_embedding_dimension

            self._embedding_function = await self._create_embedding_function(
                provider=self.embedding_provider,
                model_name=self.embedding_model,
            )

            if self._embedding_function:
                # Always update embedding_dim based on actual model (auto-detect)
                # This ensures we use the correct dimension regardless of what was passed
                actual_dim = get_embedding_dimension(
                    "local"
                    if (self.embedding_provider == "auto" or self.embedding_provider == "local")
                    else self.embedding_provider,
                    self.embedding_model or "all-MiniLM-L6-v2",
                )
                if actual_dim != self.embedding_dim:
                    if self.embedding_dim != 1536 or actual_dim != 1536:  # Only log if not default
                        logger.info(
                            f"📏 Auto-detected embedding dimension: {actual_dim} "
                            f"(was {self.embedding_dim}, updated to match model)"
                        )
                    self.embedding_dim = actual_dim

                logger.info(
                    f"✅ Embedding function initialized (provider: {self.embedding_provider}, dim: {self.embedding_dim})"
                )
            else:
                error_msg = (
                    f"Failed to initialize embedding function with provider '{self.embedding_provider}' "
                    f"and model '{self.embedding_model or 'default'}'. "
                    f"Embeddings are required. Please ensure:\n"
                    f"  1. For 'local' provider: Install 'pymilvus[model]' package\n"
                    f"  2. For 'openai' provider: Set OPENAI_API_KEY environment variable\n"
                    f"  3. For 'auto' provider: Either install 'pymilvus[model]' or set OPENAI_API_KEY"
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
            self.connect()

        # Initialize embedding function first (needed to determine correct dimension)
        await self._initialize_embedding_function()

        # Create collection with correct dimension
        self._create_collection()

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
                f"Either:\n"
                f"  1. Set OPENAI_API_KEY environment variable, OR\n"
                f"  2. Install 'pymilvus[model]' for local embeddings"
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
        """
        Add a policy to storage.

        If no embedding is provided, one will be generated automatically using
        the configured embedding provider.

        Args:
            policy: Policy object to store
            embedding: Optional pre-computed vector embedding. If None, will be generated.
        """
        # Initialize embedding function first to get correct dimensions
        if not self._embedding_initialized:
            await self._initialize_embedding_function()

        # Now initialize storage with correct dimensions
        if not self._connected:
            await self.initialize_async()

        # Generate embedding if not provided
        if embedding is None:
            embedding = await self._generate_policy_embedding(policy)

        policy_data = self._policy_to_dict(policy)
        policy_data["embedding"] = embedding

        try:
            self.collection.insert([policy_data])
            self.collection.flush()
            logger.info(f"Added policy {policy.id} to storage")
        except Exception as e:
            logger.error(f"Failed to add policy {policy.id}: {e}")
            raise

    async def update_policy(self, policy: Policy, embedding: Optional[List[float]] = None):
        """
        Update an existing policy.

        If no embedding is provided, one will be generated automatically.

        Args:
            policy: Updated policy object
            embedding: Optional new embedding. If None, will be generated automatically.
        """
        # Delete old version
        await self.delete_policy(policy.id)
        # Insert new version (embedding will be generated if not provided)
        await self.add_policy(policy, embedding)

    async def delete_policy(self, policy_id: str):
        """
        Delete a policy from storage.

        Args:
            policy_id: ID of the policy to delete
        """
        if not self._connected:
            await self.initialize_async()

        try:
            expr = f'id == "{policy_id}"'
            self.collection.delete(expr)
            self.collection.flush()
            logger.info(f"Deleted policy {policy_id}")
        except Exception as e:
            logger.error(f"Failed to delete policy {policy_id}: {e}")
            raise

    async def get_policy(self, policy_id: str) -> Optional[Policy]:
        """
        Retrieve a policy by ID.

        Args:
            policy_id: ID of the policy to retrieve

        Returns:
            Policy object or None if not found
        """
        if not self._connected:
            await self.initialize_async()

        try:
            self.collection.load()
            expr = f'id == "{policy_id}"'
            results = self.collection.query(expr=expr, output_fields=["policy_json"])

            if results:
                return self._dict_to_policy(results[0])
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
        """
        Search for policies using vector similarity.

        Args:
            query_embedding: Query vector embedding
            limit: Maximum number of results to return
            policy_type: Filter by policy type (optional)
            enabled_only: Only return enabled policies

        Returns:
            List of (Policy, similarity_score) tuples
        """
        if not self._connected:
            await self.initialize_async()

        try:
            self.collection.load()

            # Build filter expression
            filter_expr = []
            if enabled_only:
                filter_expr.append("enabled == true")
            if policy_type:
                filter_expr.append(f'policy_type == "{policy_type.value}"')

            expr = " && ".join(filter_expr) if filter_expr else None

            search_params = {"metric_type": "COSINE", "params": {"nprobe": 10}}

            results = self.collection.search(
                data=[query_embedding],
                anns_field="embedding",
                param=search_params,
                limit=limit,
                expr=expr,
                output_fields=["policy_json", "priority"],
            )

            policies = []
            if results and len(results) > 0:
                for hit in results[0]:
                    policy = self._dict_to_policy({"policy_json": hit.entity.get("policy_json")})
                    similarity_score = hit.distance
                    policies.append((policy, similarity_score))

                # Sort by priority (descending) then by similarity (descending)
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
        """
        List all policies with optional filtering.

        Args:
            policy_type: Filter by policy type (optional)
            enabled_only: Only return enabled policies
            limit: Maximum number of results

        Returns:
            List of Policy objects
        """
        if not self._connected:
            await self.initialize_async()

        try:
            self.collection.load()

            # Build filter expression
            filter_expr = []
            if enabled_only:
                filter_expr.append("enabled == true")
            if policy_type:
                filter_expr.append(f'policy_type == "{policy_type.value}"')

            expr = " && ".join(filter_expr) if filter_expr else None

            results = self.collection.query(
                expr=expr if expr else "",
                output_fields=["policy_json", "priority"],
                limit=limit,
            )

            policies = [self._dict_to_policy(result) for result in results]
            # Sort by priority
            policies.sort(key=lambda x: x.priority, reverse=True)

            return policies
        except Exception as e:
            logger.error(f"Failed to list policies: {e}")
            return []

    async def count_policies(self, policy_type: Optional[PolicyType] = None) -> int:
        """
        Count policies in storage.

        Args:
            policy_type: Filter by policy type (optional)

        Returns:
            Number of policies
        """
        if not self._connected:
            await self.initialize_async()

        try:
            self.collection.load()

            if policy_type:
                expr = f'policy_type == "{policy_type.value}"'
                results = self.collection.query(expr=expr, output_fields=["id"])
                return len(results)
            else:
                return self.collection.num_entities
        except Exception as e:
            logger.error(f"Failed to count policies: {e}")
            return 0
