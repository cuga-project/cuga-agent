from cuga.backend.storage.policy.base import PolicyStoreBackend
from cuga.backend.storage.policy.local import LocalPolicyStore
from cuga.backend.storage.policy.prod import ProdPolicyStore


def get_policy_store_backend(
    collection_name: str, mode: str, local_db_path: str, postgres_url: str
) -> PolicyStoreBackend:
    if mode == "prod":
        if not postgres_url:
            raise ValueError("storage.postgres_url is required when storage.mode=prod")
        return ProdPolicyStore(postgres_url, collection_name)
    return LocalPolicyStore(local_db_path, collection_name)


__all__ = ["PolicyStoreBackend", "LocalPolicyStore", "ProdPolicyStore", "get_policy_store_backend"]
