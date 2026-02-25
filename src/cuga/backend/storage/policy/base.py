from typing import Any, Dict, List, Optional, Protocol

from cuga.backend.cuga_graph.policy.models import PolicyType
from cuga.backend.storage.embedding.base import EmbeddingSchemaConfig


def policy_embedding_schema(embedding_dim: int) -> EmbeddingSchemaConfig:
    return EmbeddingSchemaConfig(
        embedding_dim=embedding_dim,
        id_column="id",
        metadata_columns={
            "id": "text",
            "policy_type": "text",
            "enabled": "boolean",
            "priority": "integer",
        },
        auxiliary_columns={"policy_json": "text"},
    )


class PolicyStoreBackend(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def create_schema(self, embedding_dim: int) -> None: ...
    def add_policy(self, policy_data: Dict[str, Any]) -> None: ...
    def update_policy(self, policy_data: Dict[str, Any]) -> None: ...
    def delete_policy(self, policy_id: str) -> None: ...
    def get_policy(self, policy_id: str) -> Optional[Dict[str, Any]]: ...
    def search_policies(
        self,
        query_embedding: List[float],
        limit: int,
        policy_type: Optional[PolicyType],
        enabled_only: bool,
    ) -> List[tuple]: ...
    def list_policies(
        self,
        policy_type: Optional[PolicyType],
        enabled_only: bool,
        limit: int,
    ) -> List[Dict[str, Any]]: ...
    def count_policies(self, policy_type: Optional[PolicyType]) -> int: ...
