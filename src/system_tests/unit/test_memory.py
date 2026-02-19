#!/usr/bin/env python3
"""Unit tests for Kaizen-backed CUGA memory."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kaizen.schema.conflict_resolution import EntityUpdate
from kaizen.schema.core import Entity, Namespace, RecordedEntity
from kaizen.schema.exceptions import NamespaceNotFoundException
from kaizen.schema.tips import Tip

from cuga.backend.memory.memory import Memory
from cuga.config import settings


class FakeKaizenClient:
    def __init__(self):
        self._namespaces: dict[str, Namespace] = {}
        self._entities: dict[str, list[RecordedEntity]] = {}
        self._counter = 1

    def ready(self) -> bool:
        return True

    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        namespace_id = namespace_id or f"ns_{len(self._namespaces) + 1}"
        namespace = Namespace(id=namespace_id, created_at=datetime.now(UTC), num_entities=0)
        self._namespaces[namespace_id] = namespace
        self._entities.setdefault(namespace_id, [])
        return namespace

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        namespace = self._namespaces.get(namespace_id)
        if namespace is None:
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")
        return Namespace(
            id=namespace.id,
            created_at=namespace.created_at,
            num_entities=len(self._entities.get(namespace_id, [])),
        )

    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        return [
            Namespace(id=ns.id, created_at=ns.created_at, num_entities=len(self._entities.get(ns.id, [])))
            for ns in list(self._namespaces.values())[:limit]
        ]

    def delete_namespace(self, namespace_id: str) -> None:
        self._namespaces.pop(namespace_id, None)
        self._entities.pop(namespace_id, None)

    def namespace_exists(self, namespace_id: str) -> bool:
        return namespace_id in self._namespaces

    def update_entities(
        self, namespace_id: str, entities: list[Entity], enable_conflict_resolution: bool = True
    ) -> list[EntityUpdate]:
        _ = enable_conflict_resolution
        if namespace_id not in self._namespaces:
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")

        updates: list[EntityUpdate] = []
        for entity in entities:
            entity_id = str(self._counter)
            self._counter += 1
            recorded = RecordedEntity(
                id=entity_id,
                type=entity.type,
                content=entity.content,
                metadata=entity.metadata,
                created_at=datetime.now(UTC),
            )
            self._entities[namespace_id].append(recorded)
            updates.append(
                EntityUpdate(
                    id=entity_id,
                    type=entity.type,
                    content=entity.content,
                    event="ADD",
                    metadata=entity.metadata,
                )
            )
        return updates

    def search_entities(
        self,
        namespace_id: str,
        query: str | None = None,
        filters: dict | None = None,
        limit: int = 10,
    ) -> list[RecordedEntity]:
        if namespace_id not in self._namespaces:
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")
        rows = list(self._entities[namespace_id])

        def matches(entity: RecordedEntity) -> bool:
            for key, value in (filters or {}).items():
                if key == "__entity_type":
                    if entity.type != value:
                        return False
                elif key.startswith("metadata."):
                    metadata_key = key.split(".", 1)[1]
                    if (entity.metadata or {}).get(metadata_key) != value:
                        return False
                elif key == "type":
                    if entity.type != value:
                        return False
                elif key == "id":
                    if entity.id != str(value):
                        return False
                else:
                    if (entity.metadata or {}).get(key) != value:
                        return False
            if query:
                return query.lower() in str(entity.content).lower()
            return True

        matched = [entity for entity in rows if matches(entity)]
        matched.sort(key=lambda row: row.created_at, reverse=True)
        return matched[:limit]

    def get_all_entities(
        self,
        namespace_id: str,
        filters: dict | None = None,
        limit: int = 100,
    ) -> list[RecordedEntity]:
        return self.search_entities(namespace_id=namespace_id, query=None, filters=filters, limit=limit)

    def delete_entity_by_id(self, namespace_id: str, entity_id: str) -> None:
        if namespace_id not in self._namespaces:
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")
        self._entities[namespace_id] = [entity for entity in self._entities[namespace_id] if entity.id != entity_id]


@pytest.fixture(autouse=True)
def memory_fixture(monkeypatch):
    import cuga.backend.memory.memory as memory_module

    settings.set("advanced_features.enable_memory", True)
    settings.set("advanced_features.enable_fact", True)

    Memory._instance = None
    Memory._initialized = False

    fake_client = FakeKaizenClient()
    monkeypatch.setattr(memory_module, "KaizenClient", lambda: fake_client)
    monkeypatch.setattr(
        memory_module,
        "extract_facts_from_messages",
        lambda _messages: [
            memory_module.ExtractedFact(
                category="user_preferences",
                key="timezone",
                value="PST",
                content="User timezone is PST",
            )
        ],
    )
    monkeypatch.setattr(
        memory_module,
        "generate_tips",
        lambda _messages: [
            Tip(
                content="Retry with a narrower query",
                rationale="Narrow queries return stronger matches",
                category="strategy",
                trigger="When retrieval is noisy",
            )
        ],
    )

    yield fake_client

    Memory._instance = None
    Memory._initialized = False


class TestMemorySingleton:
    def test_memory_singleton_instance(self):
        memory1 = Memory()
        memory2 = Memory()
        assert memory1 is memory2

    def test_memory_singleton_initialization(self):
        memory = Memory()
        assert hasattr(memory, "memory_client")
        assert memory.user_id is None


class TestMemoryBehavior:
    def test_namespace_and_fact_crud(self):
        memory = Memory()
        namespace = memory.create_namespace("test_namespace")

        events = memory.create_and_store_fact(
            namespace_id=namespace.id,
            content="User likes soccer",
            metadata={"user_id": "u-1", "category": "sports", "key": "activity", "value": "soccer"},
            enable_conflict_resolution=False,
        )

        assert len(events) == 1
        facts = memory.search_for_facts(namespace.id, query="soccer", filters={"user_id": "u-1"}, limit=5)
        assert len(facts) == 1
        assert facts[0].type == "fact"
        assert facts[0].metadata.get("category") == "sports"

        memory.delete_namespace(namespace.id)
        with pytest.raises(NamespaceNotFoundException):
            memory.get_namespace_details(namespace.id)

    @pytest.mark.asyncio
    async def test_extract_facts_from_messages_async(self):
        memory = Memory()
        namespace = memory.create_namespace("facts_async")

        updates = await memory.extract_facts_from_messages_async(
            namespace_id=namespace.id,
            messages=[{"role": "user", "content": "I live in SF"}],
            metadata={"user_id": "u-2"},
            enable_conflict_resolution=False,
        )

        assert len(updates) == 1
        facts = memory.search_for_facts(namespace.id, filters={"user_id": "u-2"})
        assert facts[0].metadata.get("key") == "timezone"

    @pytest.mark.asyncio
    async def test_run_lifecycle_and_tips(self):
        memory = Memory()
        namespace = memory.create_namespace("run_ns")

        run = memory.create_run(namespace.id, run_id="run_123")
        memory.add_step(
            namespace_id=namespace.id,
            run_id=run.id,
            step={"name": "TaskAnalyzer", "status": "ok", "intent": "Book flight"},
            prompt="Analyze",
        )
        memory.add_step(
            namespace_id=namespace.id,
            run_id=run.id,
            step={"name": "APIPlanner", "status": "ok"},
            prompt="Plan",
        )

        await memory.end_run(namespace.id, run.id)
        ended = memory.get_run(namespace.id, run.id)

        assert ended.ended is True
        tips = memory.search_entities(namespace.id, query=None, filters={"__entity_type": "tip"}, limit=10)
        assert len(tips) >= 1

    def test_preference_helpers(self):
        memory = Memory()
        namespace = memory.create_namespace("pref_ns")

        memory.update_preference(
            namespace_id=namespace.id,
            user_id="u-3",
            category="meeting_preferences",
            key="default_duration",
            value="30",
        )

        preferences = memory.get_user_preferences(namespace_id=namespace.id, user_id="u-3")
        assert "meeting_preferences" in preferences
        assert preferences["meeting_preferences"][0]["value"] == "30"

        memory.delete_preference(
            namespace_id=namespace.id,
            user_id="u-3",
            category="meeting_preferences",
            key="default_duration",
        )
        cleared = memory.get_user_preferences(namespace_id=namespace.id, user_id="u-3")
        assert "meeting_preferences" not in cleared
