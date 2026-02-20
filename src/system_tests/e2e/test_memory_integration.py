#!/usr/bin/env python3
"""Integration-style smoke tests for in-process Kaizen memory wiring."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest import mock

from kaizen.schema.conflict_resolution import EntityUpdate
from kaizen.schema.core import Entity, Namespace, RecordedEntity
from kaizen.schema.exceptions import NamespaceNotFoundException

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
        namespace = Namespace(id=namespace_id, created_at=datetime.now(timezone.utc), num_entities=0)
        self.namespaces[namespace_id] = namespace
        self.entities.setdefault(namespace_id, [])
        return namespace

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        namespace = self.namespaces.get(namespace_id)
        if namespace is None:
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")
        return Namespace(
            id=namespace.id,
            created_at=namespace.created_at,
            num_entities=len(self.entities.get(namespace_id, [])),
        )

    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        return [
            Namespace(id=ns.id, created_at=ns.created_at, num_entities=len(self.entities.get(ns.id, [])))
            for ns in list(self.namespaces.values())[:limit]
        ]

    def delete_namespace(self, namespace_id: str) -> None:
        self.namespaces.pop(namespace_id, None)
        self.entities.pop(namespace_id, None)

    def namespace_exists(self, namespace_id: str) -> bool:
        return namespace_id in self.namespaces

    def ensure_namespace(self, namespace_id: str) -> Namespace:
        if self.namespace_exists(namespace_id):
            return self.get_namespace_details(namespace_id)
        return self.create_namespace(namespace_id)

    def update_entities(
        self, namespace_id: str, entities: list[Entity], enable_conflict_resolution: bool = True
    ) -> list[EntityUpdate]:
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
                created_at=datetime.now(timezone.utc),
            )
            self.entities[namespace_id].append(recorded)
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
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedEntity]:
        if namespace_id not in self.namespaces:
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")

        def _matches(entity: RecordedEntity) -> bool:
            for key, value in (filters or {}).items():
                if key == "__entity_type" and entity.type != value:
                    return False
                if key.startswith("metadata.") and (entity.metadata or {}).get(key.split(".", 1)[1]) != value:
                    return False
                if (
                    key not in {"__entity_type"}
                    and not key.startswith("metadata.")
                    and (entity.metadata or {}).get(key) != value
                ):
                    return False
            if query is None:
                return True
            return query.lower() in str(entity.content).lower()

        rows = [row for row in self.entities[namespace_id] if _matches(row)]
        rows.sort(key=lambda row: row.created_at)
        return rows[:limit]

    async def store_user_memory(
        self,
        namespace_id: str,
        message: str,
        user_id: str,
        metadata: dict | None = None,
        enable_conflict_resolution: bool = False,
    ) -> list[EntityUpdate]:
        self.ensure_namespace(namespace_id)
        event_metadata = dict(metadata or {})
        event_metadata.update(
            {
                "user_id": user_id,
                "category": "personal_details",
                "key": "name",
                "value": "Sami",
            }
        )
        return self.update_entities(
            namespace_id=namespace_id,
            entities=[Entity(type="fact", content=message, metadata=event_metadata)],
            enable_conflict_resolution=enable_conflict_resolution,
        )

    def retrieve_user_memory(
        self, namespace_id: str, user_id: str, query: str | None = None, limit: int = 5
    ) -> dict[str, list[dict[str, str]]]:
        rows = self.search_entities(
            namespace_id=namespace_id,
            query=query,
            filters={"__entity_type": "fact", "metadata.user_id": user_id},
            limit=limit,
        )
        categorized: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            category = str((row.metadata or {}).get("category") or "misc")
            categorized.setdefault(category, []).append(
                {
                    "id": row.id,
                    "content": str(row.content),
                    "key": str((row.metadata or {}).get("key", "")),
                    "value": str((row.metadata or {}).get("value", "")),
                }
            )
        return categorized


class TestMemoryIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import cuga.backend.memory.memory as memory_module

        settings.set("advanced_features.enable_memory", True)
        settings.set("advanced_features.enable_fact", True)

        memory_module._KAIZEN_CLIENT = None
        self.fake_client = FakeKaizenClient()
        self.kaizen_patch = mock.patch.object(
            memory_module, "KaizenClient", lambda config=None: self.fake_client
        )
        self.kaizen_patch.start()
        self.memory_module = memory_module

    def tearDown(self):
        self.kaizen_patch.stop()
        self.memory_module._KAIZEN_CLIENT = None

    async def test_full_memory_flow(self):
        memory = self.memory_module.get_kaizen_client()

        namespace = memory.create_namespace("integration_ns")
        self.assertEqual(namespace.id, "integration_ns")

        updates = memory.update_entities(
            namespace.id,
            entities=[
                Entity(
                    type="fact",
                    content="Python is used for APIs",
                    metadata={
                        "user_id": "test_user",
                        "category": "technology",
                        "key": "language",
                        "value": "Python",
                    },
                )
            ],
            enable_conflict_resolution=False,
        )
        self.assertEqual(len(updates), 1)

        facts = memory.search_entities(
            namespace.id,
            query="Python",
            filters={"__entity_type": "fact", "metadata.user_id": "test_user"},
            limit=10,
        )
        self.assertGreaterEqual(len(facts), 1)

        normalized_user_id, preferences = await self.memory_module.sync_user_memory(
            user_id="test_user",
            query="My name is Sami",
            namespace_id=namespace.id,
        )
        self.assertEqual(normalized_user_id, "test_user")
        self.assertIn("personal_details", preferences)

        memory.delete_namespace(namespace.id)
        with self.assertRaises(NamespaceNotFoundException):
            memory.get_namespace_details(namespace.id)


if __name__ == "__main__":
    unittest.main()
