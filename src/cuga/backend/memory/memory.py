import json
import os
import uuid
from datetime import datetime, UTC
from typing import Any, TYPE_CHECKING

from kaizen.config.llm import llm_settings
from kaizen.frontend.client.kaizen_client import KaizenClient
from kaizen.llm.fact_extraction.fact_extraction import ExtractedFact, extract_facts_from_messages
from kaizen.llm.tips.tips import generate_tips
from kaizen.schema.conflict_resolution import EntityUpdate
from kaizen.schema.core import Entity, Namespace, RecordedEntity
from pydantic import BaseModel, Field

from cuga.config import DBS_DIR, settings

if TYPE_CHECKING:
    from cuga.backend.cuga_graph.state.agent_state import AgentState


class RunRecord(BaseModel):
    id: str = Field(description="Run identifier")
    created_at: datetime = Field(description="Run creation timestamp")
    ended: bool = Field(default=False)
    steps: list[RecordedEntity] = Field(default_factory=list)


class Memory:
    _instance = None
    _initialized = False

    def __new__(cls, memory_config=None):
        if cls._instance is None:
            cls._instance = super(Memory, cls).__new__(cls)
        return cls._instance

    @staticmethod
    def _map_platform_to_kaizen_provider(platform_value: Any) -> str | None:
        if not platform_value:
            return None
        mapping = {
            "openai": "openai",
            "azure": "azure",
            "openrouter": "openrouter",
            "groq": "groq",
            "watsonx": "watsonx",
        }
        return mapping.get(str(platform_value).lower())

    @staticmethod
    def _ensure_kaizen_defaults() -> None:
        os.makedirs(DBS_DIR, exist_ok=True)
        os.environ.setdefault("KAIZEN_BACKEND", "milvus")
        os.environ.setdefault("KAIZEN_URI", os.path.join(DBS_DIR, "entities.milvus.db"))
        os.environ.setdefault("KAIZEN_SQLITE_URI", os.path.join(DBS_DIR, "entities.sqlite.db"))

        try:
            fact_cfg = settings.memory.kaizen.fact_extraction.model
            tips_cfg = settings.memory.kaizen.tips.model
            conflict_cfg = settings.memory.kaizen.conflict_resolution.model
        except Exception as exc:
            raise RuntimeError(
                "Missing required Kaizen memory model config. "
                "Define [memory.kaizen.fact_extraction.model], "
                "[memory.kaizen.tips.model], and "
                "[memory.kaizen.conflict_resolution.model] in your settings file."
            ) from exc

        fact_model = getattr(fact_cfg, "model_name", None)
        tips_model = getattr(tips_cfg, "model_name", None)
        conflict_model = getattr(conflict_cfg, "model_name", None)
        provider_platform = getattr(fact_cfg, "platform", None)

        if not fact_model or not tips_model or not conflict_model:
            raise RuntimeError(
                "Kaizen memory model_name is required for fact_extraction, tips, and conflict_resolution."
            )
        if not provider_platform:
            raise RuntimeError("Kaizen memory platform is required at [memory.kaizen.fact_extraction.model].")

        os.environ.setdefault("KAIZEN_FACT_EXTRACTION_MODEL", str(fact_model))
        os.environ.setdefault("KAIZEN_TIPS_MODEL", str(tips_model))
        os.environ.setdefault("KAIZEN_CONFLICT_RESOLUTION_MODEL", str(conflict_model))

        custom_provider = Memory._map_platform_to_kaizen_provider(provider_platform)
        if custom_provider:
            os.environ.setdefault("KAIZEN_CUSTOM_LLM_PROVIDER", custom_provider)
        else:
            raise RuntimeError(
                f"Unsupported Kaizen memory platform `{provider_platform}`. "
                "Supported: openai, azure, openrouter, groq, watsonx."
            )

        os.environ.setdefault("KAIZEN_CATEGORIZATION_MODE", str(settings.memory.categorization_mode))
        os.environ.setdefault(
            "KAIZEN_ALLOW_DYNAMIC_CATEGORIES",
            str(bool(settings.memory.allow_dynamic_categories)).lower(),
        )
        os.environ.setdefault(
            "KAIZEN_CONFIRM_NEW_CATEGORIES",
            str(bool(settings.memory.confirm_new_categories)).lower(),
        )

        # kaizen.config.llm.llm_settings is instantiated at import time.
        # Reload it after injecting defaults so runtime uses the expected model.
        llm_settings.__init__()

    def __init__(self, memory_config=None):
        if not self._initialized:
            if not settings.advanced_features.enable_memory and not settings.advanced_features.enable_fact:
                raise RuntimeError(
                    "Memory is disabled in settings. Set enable_memory = true in settings.toml to use memory features."
                )
            self._ensure_kaizen_defaults()
            self.client = KaizenClient()
            # Backward-compat internal alias used by a few async call sites.
            self.memory_client = self
            self.user_id: str | None = None
            Memory._initialized = True

    def health_check(self) -> bool:
        return self.client.ready()

    def ready(self) -> bool:
        return self.health_check()

    def create_namespace(
        self,
        namespace_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
    ) -> Namespace:
        _ = (user_id, agent_id, app_id)
        return self.client.create_namespace(namespace_id)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        return self.client.get_namespace_details(namespace_id=namespace_id)

    def search_namespaces(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
        limit: int = 10,
    ) -> list[Namespace]:
        _ = (user_id, agent_id, app_id)
        return self.client.search_namespaces(limit=limit)

    def delete_namespace(self, namespace_id: str):
        self.client.delete_namespace(namespace_id=namespace_id)

    def namespace_exists(self, namespace_id: str) -> bool:
        return self.client.namespace_exists(namespace_id)

    def update_entities(
        self,
        namespace_id: str,
        entities: list[Entity],
        enable_conflict_resolution: bool = True,
    ) -> list[EntityUpdate]:
        return self.client.update_entities(namespace_id, entities, enable_conflict_resolution=enable_conflict_resolution)

    def search_entities(
        self,
        namespace_id: str,
        query: str | None = None,
        filters: dict | None = None,
        limit: int = 10,
    ) -> list[RecordedEntity]:
        return self.client.search_entities(namespace_id=namespace_id, query=query, filters=filters, limit=limit)

    def get_all_entities(
        self,
        namespace_id: str,
        filters: dict | None = None,
        limit: int = 100,
    ) -> list[RecordedEntity]:
        return self.client.get_all_entities(namespace_id=namespace_id, filters=filters, limit=limit)

    def delete_entity_by_id(self, namespace_id: str, entity_id: str) -> None:
        self.client.delete_entity_by_id(namespace_id=namespace_id, entity_id=entity_id)

    # -------- Legacy-style wrappers used by CUGA call sites --------

    def create_and_store_fact(
        self,
        namespace_id: str,
        content: str,
        metadata: dict | None = None,
        enable_conflict_resolution: bool = True,
    ) -> list[EntityUpdate]:
        fact_metadata = dict(metadata or {})
        return self.update_entities(
            namespace_id=namespace_id,
            entities=[Entity(type="fact", content=content, metadata=fact_metadata)],
            enable_conflict_resolution=enable_conflict_resolution,
        )

    def search_for_facts(
        self,
        namespace_id: str,
        query: str | None = None,
        filters: dict | None = None,
        limit: int = 10,
    ) -> list[RecordedEntity]:
        merged_filters = {"__entity_type": "fact"}
        merged_filters.update(filters or {})
        return self.search_entities(namespace_id=namespace_id, query=query, filters=merged_filters, limit=limit)

    def get_all_facts(self, namespace_id: str, limit: int = 100) -> list[RecordedEntity]:
        return self.search_for_facts(namespace_id=namespace_id, query=None, filters=None, limit=limit)

    async def extract_facts_from_messages_async(
        self,
        namespace_id: str,
        messages: list[dict[str, str]],
        metadata: dict | None = None,
        enable_conflict_resolution: bool = True,
    ) -> list[EntityUpdate]:
        extracted = extract_facts_from_messages(messages)
        base_metadata = dict(metadata or {})
        entities: list[Entity] = []
        for one in extracted:
            if isinstance(one, ExtractedFact):
                md = dict(base_metadata)
                md["category"] = one.category
                md["key"] = one.key
                md["value"] = one.value
                entities.append(Entity(type="fact", content=one.content, metadata=md))
            else:
                entities.append(Entity(type="fact", content=str(one), metadata=base_metadata))
        if not entities:
            return []
        return self.update_entities(
            namespace_id=namespace_id,
            entities=entities,
            enable_conflict_resolution=enable_conflict_resolution,
        )

    def create_run(self, namespace_id: str, run_id: str | None = None) -> RunRecord:
        run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        self.update_entities(
            namespace_id=namespace_id,
            entities=[Entity(type="run", content=f"Run {run_id}", metadata={"run_id": run_id, "ended": False})],
            enable_conflict_resolution=False,
        )
        return self.get_run(namespace_id, run_id)

    def _search_run_entities(self, namespace_id: str, run_id: str, limit: int = 10) -> list[RecordedEntity]:
        return self.search_entities(
            namespace_id=namespace_id,
            query=None,
            filters={"__entity_type": "run", "metadata.run_id": run_id},
            limit=limit,
        )

    def get_run(self, namespace_id: str, run_id: str) -> RunRecord:
        runs = self._search_run_entities(namespace_id, run_id, limit=1)
        if not runs:
            raise ValueError(f"Run `{run_id}` not found")
        run_entity = runs[0]
        steps = self.search_entities(
            namespace_id=namespace_id,
            query=None,
            filters={"__entity_type": "run_step", "metadata.run_id": run_id},
            limit=1000,
        )
        ended = bool((run_entity.metadata or {}).get("ended", False))
        return RunRecord(id=run_id, created_at=run_entity.created_at, ended=ended, steps=steps)

    def delete_run(self, namespace_id: str, run_id: str):
        for run in self._search_run_entities(namespace_id, run_id, limit=1000):
            self.delete_entity_by_id(namespace_id, run.id)
        steps = self.search_entities(
            namespace_id=namespace_id,
            query=None,
            filters={"__entity_type": "run_step", "metadata.run_id": run_id},
            limit=1000,
        )
        for step in steps:
            self.delete_entity_by_id(namespace_id, step.id)

    def add_step(self, namespace_id: str, run_id: str, step: dict, prompt: str | None = None) -> EntityUpdate:
        step_name = str(step.get("name") or step.get("agent") or "Step")
        status = str(step.get("status") or "")
        summary = f"{step_name}: {status}".strip(": ")
        updates = self.update_entities(
            namespace_id=namespace_id,
            entities=[
                Entity(
                    type="run_step",
                    content=summary,
                    metadata={"run_id": run_id, "step": step, "prompt": prompt or "", "summary": summary},
                )
            ],
            enable_conflict_resolution=False,
        )
        return updates[0]

    def list_runs(self, namespace_id: str, limit: int = 10) -> list[RunRecord]:
        runs = self.search_entities(
            namespace_id=namespace_id,
            query=None,
            filters={"__entity_type": "run"},
            limit=limit,
        )
        out: list[RunRecord] = []
        for run in runs:
            run_id = str((run.metadata or {}).get("run_id") or run.id)
            out.append(
                RunRecord(
                    id=run_id,
                    created_at=run.created_at,
                    ended=bool((run.metadata or {}).get("ended", False)),
                    steps=[],
                )
            )
        return out

    def search_runs(
        self,
        namespace_id: str,
        query: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> RunRecord | None:
        run_step_filters = {"__entity_type": "run_step"}
        for key, value in (filters or {}).items():
            run_step_filters[f"metadata.{key}"] = value
        matches = self.search_entities(
            namespace_id=namespace_id,
            query=query,
            filters=run_step_filters,
            limit=1,
        )
        if not matches:
            return None
        run_id = str((matches[0].metadata or {}).get("run_id") or "")
        if not run_id:
            return None
        return self.get_run(namespace_id, run_id)

    async def end_run(self, namespace_id: str, run_id: str):
        runs = self._search_run_entities(namespace_id, run_id, limit=1)
        if not runs:
            return
        old_run = runs[0]
        self.delete_entity_by_id(namespace_id, old_run.id)
        updated_md = dict(old_run.metadata or {})
        updated_md["ended"] = True
        self.update_entities(
            namespace_id=namespace_id,
            entities=[Entity(type="run", content=old_run.content, metadata=updated_md)],
            enable_conflict_resolution=False,
        )
        await self.analyze_run(namespace_id, run_id)

    async def analyze_run(self, namespace_id: str, run_id: str):
        steps = self.search_entities(
            namespace_id=namespace_id,
            query=None,
            filters={"__entity_type": "run_step", "metadata.run_id": run_id},
            limit=200,
        )
        if not steps:
            return

        first_step_md = steps[0].metadata or {}
        first_step = first_step_md.get("step", {}) if isinstance(first_step_md, dict) else {}
        intent = "Unknown task"
        if isinstance(first_step, dict):
            intent = str(first_step.get("intent") or intent)

        messages: list[dict[str, str]] = [{"role": "user", "content": intent}]
        for step in steps:
            if isinstance(step.content, str):
                content = step.content
            else:
                content = json.dumps(step.content)
            messages.append({"role": "assistant", "content": content})

        try:
            tips = generate_tips(messages)
        except Exception:
            return

        agents: set[str] = set()
        for step in steps:
            metadata = step.metadata or {}
            step_data = metadata.get("step", {}) if isinstance(metadata, dict) else {}
            if isinstance(step_data, dict):
                agent_name = str(step_data.get("name") or step_data.get("agent") or "UnknownAgent")
                agents.add(agent_name)
        if not agents:
            agents = {"UnknownAgent"}

        tip_entities: list[Entity] = []
        for tip in tips:
            for agent_name in agents:
                tip_entities.append(
                    Entity(
                        type="tip",
                        content=tip.content,
                        metadata={
                            "agent": agent_name,
                            "run_id": run_id,
                            "category": tip.category,
                            "rationale": tip.rationale,
                            "trigger": tip.trigger,
                            "user_id": "100",
                        },
                    )
                )
        if tip_entities:
            self.update_entities(namespace_id=namespace_id, entities=tip_entities, enable_conflict_resolution=False)

    def get_matching_tips(
        self,
        namespace_id: str,
        agent_id: str,
        query: str,
        limit: int = 3,
    ) -> list[str]:
        tips = self.search_entities(
            namespace_id=namespace_id,
            query=query,
            filters={"__entity_type": "tip", "metadata.agent": agent_id, "metadata.user_id": "100"},
            limit=limit,
        )
        if not tips:
            tips = self.search_entities(
                namespace_id=namespace_id,
                query=query,
                filters={"__entity_type": "tip", "metadata.user_id": "100"},
                limit=limit,
            )
        return [str(tip.content) for tip in tips]

    async def store_user_message_for_preferences(
        self,
        namespace_id: str,
        message: str,
        user_id: str,
    ) -> list[EntityUpdate]:
        return await self.extract_facts_from_messages_async(
            namespace_id=namespace_id,
            messages=[{"role": "user", "content": message}],
            metadata={"user_id": user_id},
            enable_conflict_resolution=False,
        )

    def get_user_preferences(
        self,
        namespace_id: str,
        user_id: str,
        query: str | None = None,
        limit: int = 5,
    ) -> dict[str, list[dict[str, Any]]]:
        facts = self.search_for_facts(
            namespace_id=namespace_id,
            query=query,
            filters={"user_id": user_id},
            limit=limit,
        )
        categorized_preferences: dict[str, list[dict[str, Any]]] = {}
        for fact in facts:
            metadata = fact.metadata or {}
            category = str(metadata.get("category") or "misc")
            categorized_preferences.setdefault(category, []).append(
                {
                    "id": fact.id,
                    "content": str(fact.content),
                    "key": metadata.get("key"),
                    "value": metadata.get("value"),
                }
            )
        return categorized_preferences

    def update_preference(
        self, namespace_id: str, user_id: str, category: str, key: str, value: Any
    ) -> list[EntityUpdate]:
        existing = self.search_for_facts(
            namespace_id=namespace_id,
            query=None,
            filters={"user_id": user_id, "category": category, "key": key},
            limit=50,
        )
        for entity in existing:
            self.delete_entity_by_id(namespace_id, entity.id)

        content = f"User's {key.replace('_', ' ')} ({category.replace('_', ' ')}) is {value}"
        metadata = {
            "type": "user_preference",
            "category": category,
            "key": key,
            "value": value,
            "user_id": user_id,
            "confidence": 1.0,
            "source": "explicit",
            "last_updated": datetime.now(UTC).isoformat(),
        }
        return self.create_and_store_fact(
            namespace_id=namespace_id,
            content=content,
            metadata=metadata,
            enable_conflict_resolution=False,
        )

    def delete_preference(self, namespace_id: str, user_id: str, category: str, key: str) -> None:
        facts = self.search_for_facts(
            namespace_id=namespace_id,
            query=None,
            filters={"user_id": user_id, "category": category, "key": key},
            limit=50,
        )
        for fact in facts:
            self.delete_entity_by_id(namespace_id, fact.id)

    def _get_user_id(self, state: "AgentState") -> str:
        if hasattr(state, "pi") and state.pi:
            pi_dict = json.loads(state.pi)
            first = str(pi_dict.get("first_name", ""))
            last = str(pi_dict.get("last_name", ""))
            phone = str(pi_dict.get("phone_number", ""))
            state.user_id = f"{first}_{last}_{phone}"
        else:
            state.user_id = "default"
        self.user_id = state.user_id
        return state.user_id
