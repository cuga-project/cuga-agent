import datetime
import json
import uuid

from cuga.backend.memory.agentic_memory.backend.base import BaseMemoryBackend
from cuga.backend.memory.agentic_memory.config import milvus_config
from cuga.backend.memory.agentic_memory.db.sqlite_manager import SQLiteManager
from cuga.backend.memory.agentic_memory.llm.conflict_resolution.conflict_resolution import (
    resolve_conflicts,
    MemoryEvent,
)
from cuga.backend.memory.agentic_memory.schema import fact_schema, Fact, RecordedFact, Message, Namespace, Run
from cuga.backend.memory.agentic_memory.llm.fact_extraction.fact_extraction import extract_facts_from_messages
from cuga.backend.memory.agentic_memory.utils.exceptions import (
    NamespaceNotFoundException,
    RunNotFoundException,
)
from cuga.backend.memory.agentic_memory.utils.logging import Logging
from cuga.backend.memory.agentic_memory.utils.utils import (
    get_milvus_client,
    get_embedding_model,
    clean_llm_response,
    get_chat_model,
)
from json import JSONDecodeError
from pymilvus.milvus_client.index import IndexParams
from pymilvus.exceptions import MilvusException

logger = Logging.get_logger()


class MilvusMemoryBackend(BaseMemoryBackend):
    milvus = get_milvus_client()
    embedding_model = get_embedding_model('sentence-transformers/all-MiniLM-L6-v2')
    metric_type = str(getattr(milvus_config, "metric_type", "COSINE")).upper()
    _schema_filter_fields = {'id', 'content', 'created_at', 'run_id'}

    def _build_filter_expr(self, filters: dict | None, base_conditions: list[str] | None = None) -> str:
        base_conditions = base_conditions or []
        expressions = list(base_conditions)
        for key, value in (filters or {}).items():
            if value is None:
                continue
            literal = json.dumps(value)
            if key in self._schema_filter_fields:
                expressions.append(f"{key} == {literal}")
            else:
                expressions.append(f"metadata[{json.dumps(str(key))}] == {literal}")
        return ' AND '.join(expressions)

    @staticmethod
    def _extract_vector_score(result: dict) -> float | None:
        """Extract a comparable score from a Milvus vector search result."""
        for key in ("score", "distance", "_distance", "similarity"):
            value = result.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    @classmethod
    def _sort_vector_results(cls, results: list[dict], metric_type: str = "IP") -> list[dict]:
        """Sort vector search results by relevance score with metric-aware direction."""
        if not results:
            return results

        with_scores = []
        without_scores = []
        for idx, result in enumerate(results):
            score = cls._extract_vector_score(result)
            if score is None:
                without_scores.append((idx, result))
            else:
                with_scores.append((score, idx, result))

        if not with_scores:
            return results

        metric = (metric_type or "IP").upper()
        reverse = metric != "L2"
        with_scores.sort(key=lambda item: item[0], reverse=reverse)

        sorted_results = [item[2] for item in with_scores]
        # Preserve original order for records without explicit score keys.
        sorted_results.extend(item[1] for item in sorted(without_scores, key=lambda item: item[0]))
        return sorted_results

    @staticmethod
    def _flatten_search_results(results: list) -> list:
        """Flatten MilvusClient.search nested results into a list of hits."""
        if not results:
            return []

        if isinstance(results, list) and len(results) > 0 and isinstance(results[0], list):
            return list(results[0])

        return list(results)

    @staticmethod
    def _normalize_search_hit(hit) -> dict:
        """Normalize search hit shape into parse_milvus_fact-compatible dictionary."""
        if hasattr(hit, "to_dict"):
            try:
                hit = hit.to_dict()
            except Exception:
                pass

        if not isinstance(hit, dict):
            normalized_from_attrs = {}
            for attr in ("id", "distance", "score"):
                if hasattr(hit, attr):
                    normalized_from_attrs[attr] = getattr(hit, attr)
            entity_attr = getattr(hit, "entity", None)
            if entity_attr is not None:
                if hasattr(entity_attr, "to_dict"):
                    try:
                        entity_attr = entity_attr.to_dict()
                    except Exception:
                        pass
                if isinstance(entity_attr, dict):
                    normalized_from_attrs.update(entity_attr)
            return normalized_from_attrs

        entity = hit.get('entity')
        normalized = {}
        if isinstance(entity, dict):
            normalized.update(entity)
        normalized.update(hit)
        # Remove nested entity payload after flattening.
        normalized.pop('entity', None)
        return normalized

    @staticmethod
    def _fact_dedupe_key(fact: Fact) -> tuple[str, str, str] | None:
        if fact.category and fact.key and fact.value:
            return (
                fact.category.strip().lower(),
                fact.key.strip().lower(),
                str(fact.value).strip().lower(),
            )
        return None

    @classmethod
    def _dedupe_facts_by_identity(cls, facts: list[Fact]) -> list[Fact]:
        seen: set[tuple[str, str, str]] = set()
        deduped: list[Fact] = []

        for fact in facts:
            key = cls._fact_dedupe_key(fact)
            if key is None:
                deduped.append(fact)
                continue
            if key in seen:
                logger.debug(f"[MEMORY FACTS] Dropping duplicate fact tuple={key}")
                continue
            seen.add(key)
            deduped.append(fact)
        return deduped

    def ready(self):
        _ = self.milvus.list_collections()
        return {"status": "ok"}

    def validate_namespace(self, namespace_id: str):
        if not self.milvus.has_collection(namespace_id):
            raise NamespaceNotFoundException(f"Namespace {namespace_id}' not found")

    def _ensure_embedding_index(self, namespace_id: str) -> None:
        """Ensure vector index exists for embedding search on legacy collections."""
        try:
            existing_indexes = self.milvus.list_indexes(
                collection_name=namespace_id, field_name='embedding'
            )
            if existing_indexes:
                return

            logger.warning(
                f"[MEMORY SEARCH] Missing embedding index for namespace={namespace_id}; creating AUTOINDEX ({self.metric_type})"
            )
            index_params = IndexParams()
            index_params.add_index(
                field_name='embedding',
                index_type='AUTOINDEX',
                index_name='embedding_auto_idx',
                metric_type=self.metric_type,
            )
            self.milvus.create_index(collection_name=namespace_id, index_params=index_params)
            # Search expects loaded collection in some Milvus deployments.
            self.milvus.load_collection(collection_name=namespace_id)
            logger.info(f"[MEMORY SEARCH] Created embedding index for namespace={namespace_id}")
        except Exception as e:
            logger.error(
                f"[MEMORY SEARCH] Failed to ensure embedding index for namespace={namespace_id}: {e}"
            )
            raise

    def create_namespace(
        self,
        namespace_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
    ) -> Namespace:
        """Create a new namespace for facts to exist in."""
        namespace_id = namespace_id or 'ns_' + str(uuid.uuid4()).replace('-', '_')

        if not self.milvus.has_collection(namespace_id):
            self.milvus.create_collection(collection_name=namespace_id, schema=fact_schema)
        self._ensure_embedding_index(namespace_id)

        with SQLiteManager() as db_manager:
            return db_manager.create_namespace(namespace_id, user_id, agent_id, app_id)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        self.validate_namespace(namespace_id)

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
            namespaces = []
            for namespace in db_manager.search_namespaces(user_id, agent_id, app_id, limit):
                namespace.num_entities = self.milvus.get_collection_stats(namespace.id)['row_count']
                namespaces.append(namespace)
            return namespaces

    def delete_namespace(self, namespace_id: str):
        """Delete a namespace that facts exist in."""
        self.milvus.drop_collection(collection_name=namespace_id)

        with SQLiteManager() as db_manager:
            db_manager.delete_namespace(namespace_id)

    def update_facts(
        self, namespace_id: str, facts: list[Fact], enable_conflict_resolution: bool = True
    ) -> list[MemoryEvent]:
        self.validate_namespace(namespace_id)
        now = datetime.datetime.now(datetime.UTC)
        # Use fact's metadata if provided, otherwise default to empty dict for Milvus compatibility
        facts_with_temporary_ids = []
        for i, fact in enumerate(facts):
            fact_data = fact.model_dump()
            if fact_data.get('metadata') is None:
                fact_data['metadata'] = {}
            # Ensure user-scoped storage even when caller omits user_id metadata.
            fact_data['metadata'].setdefault('user_id', 'default')
            # Preserve category/key/value through conflict resolution by embedding in metadata.
            if fact.category and 'category' not in fact_data['metadata']:
                fact_data['metadata']['category'] = fact.category
            if fact.key and 'key' not in fact_data['metadata']:
                fact_data['metadata']['key'] = fact.key
            if fact.value and 'value' not in fact_data['metadata']:
                fact_data['metadata']['value'] = fact.value
            facts_with_temporary_ids.append(
                RecordedFact(
                    **fact_data, created_at=datetime.datetime.now(datetime.UTC), id=f'Unprocessed_Fact_{i}'
                )
            )

        if enable_conflict_resolution:
            old_facts = []
            for fact in facts:
                old_facts.extend(self.search_for_facts(namespace_id=namespace_id, query=fact.content))

            updates = resolve_conflicts(old_facts, facts_with_temporary_ids)
            for update in updates:
                match update.event:
                    case 'ADD':
                        # Prepare metadata with category, key, value
                        metadata = update.metadata or {}
                        metadata.setdefault('user_id', 'default')
                        if hasattr(update, 'category') and update.category:
                            metadata['category'] = update.category
                        if hasattr(update, 'key') and update.key:
                            metadata['key'] = update.key
                        if hasattr(update, 'value') and update.value:
                            metadata['value'] = update.value

                        fact_id = str(
                            self.milvus.insert(
                                collection_name=namespace_id,
                                data={
                                    'content': update.content,
                                    'created_at': int(now.timestamp()),
                                    'embedding': self.embedding_model.encode(update.content),
                                    'metadata': metadata,
                                    'run_id': '',
                                },
                            )['ids'][0]
                        )
                        update.id = fact_id
                    case 'UPDATE':
                        # Prepare metadata with category, key, value
                        metadata = update.metadata or {}
                        metadata.setdefault('user_id', 'default')
                        if hasattr(update, 'category') and update.category:
                            metadata['category'] = update.category
                        if hasattr(update, 'key') and update.key:
                            metadata['key'] = update.key
                        if hasattr(update, 'value') and update.value:
                            metadata['value'] = update.value

                        self.milvus.upsert(
                            collection_name=namespace_id,
                            data={
                                'id': update.id,
                                'content': update.content,
                                'created_at': int(now.timestamp()),
                                'embedding': self.embedding_model.encode(update.content),
                                'metadata': metadata,
                            },
                            kwargs={"partial_update": True},
                        )
                    case 'DELETE':
                        self.delete_fact_by_id(namespace_id=namespace_id, fact_id=update.id)
                    case 'NONE':
                        pass
        else:
            updates = []
            for fact in facts:
                # Prepare metadata with category, key, value
                metadata = fact.metadata or {}
                metadata.setdefault('user_id', 'default')
                if fact.category:
                    metadata['category'] = fact.category
                if fact.key:
                    metadata['key'] = fact.key
                if fact.value:
                    metadata['value'] = fact.value

                fact_id = str(
                    self.milvus.insert(
                        collection_name=namespace_id,
                        data={
                            'content': fact.content,
                            'created_at': int(now.timestamp()),
                            'embedding': self.embedding_model.encode(fact.content),
                            'metadata': metadata,
                            'run_id': '',
                        },
                    )['ids'][0]
                )
                updates.append(
                    MemoryEvent(id=fact_id, content=fact.content, event='ADD', metadata=fact.metadata)
                )
        return updates

    def create_and_store_fact(
        self, namespace_id: str, fact: Fact, enable_conflict_resolution: bool = True
    ) -> list[MemoryEvent]:
        return self.update_facts(
            namespace_id=namespace_id, facts=[fact], enable_conflict_resolution=enable_conflict_resolution
        )

    def search_for_facts(
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedFact]:
        self.validate_namespace(namespace_id)
        filters = filters or {}

        if query is None:
            try:
                stats = self.milvus.get_collection_stats(namespace_id)
                if int(stats.get("row_count", 0)) == 0:
                    return []
            except Exception:
                # If stats call fails, continue and let query path decide.
                pass

            try:
                results = self.milvus.query(
                    collection_name=namespace_id,
                    filter=self._build_filter_expr(filters, base_conditions=['id > 0']),
                    output_fields=['id', 'content', 'created_at', 'run_id', 'metadata'],
                    limit=limit,
                )
            except MilvusException as e:
                # Milvus Lite can fail on empty/growing segments with vector raw-data assertions.
                if "HasRawData" in str(e):
                    logger.warning(
                        f"[MEMORY SEARCH] Milvus raw-data assertion for namespace={namespace_id}; returning empty results."
                    )
                    return []
                raise
        else:
            metric_type = self.metric_type
            self._ensure_embedding_index(namespace_id)
            try:
                raw_results = self.milvus.search(
                    collection_name=namespace_id,
                    anns_field='embedding',
                    data=[self.embedding_model.encode(query)],
                    filter=self._build_filter_expr(filters),
                    limit=limit,
                    output_fields=['*'],
                    search_params={"metric_type": metric_type},
                )
            except Exception as e:
                if "index not found" in str(e).lower():
                    logger.warning(
                        f"[MEMORY SEARCH] Index not found during search for namespace={namespace_id}; attempting one-time index rebuild"
                    )
                    self._ensure_embedding_index(namespace_id)
                    raw_results = self.milvus.search(
                        collection_name=namespace_id,
                        anns_field='embedding',
                        data=[self.embedding_model.encode(query)],
                        filter=self._build_filter_expr(filters),
                        limit=limit,
                        output_fields=['*'],
                        search_params={"metric_type": metric_type},
                    )
                else:
                    raise
            flat_results = self._flatten_search_results(raw_results)
            normalized_results = [self._normalize_search_hit(hit) for hit in flat_results]
            results = self._sort_vector_results(normalized_results, metric_type=metric_type)
            if results:
                top_scores = [self._extract_vector_score(item) for item in results[:5]]
                top_preview = []
                for item in results[:5]:
                    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                    category = metadata.get("category", "misc")
                    key = metadata.get("key")
                    pointer = f"{category}.{key}" if key else str(category)
                    content = str(item.get("content", ""))[:60]
                    top_preview.append(f"{pointer}:{content}")
                logger.debug(
                    f"[MEMORY SEARCH] namespace={namespace_id} metric={metric_type} top_scores={top_scores} top_preview={top_preview}"
                )
        return [parse_milvus_fact(i) for i in results]

    def delete_fact_by_id(self, namespace_id: str, fact_id: str):
        fact_id = int(fact_id)
        self.validate_namespace(namespace_id)
        self.milvus.delete(collection_name=namespace_id, ids=[fact_id])

    async def extract_facts_from_messages_async(
        self,
        namespace_id: str,
        messages: list[Message],
        metadata: dict | None = None,
        enable_conflict_resolution: bool = True,
    ) -> list[MemoryEvent]:
        """Takes a list of messages between a user and a chatbot, extracting and storing facts about the user,
        their personal preferences, upcoming plans, professional details, and other miscellaneous information.
        """
        self.validate_namespace(namespace_id)
        logger.debug(f"[BREAKPOINT] About to extract facts from {len(messages)} messages")
        extracted_facts = await extract_facts_from_messages(messages)
        logger.debug(f"[BREAKPOINT] Extracted {len(extracted_facts) if extracted_facts else 0} facts")

        # Handle empty results
        if not extracted_facts:
            return []

        normalized_metadata = dict(metadata or {})
        normalized_metadata.setdefault("user_id", "default")

        # Handle both legacy (list of strings) and new (list of Fact objects) formats
        if isinstance(extracted_facts[0], Fact):
            # New format with categorization - facts are already Fact objects
            facts = extracted_facts
            # Merge provided metadata with fact metadata
            for fact in facts:
                if fact.metadata is None:
                    fact.metadata = {}
                fact.metadata.update(normalized_metadata)
        elif isinstance(extracted_facts[0], str):
            # Legacy format - convert strings to Fact objects
            facts = [Fact(content=fact, metadata=normalized_metadata) for fact in extracted_facts]
        else:
            # Unexpected format - log and return empty
            logger.error(f"Unexpected fact format: {type(extracted_facts[0]).__name__}. Expected Fact or str.")
            return []

        facts = self._dedupe_facts_by_identity(facts)
        if not facts:
            return []

        return self.update_facts(
            namespace_id=namespace_id,
            facts=facts,
            enable_conflict_resolution=enable_conflict_resolution,
        )

    def create_run(self, namespace_id: str, run_id: str) -> Run:
        """Create a new agentic workflow run."""
        run_id = run_id or 'run_' + str(uuid.uuid4()).replace('-', '_')
        with SQLiteManager() as db_manager:
            return db_manager.create_run(namespace_id, run_id)

    def delete_run(self, namespace_id: str, run_id: str):
        self.validate_namespace(namespace_id)
        self.milvus.delete(collection_name=namespace_id, filter=f"run_id == '{run_id}'")
        with SQLiteManager() as db_manager:
            db_manager.delete_run(namespace_id=namespace_id, run_id=run_id)

    def add_step(self, namespace_id: str, run_id: str, step: dict, prompt: str) -> MemoryEvent:
        self.validate_namespace(namespace_id)
        llm = get_chat_model(milvus_config.step_processing)
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
            extraction = llm.invoke(messages).content
            try:
                parsed_extraction = json.loads(clean_llm_response(extraction))
            except JSONDecodeError as e:
                decode_error = e
                continue
            else:
                break
        else:
            raise decode_error

        metadata = {**parsed_extraction, 'run_id': run_id, 'step': step}
        added_step = self.milvus.insert(
            collection_name=namespace_id,
            data={
                'content': parsed_extraction['summary'],
                'created_at': int(datetime.datetime.now(datetime.UTC).timestamp()),
                'run_id': run_id,
                'embedding': self.embedding_model.encode(parsed_extraction['summary']),
                'metadata': {**parsed_extraction, 'step': step},
            },
        )

        return MemoryEvent(
            id=str(added_step['ids'][0]), content=parsed_extraction['summary'], event='ADD', metadata=metadata
        )

    def get_run(self, namespace_id: str, run_id: str) -> Run:
        self.validate_namespace(namespace_id)
        steps = [
            parse_milvus_fact(step)
            for step in self.milvus.query(
                collection_name=namespace_id,
                filter=f"run_id == '{run_id}'",
            )
        ]
        sorted_steps = sorted(steps, key=lambda step: step.created_at)

        with SQLiteManager() as db_manager:
            run = db_manager.get_run(namespace_id=namespace_id, run_id=run_id)
        if run is None:
            raise RunNotFoundException(f'Run `{run_id}` not found.')
        run.steps = sorted_steps
        return run

    def search_runs(self, namespace_id: str, query: str, filters: dict[str, str]) -> Run | None:
        self.validate_namespace(namespace_id)
        filters = filters or {}

        metric_type = self.metric_type
        self._ensure_embedding_index(namespace_id)
        raw_results = self.milvus.search(
            collection_name=namespace_id,
            anns_field='embedding',
            data=[self.embedding_model.encode(query)],
            filter=' AND '.join(['run_id != ""'] + [f"{k} == '{v}'" for k, v in filters.items()]),
            limit=5,
            output_fields=['*'],
            search_params={"metric_type": metric_type},
        )
        flat_results = self._flatten_search_results(raw_results)
        normalized_results = [self._normalize_search_hit(hit) for hit in flat_results]
        normalized_results = self._sort_vector_results(normalized_results, metric_type=metric_type)
        results = [parse_milvus_fact(i) for i in normalized_results]

        if len(results) > 0:
            run_id = results[0].run_id
            return self.get_run(namespace_id, run_id)
        else:
            return None


def parse_milvus_fact(fact: dict) -> RecordedFact:
    # Extract category, key, value from metadata if present
    metadata = fact.get('metadata', {})
    category = metadata.get('category')
    key = metadata.get('key')
    value = metadata.get('value')

    return RecordedFact.model_validate(
        {
            **fact,
            'id': str(fact['id']),
            'created_at': datetime.datetime.fromtimestamp(fact['created_at'], datetime.UTC),
            'category': category,
            'key': key,
            'value': value,
        }
    )
