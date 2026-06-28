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
    db_path = os.path.join(DBS_DIR, "cuga.db")
    # User-only file perms (0o600) for the SQLite DB that holds agent
    # configs — including UI-entered embedding_api_key / OAuth client
    # secrets / future fields. Default umask leaves it 0o644 (world-
    # readable on the host), and config_store has no encryption layer.
    # Matches the .internal_token pattern in server/main.py.
    #
    # Pre-create the file (touch) before SQLite sees it so the mode
    # is set on first creation, not retroactively. SQLite's open is
    # idempotent — touching first doesn't break its schema init.
    try:
        if not os.path.exists(db_path):
            # touch + chmod before SQLite ever sees the file
            with open(db_path, "a"):
                pass
        os.chmod(db_path, 0o600)
    except OSError:
        # Best-effort hardening — skip on filesystems that don't honor
        # POSIX modes (Windows mounts, certain network FS). Not security
        # critical there since the OS layer enforces its own ACLs.
        pass
    return db_path


def _postgres_url() -> str:
    return getattr(settings, "storage", None) and getattr(settings.storage, "postgres_url", "") or ""


def get_storage_connection_params() -> tuple[str, str, str]:
    """Return ``(mode, local_db_path, postgres_url)`` for embedding backends."""
    return _storage_mode(), _local_db_path(), _postgres_url()


class StorageFacade:
    def __init__(self) -> None:
        self._relational_stores: dict[str, "RelationalStore"] = {}

    def get_relational_store(self, db_name: str) -> "RelationalStore":
        from cuga.backend.storage.relational import get_relational_store

        if db_name not in self._relational_stores:
            self._relational_stores[db_name] = get_relational_store(
                db_name, _storage_mode(), _local_db_path(), _postgres_url()
            )
        return self._relational_stores[db_name]

    async def close_relational_stores(self) -> None:
        for store in self._relational_stores.values():
            await store.close()
        self._relational_stores.clear()

    def invalidate_relational_stores(self) -> None:
        """Drop cached relational stores so the next access reopens against current files.

        Used after destructive operations (e.g. reset_config_db) that delete the backing
        local DB file out from under cached connections. Closes connections synchronously
        when the store supports it; otherwise drops the reference for lazy reopen.
        """
        for store in self._relational_stores.values():
            close_sync = getattr(store, "close_sync", None)
            if callable(close_sync):
                try:
                    close_sync()
                except Exception:
                    pass
        self._relational_stores.clear()

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
