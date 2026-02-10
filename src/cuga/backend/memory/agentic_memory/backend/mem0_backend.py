from json import JSONDecodeError

import json
import uuid

from cuga.backend.memory.agentic_memory.backend.base import BaseMemoryBackend
from cuga.backend.memory.agentic_memory.db.sqlite_manager import SQLiteManager
from cuga.backend.memory.agentic_memory.config import get_config
from cuga.backend.memory.agentic_memory.llm.conflict_resolution.schema import MemoryEvent
from cuga.backend.memory.agentic_memory.schema import Fact, RecordedFact, Message, Run, Namespace
from cuga.backend.memory.agentic_memory.utils.exceptions import (
    NamespaceNotFoundException,
    RunNotFoundException,
)

try:
    from mem0 import Memory
    from mem0.configs.base import MemoryConfig
    from mem0.llms.base import LLMBase
    from pymilvus import MilvusClient

    MEM0_AVAILABLE = True
except ImportError:
    MEM0_AVAILABLE = False
    Memory = None
    MemoryConfig = None
    LLMBase = None
    MilvusClient = None


class Mem0MemoryBackend(BaseMemoryBackend):
    # Cache for backend namespaces
    namespaces: dict[str, Memory] = {}

    def __init__(self, *args, **kwargs):
        if not MEM0_AVAILABLE:
            raise ImportError(
                "mem0 is not installed. Please install it with: pip install mem0ai\n"
                "Or disable memory in settings.toml: enable_memory = false"
            )
        super().__init__(*args, **kwargs)

    def ready(self) -> bool:
        return True

    def _cache_namespace(self, namespace_id: str) -> Memory:
        config = get_config(namespace_id)
        memory = Memory(config=MemoryConfig.model_validate(config))
        if namespace_id not in self.namespaces:
            self.namespaces[namespace_id] = memory
        return memory

    def _get_namespace(self, namespace_id: str) -> Memory:
        memory = self.namespaces.get(namespace_id)
        if memory is None:
            with SQLiteManager() as db_manager:
                namespace = db_manager.get_namespace(namespace_id)
            if namespace:
                memory = self._cache_namespace(namespace_id)
            else:
                raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")
        return memory

    def create_namespace(
        self,
        namespace_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
    ) -> Namespace:
        namespace_id = namespace_id or 'ns_' + str(uuid.uuid4()).replace('-', '_')
        self._cache_namespace(namespace_id)

        with SQLiteManager() as db_manager:
            return db_manager.create_namespace(namespace_id, user_id, agent_id, app_id)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        # TODO: add e.g. number of records
        # memory = self._get_namespace(namespace_id=namespace_id)

        with SQLiteManager() as db_manager:
            namespace = db_manager.get_namespace(namespace_id)
            if namespace is None:
                raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")
            return namespace

    def search_namespaces(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
        limit: int = 10,
    ) -> list[Namespace]:
        with SQLiteManager() as db_manager:
            return db_manager.search_namespaces(user_id, agent_id, app_id, limit)

    def delete_namespace(self, namespace_id: str):
        try:
            memory = self._cache_namespace(namespace_id)
            milvus: MilvusClient = memory.vector_store.client
            milvus.drop_collection(collection_name=namespace_id)
        except Exception:
            pass
        self.namespaces.pop(namespace_id, None)
        with SQLiteManager() as db_manager:
            db_manager.delete_namespace(namespace_id)

    def update_facts(
        self, namespace_id: str, facts: list[Fact], enable_conflict_resolution: bool = True
    ) -> list[MemoryEvent]:
        memory = self._get_namespace(namespace_id=namespace_id)
        updates = []
        for fact in facts:
            user_id = fact.metadata.get('user_id')
            results = memory.add(fact.content, user_id=user_id, metadata=fact.metadata, infer=False)[
                'results'
            ]
            for result in results:
                updates.append(
                    MemoryEvent(
                        id=result['id'],
                        content=result['memory'],
                        event=result['event'],
                        metadata=fact.metadata,
                    )
                )
        return updates

    def create_and_store_fact(
        self, namespace_id: str, fact: Fact, enable_conflict_resolution: bool = True
    ) -> list[MemoryEvent]:
        memory = self._get_namespace(namespace_id=namespace_id)
        user_id = fact.metadata.get('user_id')
        results = memory.add(
            fact.content, user_id=user_id, metadata=fact.metadata, infer=enable_conflict_resolution
        )['results']
        updates = []
        for result in results:
            updates.append(
                MemoryEvent(
                    id=result['id'], content=result['memory'], event=result['event'], metadata=fact.metadata
                )
            )
        return updates

    def _result_to_fact(self, result: dict) -> RecordedFact:
        return RecordedFact(
            id=result['id'],
            content=result['memory'],
            metadata=result['metadata'],
            created_at=result['created_at'],
            run_id=result.get('run_id'),
        )

    def search_for_facts(
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedFact]:
        memory = self._get_namespace(namespace_id=namespace_id)
        filters = filters or {}
        user_id = filters.get('user_id')
        agent_id = filters.get('agent_id')
        run_id = filters.get('run_id')
        if query is None:
            return [
                self._result_to_fact(result)
                for result in memory.get_all(
                    user_id=user_id, agent_id=agent_id, run_id=run_id, filters=filters, limit=limit
                )['results']
            ]
        else:
            return [
                self._result_to_fact(result)
                for result in memory.search(
                    query=query,
                    user_id=user_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    filters=filters,
                    limit=limit,
                )['results']
            ]

    def delete_fact_by_id(self, namespace_id: str, fact_id: str):
        memory = self._get_namespace(namespace_id=namespace_id)
        # noinspection PyBroadException
        try:
            memory.delete(fact_id)
        except Exception:
            pass

    async def extract_facts_from_messages_async(
        self,
        namespace_id: str,
        messages: list[Message],
        metadata: dict | None = None,
        enable_conflict_resolution: bool = True,
    ) -> list[MemoryEvent]:
        memory = self._get_namespace(namespace_id=namespace_id)
        user_id = (metadata or {}).get("user_id") or "default"
        results = memory.add(
            [m.model_dump() for m in messages], metadata=metadata, user_id=user_id, infer=True
        )['results']
        updates = []
        for result in results:
            updates.append(
                MemoryEvent(
                    id=result['id'], content=result['memory'], event=result['event'], metadata=metadata
                )
            )
        return updates

    def create_run(self, namespace_id: str, run_id: str | None = None) -> Run:
        run_id = run_id or 'run_' + str(uuid.uuid4()).replace('-', '_')
        with SQLiteManager() as db_manager:
            return db_manager.create_run(namespace_id, run_id)

    def delete_run(self, namespace_id: str, run_id: str):
        memory = self._get_namespace(namespace_id=namespace_id)
        memory.delete_all(user_id='default', run_id=run_id)
        with SQLiteManager() as db_manager:
            db_manager.delete_run(namespace_id=namespace_id, run_id=run_id)

    def add_step(self, namespace_id: str, run_id: str, step: dict, prompt: str) -> MemoryEvent:
        memory = self._get_namespace(namespace_id=namespace_id)
        llm: LLMBase = memory.llm

        messages = [
            {
                "role": "system",
                "content": prompt
                + '\n\nHere is the actual step you are working on:\n'
                + json.dumps(step, indent=4),
            }
        ]

        decode_error = None
        for attempt in range(3):
            extraction = llm.generate_response(messages)
            try:
                parsed_extraction = json.loads(extraction)
            except JSONDecodeError as e:
                decode_error = e
                continue
            else:
                break
        else:
            raise decode_error

        metadata = {**parsed_extraction, "step": step}
        added_step = memory.add(
            parsed_extraction['summary'],
            user_id='default',
            metadata=metadata,
            run_id=run_id,
            infer=False,
        )
        return MemoryEvent(
            id=added_step['results'][0]['id'],
            content=added_step['results'][0]['memory'],
            event=added_step['results'][0]['event'],
            metadata=metadata,
        )

    def get_run(self, namespace_id: str, run_id: str) -> Run:
        memory = self._get_namespace(namespace_id=namespace_id)
        steps = [
            self._result_to_fact(step)
            for step in memory.get_all(user_id='default', run_id=run_id)['results']
        ]
        sorted_steps = sorted(steps, key=lambda step: step.created_at)

        with SQLiteManager() as db_manager:
            run = db_manager.get_run(namespace_id=namespace_id, run_id=run_id)
        if run is None:
            raise RunNotFoundException(f'Run "{run_id}" not found')
        run.steps = sorted_steps
        return run

    def search_runs(self, namespace_id: str, query: str, filters: dict[str, str]) -> Run | None:
        memory = self._get_namespace(namespace_id=namespace_id)
        results = memory.search(query=query, user_id='default', filters=filters)['results']
        if len(results) > 0:
            run_id = results[0]['run_id']
            steps = [
                self._result_to_fact(step)
                for step in memory.get_all(user_id='default', run_id=run_id)['results']
            ]
            sorted_steps = sorted(steps, key=lambda step: step.created_at)

            with SQLiteManager() as db_manager:
                run = db_manager.get_run(namespace_id=namespace_id, run_id=run_id)
            run.steps = sorted_steps
            return run
        else:
            return None
