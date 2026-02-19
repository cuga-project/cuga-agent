#!/usr/bin/env python3
"""Integration-style smoke test for in-process Kaizen memory."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest import mock

from kaizen.schema.conflict_resolution import EntityUpdate
from kaizen.schema.core import Entity, Namespace, RecordedEntity
from kaizen.schema.exceptions import NamespaceNotFoundException
from kaizen.schema.tips import Tip

from cuga.backend.memory.memory import Memory
from cuga.config import settings


class FakeKaizenClient:
    def __init__(self):
        self.namespaces: dict[str, Namespace] = {}
        self.entities: dict[str, list[RecordedEntity]] = {}
        self.counter = 1

    def ready(self) -> bool:
        return True

    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        namespace_id = namespace_id or "ns_auto"
        namespace = Namespace(id=namespace_id, created_at=datetime.now(UTC), num_entities=0)
        self.namespaces[namespace_id] = namespace
        self.entities[namespace_id] = []
        return namespace

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        if namespace_id not in self.namespaces:
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")
        ns = self.namespaces[namespace_id]
        return Namespace(id=ns.id, created_at=ns.created_at, num_entities=len(self.entities.get(ns.id, [])))

    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        namespaces = list(self.namespaces.values())[:limit]
        return [Namespace(id=ns.id, created_at=ns.created_at, num_entities=len(self.entities[ns.id])) for ns in namespaces]

    def delete_namespace(self, namespace_id: str) -> None:
        self.namespaces.pop(namespace_id, None)
        self.entities.pop(namespace_id, None)

    def namespace_exists(self, namespace_id: str) -> bool:
        return namespace_id in self.namespaces

    def update_entities(self, namespace_id: str, entities: list[Entity], enable_conflict_resolution: bool = True) -> list[EntityUpdate]:
        _ = enable_conflict_resolution
        if namespace_id not in self.namespaces:
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")

        updates: list[EntityUpdate] = []
        for entity in entities:
            entity_id = str(self.counter)
            self.counter += 1
            recorded = RecordedEntity(
                id=entity_id,
                type=entity.type,
                content=entity.content,
                metadata=entity.metadata,
                created_at=datetime.now(UTC),
            )
            self.entities[namespace_id].append(recorded)
            updates.append(EntityUpdate(id=entity_id, type=entity.type, content=entity.content, event="ADD", metadata=entity.metadata))
        return updates

    def search_entities(self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10) -> list[RecordedEntity]:
        if namespace_id not in self.namespaces:
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")

        def _matches(entity: RecordedEntity) -> bool:
            for key, value in (filters or {}).items():
                if key == "__entity_type" and entity.type != value:
                    return False
                if key.startswith("metadata.") and (entity.metadata or {}).get(key.split(".", 1)[1]) != value:
                    return False
                if key not in {"__entity_type"} and not key.startswith("metadata.") and (entity.metadata or {}).get(key) != value:
                    return False
            if query is None:
                return True
            return query.lower() in str(entity.content).lower()

        rows = [row for row in self.entities[namespace_id] if _matches(row)]
        rows.sort(key=lambda row: row.created_at)
        return rows[:limit]

    def get_all_entities(self, namespace_id: str, filters: dict | None = None, limit: int = 100) -> list[RecordedEntity]:
        return self.search_entities(namespace_id=namespace_id, query=None, filters=filters, limit=limit)

    def delete_entity_by_id(self, namespace_id: str, entity_id: str) -> None:
        if namespace_id not in self.namespaces:
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")
        self.entities[namespace_id] = [row for row in self.entities[namespace_id] if row.id != entity_id]


class TestMemoryIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import cuga.backend.memory.memory as memory_module

        settings.set("advanced_features.enable_memory", True)
        settings.set("advanced_features.enable_fact", True)

        Memory._instance = None
        Memory._initialized = False

        self.fake_client = FakeKaizenClient()
        self.kaizen_patch = mock.patch.object(memory_module, "KaizenClient", lambda: self.fake_client)
        self.extract_patch = mock.patch.object(
            memory_module,
            "extract_facts_from_messages",
            lambda _messages: [
                memory_module.ExtractedFact(
                    category="personal_details",
                    key="name",
                    value="Sami",
                    content="User's name is Sami",
                )
            ],
        )
        self.tips_patch = mock.patch.object(
            memory_module,
            "generate_tips",
            lambda _messages: [
                Tip(
                    content="Always confirm date boundaries",
                    rationale="It prevents API off-by-one windows",
                    category="strategy",
                    trigger="When scheduling",
                )
            ],
        )

        self.kaizen_patch.start()
        self.extract_patch.start()
        self.tips_patch.start()

        self.memory = Memory()

    def tearDown(self):
        self.kaizen_patch.stop()
        self.extract_patch.stop()
        self.tips_patch.stop()
        Memory._instance = None
        Memory._initialized = False

    async def test_full_memory_flow(self):
        namespace = self.memory.create_namespace("integration_ns")
        self.assertEqual(namespace.id, "integration_ns")

        self.memory.create_and_store_fact(
            namespace_id=namespace.id,
            content="Python is used for APIs",
            metadata={"user_id": "test_user", "category": "technology", "key": "language", "value": "Python"},
            enable_conflict_resolution=False,
        )

        extracted = await self.memory.extract_facts_from_messages_async(
            namespace_id=namespace.id,
            messages=[{"role": "user", "content": "My name is Sami"}],
            metadata={"user_id": "test_user"},
            enable_conflict_resolution=False,
        )
        self.assertEqual(len(extracted), 1)

        facts = self.memory.search_for_facts(namespace.id, query="Sami", filters={"user_id": "test_user"}, limit=10)
        self.assertGreaterEqual(len(facts), 1)

        run = self.memory.create_run(namespace.id, run_id="run_1")
        self.memory.add_step(namespace.id, run.id, {"name": "TaskAnalyzer", "status": "ok", "intent": "book meeting"})
        await self.memory.end_run(namespace.id, run.id)

        run_after = self.memory.get_run(namespace.id, run.id)
        self.assertTrue(run_after.ended)

        tips = self.memory.search_entities(namespace.id, query=None, filters={"__entity_type": "tip"}, limit=10)
        self.assertGreaterEqual(len(tips), 1)

        self.memory.delete_namespace(namespace.id)
        with self.assertRaises(NamespaceNotFoundException):
            self.memory.get_namespace_details(namespace.id)


if __name__ == "__main__":
    unittest.main()
