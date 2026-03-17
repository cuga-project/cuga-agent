from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class EmbeddingSchemaConfig:
    embedding_dim: int
    id_column: str
    metadata_columns: Dict[str, str]
    auxiliary_columns: Dict[str, str]


class EmbeddingStoreBackend(Protocol):
    async def add(self, id: str, embedding: List[float], metadata: Dict[str, Any]) -> None: ...
    async def search(
        self, query_embedding: List[float], limit: int, metadata_filter: Dict[str, Any]
    ) -> List[tuple]: ...
    async def get(self, id: str) -> Optional[Dict[str, Any]]: ...
    async def delete(self, id: str) -> None: ...
    async def list(self, metadata_filter: Dict[str, Any], limit: int) -> List[Dict[str, Any]]: ...
