from cuga.backend.storage.embedding.base import EmbeddingSchemaConfig, EmbeddingStoreBackend
from cuga.backend.storage.embedding.embedding_service import (
    create_embedding_function,
    get_embedding_config,
    get_embedding_dimension,
)
from cuga.backend.storage.embedding.local import LocalEmbeddingStore
from cuga.backend.storage.embedding.prod import ProdEmbeddingStore


def get_embedding_store(
    collection_name: str,
    schema: EmbeddingSchemaConfig,
    mode: str,
    local_db_path: str,
    postgres_url: str,
) -> EmbeddingStoreBackend:
    if mode == "prod":
        if not postgres_url:
            raise ValueError("storage.postgres_url is required when storage.mode=prod")
        return ProdEmbeddingStore(postgres_url, collection_name, schema)
    return LocalEmbeddingStore(local_db_path, collection_name, schema)


__all__ = [
    "EmbeddingSchemaConfig",
    "EmbeddingStoreBackend",
    "LocalEmbeddingStore",
    "ProdEmbeddingStore",
    "create_embedding_function",
    "get_embedding_config",
    "get_embedding_dimension",
    "get_embedding_store",
]
