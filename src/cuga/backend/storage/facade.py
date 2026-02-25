import os
from typing import TYPE_CHECKING, Optional

from cuga.config import DBS_DIR, settings

if TYPE_CHECKING:
    from cuga.backend.storage.relational.base import RelationalStore
    from cuga.backend.storage.embedding.base import EmbeddingSchemaConfig, EmbeddingStoreBackend
    from cuga.backend.storage.policy.base import PolicyStoreBackend

_storage_facade: Optional["StorageFacade"] = None


def get_storage() -> "StorageFacade":
    global _storage_facade
    if _storage_facade is None:
        _storage_facade = StorageFacade()
    return _storage_facade


def _storage_mode() -> str:
    return getattr(settings, "storage", None) and getattr(settings.storage, "mode", "local") or "local"


def _local_db_path() -> str:
    path = getattr(settings, "storage", None) and getattr(settings.storage, "local_db_path", "") or ""
    if path:
        return path
    os.makedirs(DBS_DIR, exist_ok=True)
    return os.path.join(DBS_DIR, "cuga.db")


def _postgres_url() -> str:
    return getattr(settings, "storage", None) and getattr(settings.storage, "postgres_url", "") or ""


class StorageFacade:
    def get_relational_store(self, db_name: str) -> "RelationalStore":
        from cuga.backend.storage.relational import get_relational_store

        return get_relational_store(db_name, _storage_mode(), _local_db_path(), _postgres_url())

    def get_embedding_store(
        self, collection_name: str, schema: "EmbeddingSchemaConfig"
    ) -> "EmbeddingStoreBackend":
        from cuga.backend.storage.embedding import get_embedding_store

        return get_embedding_store(
            collection_name, schema, _storage_mode(), _local_db_path(), _postgres_url()
        )

    def get_policy_store_backend(self, collection_name: str) -> "PolicyStoreBackend":
        from cuga.backend.storage.policy import get_policy_store_backend

        return get_policy_store_backend(collection_name, _storage_mode(), _local_db_path(), _postgres_url())
