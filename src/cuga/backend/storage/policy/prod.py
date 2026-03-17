from typing import Any, Dict, List, Optional

from cuga.backend.cuga_graph.policy.models import PolicyType
from cuga.backend.storage.embedding.prod import ProdEmbeddingStore
from cuga.backend.storage.policy.base import policy_embedding_schema
from cuga.config import get_service_instance_id, get_tenant_id


class ProdPolicyStore:
    def __init__(self, postgres_url: str, collection_name: str):
        self._postgres_url = postgres_url
        self._collection_name = collection_name
        self._embedding_dim: Optional[int] = None
        self._store: Optional[ProdEmbeddingStore] = None

    def _get_store(self, embedding_dim: int) -> ProdEmbeddingStore:
        if self._store is None:
            schema = policy_embedding_schema(embedding_dim)
            self._store = ProdEmbeddingStore(self._postgres_url, self._collection_name, schema)
            self._embedding_dim = embedding_dim
        return self._store

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def create_schema(self, embedding_dim: int) -> None:
        self._get_store(embedding_dim)

    def _instance_id(self) -> str:
        return get_service_instance_id()

    def _tenant_id(self) -> str:
        return get_tenant_id()

    async def add_policy(self, policy_data: Dict[str, Any]) -> None:
        embedding = policy_data.get("embedding")
        if embedding is None:
            raise ValueError("policy_data must contain 'embedding'")
        policy_id = policy_data["id"]
        meta = {
            "id": policy_id,
            "tenant_id": self._tenant_id(),
            "instance_id": self._instance_id(),
            "policy_type": policy_data.get("policy_type", ""),
            "enabled": policy_data.get("enabled", True),
            "priority": policy_data.get("priority", 0),
            "policy_json": policy_data.get("policy_json", "{}"),
        }
        dim = len(embedding)
        store = self._get_store(dim)
        await store.add(policy_id, embedding, meta)

    async def update_policy(self, policy_data: Dict[str, Any]) -> None:
        await self.delete_policy(policy_data["id"])
        await self.add_policy(policy_data)

    async def delete_policy(self, policy_id: str) -> None:
        if self._store is None:
            return
        await self._store.delete(policy_id, tenant_id=self._tenant_id(), instance_id=self._instance_id())

    async def get_policy(self, policy_id: str) -> Optional[Dict[str, Any]]:
        if self._store is None:
            return None
        return await self._store.get(policy_id, tenant_id=self._tenant_id(), instance_id=self._instance_id())

    async def search_policies(
        self,
        query_embedding: List[float],
        limit: int,
        policy_type: Optional[PolicyType],
        enabled_only: bool,
    ) -> List[tuple]:
        if self._store is None:
            return []
        filt: Dict[str, Any] = {"tenant_id": self._tenant_id(), "instance_id": self._instance_id()}
        if policy_type is not None:
            filt["policy_type"] = policy_type.value if hasattr(policy_type, "value") else str(policy_type)
        if enabled_only:
            filt["enabled"] = True
        return await self._store.search(query_embedding, limit, filt)

    async def list_policies(
        self,
        policy_type: Optional[PolicyType],
        enabled_only: bool,
        limit: int,
    ) -> List[Dict[str, Any]]:
        if self._store is None:
            return []
        filt: Dict[str, Any] = {"tenant_id": self._tenant_id(), "instance_id": self._instance_id()}
        if policy_type is not None:
            filt["policy_type"] = policy_type.value if hasattr(policy_type, "value") else str(policy_type)
        if enabled_only:
            filt["enabled"] = True
        return await self._store.list(filt, limit)

    async def count_policies(self, policy_type: Optional[PolicyType]) -> int:
        if self._store is None:
            return 0
        filt: Dict[str, Any] = {"tenant_id": self._tenant_id(), "instance_id": self._instance_id()}
        if policy_type is not None:
            filt["policy_type"] = policy_type.value if hasattr(policy_type, "value") else str(policy_type)
        rows = await self._store.list(filt, 10000)
        return len(rows)
