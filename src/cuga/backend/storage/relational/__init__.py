from cuga.backend.storage.relational.base import RelationalStore
from cuga.backend.storage.relational.local import LocalRelationalStore
from cuga.backend.storage.relational.prod import ProdRelationalStore


def get_relational_store(db_name: str, mode: str, local_db_path: str, postgres_url: str) -> RelationalStore:
    if mode == "prod":
        if not postgres_url:
            raise ValueError("storage.postgres_url is required when storage.mode=prod")
        return ProdRelationalStore(postgres_url, db_name)
    return LocalRelationalStore(local_db_path)


__all__ = ["RelationalStore", "LocalRelationalStore", "ProdRelationalStore", "get_relational_store"]
