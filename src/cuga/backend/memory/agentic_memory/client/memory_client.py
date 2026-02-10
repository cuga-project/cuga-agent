"""
V1 Memory Client for Self-Hosted Service
This client interfaces with the self-hosted Memory v1 API endpoints.
"""

from cuga.backend.memory.agentic_memory.schema import Fact, Message, Namespace, RecordedFact, Run
from cuga.backend.memory.agentic_memory.utils.exceptions import NamespaceNotFoundException
from cuga.backend.memory.agentic_memory.llm.conflict_resolution.schema import MemoryEvent
from cuga.config import settings
import os
from cuga.config import SETTINGS_TOML_PATH
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class MemoryConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='AGENTIC_MEMORY_')
    provider: Literal['milvus', 'http', 'mem0']
    settings: BaseSettings | None = None


class MemoryClient:
    """Wrapper client around various memory backends."""

    def __init__(self, config: MemoryConfig | None = None):
        """Initialize the Memory client."""
        print(
            "MemoryClient init: "
            f"settings.features.memory_provider={settings.features.memory_provider}, "
            f"SETTINGS_TOML_PATH={SETTINGS_TOML_PATH}, "
            f"cwd={os.getcwd()}, "
            f"DYNACONF_FEATURES__MEMORY_PROVIDER={os.getenv('DYNACONF_FEATURES__MEMORY_PROVIDER')}, "
            f"FEATURES__MEMORY_PROVIDER={os.getenv('FEATURES__MEMORY_PROVIDER')}, "
            f"DYNACONF_ENV={os.getenv('DYNACONF_ENV')}, "
            f"ENV_FOR_DYNACONF={os.getenv('ENV_FOR_DYNACONF')}"
        )
        try:
            print(
                "MemoryClient dynaconf: "
                f"current_env={getattr(settings, 'current_env', None)}, "
                f"loaded_files={getattr(settings, '_loaded_files', None)}, "
                f"features_dict={settings.as_dict().get('FEATURES')}"
            )
        except Exception as exc:
            print(f"MemoryClient dynaconf: failed to read settings ({exc})")
        self.config = config or MemoryConfig(provider=settings.features.memory_provider)
        if self.config.provider == 'milvus':
            from cuga.backend.memory.agentic_memory.backend.milvus import MilvusMemoryBackend

            self.backend = MilvusMemoryBackend(self.config.settings)
        elif self.config.provider == 'http':
            from cuga.backend.memory.agentic_memory.backend.http_backend import HTTPMemoryBackend

            self.backend = HTTPMemoryBackend(self.config.settings)
        elif self.config.provider == 'mem0':
            from cuga.backend.memory.agentic_memory.backend.mem0_backend import Mem0MemoryBackend

            self.backend = Mem0MemoryBackend(self.config.settings)
        else:
            raise NotImplementedError(f'Memory backend not implemented for provider {self.config.provider}')

    def ready(self) -> bool:
        """Check if the Memory v1 service is healthy."""
        return self.backend.ready()

    def create_namespace(
        self,
        namespace_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
    ) -> Namespace:
        """Create a new namespace for facts to exist in."""
        return self.backend.create_namespace(namespace_id, user_id, agent_id, app_id)

    def all_namespaces(self, limit: int = 10) -> list[Namespace]:
        """Get details about a specific namespace."""
        return self.backend.search_namespaces(None, None, None, limit)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        """Get details about a specific namespace."""
        return self.backend.get_namespace_details(namespace_id)

    def search_namespaces(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
        limit: int = 10,
    ) -> list[Namespace]:
        """Search namespace with filters."""
        return self.backend.search_namespaces(user_id, agent_id, app_id, limit)

    def delete_namespace(self, namespace_id: str) -> None:
        """Delete a namespace that facts exist in."""
        self.backend.delete_namespace(namespace_id)

    def create_and_store_fact(
        self, namespace_id: str, fact: Fact, enable_conflict_resolution: bool = True
    ) -> list[MemoryEvent]:
        """Add a single fact to a namespace."""
        return self.backend.create_and_store_fact(namespace_id, fact, enable_conflict_resolution)

    def update_facts(
        self, namespace_id: str, facts: list[Fact], enable_conflict_resolution: bool = True
    ) -> list[MemoryEvent]:
        """Add multiple facts to a namespace."""
        return self.backend.update_facts(namespace_id, facts, enable_conflict_resolution)

    def search_for_facts(
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedFact]:
        """Search for facts in a namespace."""
        return self.backend.search_for_facts(namespace_id, query, filters, limit)

    def get_all_facts(
        self, namespace_id: str, filters: dict | None = None, limit: int = 100
    ) -> list[RecordedFact]:
        """Get all facts from a namespace."""
        return self.search_for_facts(namespace_id, query=None, filters=filters, limit=limit)

    def delete_fact_by_id(self, namespace_id: str, fact_id: str) -> None:
        """Delete a specific fact by its ID."""
        return self.backend.delete_fact_by_id(namespace_id, fact_id)

    async def extract_facts_from_messages_async(
        self,
        namespace_id: str,
        messages: list[Message],
        metadata: dict | None = None,
        enable_conflict_resolution: bool = True,
    ) -> list[MemoryEvent]:
        """
        Extract facts from a list of messages and store them in the namespace.
        This is a background processing operation.
        """
        return await self.backend.extract_facts_from_messages_async(
            namespace_id, messages, metadata, enable_conflict_resolution
        )

    def create_run(self, namespace_id: str, run_id: str | None = None) -> Run:
        """Create a new agentic workflow run."""
        return self.backend.create_run(namespace_id, run_id)

    def get_run(self, namespace_id: str, run_id: str) -> Run:
        """Get an existing agentic workflow run."""
        return self.backend.get_run(namespace_id, run_id)

    def delete_run(self, namespace_id: str, run_id: str):
        """Delete a specific run by its ID."""
        self.backend.delete_run(namespace_id, run_id)

    def add_step(self, namespace_id: str, run_id: str, step: dict, prompt: str) -> MemoryEvent:
        """Save the results of a step into memory"""
        return self.backend.add_step(namespace_id, run_id, step, prompt)

    def list_runs(self, namespace_id: str, limit: int = 10) -> list[Run]:
        """Retrieve the list of runs in a namespace."""
        return self.backend.list_runs(namespace_id, limit)

    def search_runs(
        self, namespace_id: str, query: str | None = None, filters: dict[str, str] | None = None
    ) -> Run | None:
        """Search a namespace for a step which best matches a query."""
        return self.backend.search_runs(namespace_id, query, filters)

    async def end_run(self, namespace_id: str, run_id: str):
        """Declare a given run ended. This may trigger background tasks processing the data found in the run."""
        await self.backend.end_run(namespace_id, run_id)

    async def analyze_run(self, namespace_id: str, run_id: str):
        """Perform any background tasks to process data found in the run."""
        await self.backend.analyze_run(namespace_id, run_id)

    # Convenience methods for common patterns
    def namespace_exists(self, namespace_id: str) -> bool:
        """Check if a namespace exists."""
        try:
            self.backend.get_namespace_details(namespace_id)
            return True
        except NamespaceNotFoundException:
            return False
