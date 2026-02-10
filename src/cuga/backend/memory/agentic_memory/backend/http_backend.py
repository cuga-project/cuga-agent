"""
V1 Memory Client for Self-Hosted Service
This client interfaces with the self-hosted Memory v1 API endpoints.
"""

from json import JSONDecodeError

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional

from cuga.backend.memory.agentic_memory.schema import Fact, Message, RecordedFact, Run, Namespace
from cuga.backend.memory.agentic_memory.llm.conflict_resolution.schema import MemoryEvent
from cuga.backend.memory.agentic_memory.utils.exceptions import (
    APIRequestException,
    NamespaceNotFoundException,
    NamespaceAlreadyExistsException,
    RunNotFoundException,
)
from cuga.backend.memory.agentic_memory.backend.base import BaseMemoryBackend


class HTTPBackendSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='AGENTIC_MEMORY_')
    base_url: str = 'http://localhost:8888'  # Base URL of the self-hosted Memory service
    api_key: str | None = None  # Optional API key for authentication
    timeout: float = 60.0  # Request timeout in seconds (default: 60.0)


class HTTPMemoryBackend(BaseMemoryBackend):
    """A backend which points to a compatible and already running backend via HTTP."""

    def __init__(self, config: HTTPBackendSettings | None = None):
        super().__init__(config)
        self.config = config or HTTPBackendSettings()

        # Prepare headers
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}

        # Add API key to headers if provided
        if self.config.api_key:
            headers['Authorization'] = f'Bearer {self.config.api_key}'

        # Initialize httpx client with headers and timeout configuration
        self._aclient = httpx.AsyncClient(
            base_url=self.config.base_url,
            headers=headers,
            timeout=httpx.Timeout(
                self.config.timeout, connect=10.0
            ),  # configurable timeout, 10s connect timeout
            transport=httpx.AsyncHTTPTransport(retries=3),
        )
        self._client = httpx.Client(
            base_url=self.config.base_url,
            headers=headers,
            timeout=httpx.Timeout(
                self.config.timeout, connect=10.0
            ),  # configurable timeout, 10s connect timeout
            transport=httpx.HTTPTransport(retries=3),
        )

    def __del__(self):
        self._client.close()

    def ready(self) -> bool:
        """
        Check if the Memory V1 service is healthy.

        Returns:
            True if service is healthy, False otherwise

        Raises:
            APIRequestException: If the request fails
        """
        response = self._client.get('/v1/health/live')
        if response.status_code == httpx.codes.OK:
            return True
        elif response.status_code == httpx.codes.SERVICE_UNAVAILABLE:
            return False
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def create_namespace(
        self,
        namespace_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
    ) -> Namespace:
        """
        Create a new namespace for facts to exist in.

        Args:
            user_id: The user that created the namespace.
            agent_id: The agent associated with the namespace.
            app_id: The application associated with the namespace.

        Returns:
            The ID of the created namespace

        Raises:
            NamespaceAlreadyExistsException: If the namespace already exists
            APIRequestException: If the request fails
        """
        json = remove_none(
            {"namespace_id": namespace_id, "user_id": user_id, "agent_id": agent_id, "app_id": app_id}
        )
        response = self._client.post('/v1/namespaces', json=json)
        if response.status_code == httpx.codes.CREATED:
            return Namespace.model_validate(response.json())
        elif response.status_code == httpx.codes.CONFLICT:
            raise NamespaceAlreadyExistsException(response.json()['detail'])
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        """
        Get details about a specific namespace.

        Args:
            namespace_id: ID of the namespace

        Returns:
            A dictionary containing details about the namespace
        """
        response = self._client.get(f"/v1/namespaces/{namespace_id}")
        if response.status_code == httpx.codes.OK:
            return Namespace.model_validate(response.json())
        elif response.status_code == httpx.codes.NOT_FOUND:
            raise NamespaceNotFoundException(response.json()['detail'])
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def search_namespaces(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
        limit: int = 10,
    ) -> list[Namespace]:
        """
        Search namespace with filters.

        Args:
            user_id: The user that created the namespace.
            agent_id: The agent associated with the namespace.
            app_id: The application associated with the namespace.
            limit: Number of results to return

        Returns:
            A list of `Namespace`s

        Raises:
            NamespaceNotFoundException: If the namespace doesn't exist
            APIRequestException: If the request fails
        """
        params = remove_none({"user_id": user_id, "agent_id": agent_id, "app_id": app_id, "limit": limit})
        response = self._client.get("/v1/namespaces", params=params)
        if response.status_code == httpx.codes.OK:
            return [Namespace.model_validate(result) for result in response.json()]
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def delete_namespace(self, namespace_id: str) -> None:
        """
        Delete a namespace that facts exist in.
        Deleting a namespace is idempotent, so deleting a nonexistent namespace will not result in an error.

        Args:
            namespace_id: The namespace ID to delete

        Raises:
            APIRequestException: If the request fails
        """
        response = self._client.delete(f'/v1/namespaces/{namespace_id}')
        if response.status_code != httpx.codes.NO_CONTENT:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def create_and_store_fact(
        self, namespace_id: str, fact: Fact, enable_conflict_resolution: bool = True
    ) -> list[MemoryEvent]:
        """
        Add a single fact to a namespace.

        Args:
            namespace_id: The namespace to add the fact to
            fact: The fact to store
            enable_conflict_resolution: If true, old facts will be reconciled with new facts.
        Returns:
            A list of MemoryEvents

        Raises:
            NamespaceNotFoundException: If the namespace doesn't exist
            APIRequestException: If the request fails
        """
        response = self._client.put(f"/v1/namespaces/{namespace_id}/facts", json=fact.model_dump())
        if response.status_code == httpx.codes.CREATED:
            return [MemoryEvent.model_validate(result) for result in response.json()]
        if response.status_code == httpx.codes.NOT_FOUND:
            raise NamespaceNotFoundException(response.json()['detail'])
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def update_facts(
        self, namespace_id: str, facts: list[Fact], enable_conflict_resolution: bool = True
    ) -> list[MemoryEvent]:
        """
        Add a single fact to a namespace.

        Args:
            namespace_id: The namespace to add the fact to
            facts: A list of new facts to add.
            enable_conflict_resolution: If true, old facts will be reconciled with new facts.

        Returns:
            A list of MemoryEvents

        Raises:
            NamespaceNotFoundException: If the namespace doesn't exist
            APIRequestException: If the request fails
        """
        json = [fact.model_dump() for fact in facts]
        response = self._client.patch(f"/v1/namespaces/{namespace_id}/facts", json=json)
        if response.status_code == httpx.codes.CREATED:
            return [MemoryEvent.model_validate(result) for result in response.json()]
        if response.status_code == httpx.codes.NOT_FOUND:
            raise NamespaceNotFoundException(response.json()['detail'])
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def search_for_facts(
        self, namespace_id: str, query: Optional[str] = None, filters: dict | None = None, limit: int = 10
    ) -> List[RecordedFact]:
        """
        Search for facts in a namespace.

        Args:
            namespace_id: The namespace to search in
            query: Optional search query. If None, returns all facts
            filters: Optional filters to apply to the fact's metadata.
            limit: Maximum number of facts to return

        Returns:
            List of recorded facts matching the query

        Raises:
            NamespaceNotFoundException: If the namespace doesn't exist
            APIRequestException: If the request fails
        """
        json = remove_none({"query": query, "filters": filters})
        params = {"limit": limit}
        response = self._client.post(f'/v1/namespaces/{namespace_id}/facts', json=json, params=params)
        if response.status_code == httpx.codes.OK:
            # Convert raw dicts to RecordedFact objects
            return [RecordedFact.model_validate(fact) for fact in response.json()]
        if response.status_code == httpx.codes.NOT_FOUND:
            raise NamespaceNotFoundException(response.json()['detail'])
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def get_all_facts(
        self, namespace_id: str, filters: dict | None = None, limit: int = 100
    ) -> List[RecordedFact]:
        """
        Get all facts from a namespace.

        Args:
            namespace_id: The namespace to get facts from
            filters: Optional filters to apply to the fact's metadata.
            limit: Maximum number of facts to return

        Returns:
            List of all recorded facts in the namespace

        Raises:
            NamespaceNotFoundException: If the namespace doesn't exist
            APIRequestException: If the request fails
        """
        return self.search_for_facts(namespace_id, query=None, filters=filters, limit=limit)

    def delete_fact_by_id(self, namespace_id: str, fact_id: str):
        """
        Delete a specific fact by its ID.
        Deleting a fact is idempotent, so deleting a nonexistent fact will not result in an error.

        Args:
            namespace_id: The namespace containing the fact
            fact_id: The ID of the fact to delete

        Raises:
            NamespaceNotFoundException: If the namespace doesn't exist
            APIRequestException: If the request fails
        """
        response = self._client.delete(f'/v1/namespaces/{namespace_id}/facts/{fact_id}')
        if response.status_code == httpx.codes.NOT_FOUND:
            raise NamespaceNotFoundException(response.json()['detail'])
        elif response.status_code == httpx.codes.NO_CONTENT:
            return
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

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

        Args:
            namespace_id: The namespace to store extracted facts in
            messages: List of messages to process
            metadata: Optional metadata for the facts

        Returns:
            Confirmation message about the number of messages processed

        Raises:
            NamespaceNotFoundException: If the namespace doesn't exist
            APIRequestException: If the request fails
        """
        # Convert Message objects to dicts if needed
        json = {"messages": [message.model_dump() for message in messages], "metadata": metadata}
        response = await self._aclient.post(f'/v1/namespaces/{namespace_id}/messages', json=json)
        if response.status_code == httpx.codes.OK:
            return [MemoryEvent.model_validate(event) for event in response.json()]
        elif response.status_code == httpx.codes.NOT_FOUND:
            raise NamespaceNotFoundException(response.json()['detail'])
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def create_run(self, namespace_id: str, run_id: str | None = None) -> Run:
        """
        Create a new agentic workflow run.

        Args:
            namespace_id: The namespace where the run will be created
            run_id: Optional ID to create the run with
        Returns:
            The new Run.

        Raises:
            NamespaceNotFoundException: If the namespace doesn't exist
            APIRequestException: If the request fails
        """
        json = remove_none({"run_id": run_id})
        response = self._client.post(f'/v1/namespaces/{namespace_id}/runs', json=json)
        if response.status_code == httpx.codes.CREATED:
            return Run.model_validate(response.json())
        elif response.status_code == httpx.codes.NOT_FOUND:
            raise NamespaceNotFoundException(response.json()['detail'])
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def get_run(self, namespace_id: str, run_id: str) -> Run:
        """
        Get an existing agentic workflow run.
        Args:
            namespace_id: The namespace containing the run
            run_id: The ID of the run to get

        Returns:
            The Run

        Raises:
            NamespaceNotFoundException: If the namespace doesn't exist
            RunNotFoundException: If the run doesn't exist
            APIRequestException: If the request fails
        """
        response = self._client.get(f"/v1/namespaces/{namespace_id}/runs/{run_id}")
        if response.status_code == httpx.codes.OK:
            return Run.model_validate(response.json())
        elif response.status_code == httpx.codes.NOT_FOUND:
            detail = response.json()['detail']
            if 'namespace' in detail.lower():
                raise NamespaceNotFoundException(detail)
            else:
                raise RunNotFoundException(detail)
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def delete_run(self, namespace_id: str, run_id: str):
        """
        Delete a specific run by its ID.

        Args:
            namespace_id: The namespace containing the run
            run_id: The ID of the run to delete

        Raises:
            NamespaceNotFoundException: If the namespace doesn't exist
            APIRequestException: If the request fails
        """
        response = self._client.delete(f'/v1/namespaces/{namespace_id}/runs/{run_id}')
        if response.status_code == httpx.codes.NOT_FOUND:
            raise NamespaceNotFoundException(response.json()['detail'])
        elif response.status_code != httpx.codes.NO_CONTENT:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def add_step(self, namespace_id: str, run_id: str, step: dict, prompt: str) -> MemoryEvent:
        """
        Save the results of a step into memory

        Args:
            namespace_id: The namespace containing the run
            run_id: The ID of the run
            step: An arbitrary dictionary that describes the step
            prompt: A prompt used by an LLM to parse the step into a consistent JSON schema.

        Returns:
            A MemoryEvent corresponding to the step that was saved.

        Raises:
            NamespaceNotFoundException: If the namespace doesn't exist
            RunNotFoundException: If the run doesn't exist
            APIRequestException: If the request fails
        """
        response = self._client.post(
            f'/v1/namespaces/{namespace_id}/runs/{run_id}/steps',
            json={
                "step": step,
                "prompt": prompt,
            },
        )
        if response.status_code == httpx.codes.OK:
            return MemoryEvent.model_validate(response.json())
        elif response.status_code == httpx.codes.NOT_FOUND:
            detail = response.json()['detail']
            if 'namespace' in detail.lower():
                raise NamespaceNotFoundException(detail)
            else:
                raise RunNotFoundException(detail)
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def list_runs(self, namespace_id: str, limit: int = 10) -> list[Run]:
        """
        List runs in a namespace.

        Args:
            namespace_id: The namespace containing runs
            limit: Number of results to return

        Returns:
            A list of Runs (without steps populated)
        """
        response = self._client.get(f"/v1/namespaces/{namespace_id}/runs", params={"limit": limit})
        if response.status_code == httpx.codes.OK:
            return [Run.model_validate(result) for result in response.json()]
        elif response.status_code == httpx.codes.NOT_FOUND:
            raise NamespaceNotFoundException(response.json()['detail'])
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    def search_runs(
        self, namespace_id: str, query: str | None = None, filters: dict[str, str] | None = None
    ) -> Run | None:
        """
        Search a namespace for a step which best matches a query.

        Args:
            namespace_id: The namespace containing the run
            query: A sentence which best describes the desired outcome of the step.
            filters: filter based on a step's metadata which is a superset of this dictionary. Values are exact match.

        Returns:
            The entire Run containing the step which best matches the query.

        Raises:
            NamespaceNotFoundException: If the namespace doesn't exist
            APIRequestException: If the request fails
        """
        response = self._client.post(
            f'/v1/namespaces/{namespace_id}/runs/search',
            json={
                "query": query,
                "filter": filters,
            },
        )
        if response.status_code == httpx.codes.OK:
            try:
                return Run.model_validate(response.json())
            except JSONDecodeError:
                return None
        elif response.status_code == httpx.codes.NOT_FOUND:
            raise NamespaceNotFoundException(response.json()['detail'])
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    async def end_run(self, namespace_id: str, run_id: str):
        """
        Declare a given run ended. This may trigger background tasks processing the data found in the run.

        Args:
            namespace_id: The namespace containing the run.
            run_id: The ID of the run.

        Raises:
            NamespaceNotFoundException: If the namespace doesn't exist
            APIRequestException: If the request fails
        """
        response = await self._aclient.post(f'/v1/namespaces/{namespace_id}/runs/{run_id}/end')
        if response.status_code == httpx.codes.OK:
            return
        elif response.status_code == httpx.codes.NOT_FOUND:
            raise NamespaceNotFoundException(response.json()['detail'])
        else:
            raise APIRequestException(f'Unexpected {response.status_code} response: {response.text}')

    async def analyze_run(self, namespace_id: str, run_id: str):
        """For the HTTP backend, this is a no-op because analysis will be performed by the service"""
        pass


def remove_none(o: dict) -> dict:
    """Remove None values from a dictionary."""
    return {k: v for k, v in o.items() if v is not None}
