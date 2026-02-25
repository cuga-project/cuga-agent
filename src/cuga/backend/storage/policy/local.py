from typing import Any, Dict, List, Optional

from cuga.backend.cuga_graph.policy.models import PolicyType
from cuga.backend.storage.embedding.local import LocalEmbeddingStore
from cuga.backend.storage.policy.base import policy_embedding_schema


class LocalPolicyStore:
    def __init__(self, db_path: str, collection_name: str):
        self._db_path = db_path
        self._collection_name = collection_name
        self._embedding_dim: Optional[int] = None
        self._store: Optional[LocalEmbeddingStore] = None

    def _get_store(self, embedding_dim: int) -> LocalEmbeddingStore:
        if self._store is None:
            schema = policy_embedding_schema(embedding_dim)
            self._store = LocalEmbeddingStore(self._db_path, self._collection_name, schema)
            self._embedding_dim = embedding_dim
        return self._store

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def create_schema(self, embedding_dim: int) -> None:
        self._get_store(embedding_dim)

    def add_policy(self, policy_data: Dict[str, Any]) -> None:
        embedding = policy_data.get("embedding")
        if embedding is None:
            raise ValueError("policy_data must contain 'embedding'")
        policy_id = policy_data["id"]
        meta = {
            "id": policy_id,
            "policy_type": policy_data.get("policy_type", ""),
            "enabled": policy_data.get("enabled", True),
            "priority": policy_data.get("priority", 0),
            "policy_json": policy_data.get("policy_json", "{}"),
        }
        dim = len(embedding)
        store = self._get_store(dim)
        store.delete(policy_id)
        store.add(policy_id, embedding, meta)

    def update_policy(self, policy_data: Dict[str, Any]) -> None:
        self.delete_policy(policy_data["id"])
        self.add_policy(policy_data)

    def delete_policy(self, policy_id: str) -> None:
        if self._store is None:
            return
        self._store.delete(policy_id)

    def get_policy(self, policy_id: str) -> Optional[Dict[str, Any]]:
        if self._store is None:
            return None
        return self._store.get(policy_id)

    def search_policies(
        self,
        query_embedding: List[float],
        limit: int,
        policy_type: Optional[PolicyType],
        enabled_only: bool,
    ) -> List[tuple]:
        if self._store is None:
            return []
        filt: Dict[str, Any] = {}
        if policy_type is not None:
            filt["policy_type"] = policy_type.value if hasattr(policy_type, "value") else str(policy_type)
        if enabled_only:
            filt["enabled"] = True
        return self._store.search(query_embedding, limit, filt)

    def list_policies(
        self,
        policy_type: Optional[PolicyType],
        enabled_only: bool,
        limit: int,
    ) -> List[Dict[str, Any]]:
        if self._store is None:
            return []
        filt: Dict[str, Any] = {}
        if policy_type is not None:
            filt["policy_type"] = policy_type.value if hasattr(policy_type, "value") else str(policy_type)
        if enabled_only:
            filt["enabled"] = True
        rows = self._store.list(filt, limit)
        seen: set = set()
        out = []
        id_col = "id"
        for r in rows:
            pid = r.get(id_col)
            if pid not in seen:
                seen.add(pid)
                out.append(r)
        return out

    def count_policies(self, policy_type: Optional[PolicyType]) -> int:
        if self._store is None:
            return 0
        filt: Dict[str, Any] = {}
        if policy_type is not None:
            filt["policy_type"] = policy_type.value if hasattr(policy_type, "value") else str(policy_type)
        return len(self._store.list(filt, 10000))
