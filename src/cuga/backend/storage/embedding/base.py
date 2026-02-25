from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class EmbeddingSchemaConfig:
    embedding_dim: int
    id_column: str
    metadata_columns: Dict[str, str]
    auxiliary_columns: Dict[str, str]


class EmbeddingStoreBackend(Protocol):
    def add(self, id: str, embedding: List[float], metadata: Dict[str, Any]) -> None: ...
    def search(
        self, query_embedding: List[float], limit: int, metadata_filter: Dict[str, Any]
    ) -> List[tuple]: ...
    def get(self, id: str) -> Optional[Dict[str, Any]]: ...
    def delete(self, id: str) -> None: ...
    def list(self, metadata_filter: Dict[str, Any], limit: int) -> List[Dict[str, Any]]: ...
