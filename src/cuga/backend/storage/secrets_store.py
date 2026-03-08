import asyncio
import json
import os
import re
import threading
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet
from loguru import logger

from cuga.backend.storage import get_storage
from cuga.config import get_service_instance_id, get_tenant_id


def _get_store():
    return get_storage().get_relational_store("config")


def _tenant_id() -> str:
    return get_tenant_id()


def _instance_id() -> str:
    return get_service_instance_id()


def _parse_tags(val: Any) -> dict[str, Any] | None:
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return None
    return None


def _fernet():
    from cuga.config import settings

    sec = getattr(settings, "secrets", None)
    key_env = getattr(sec, "db_encryption_key_env", "CUGA_SECRET_KEY") if sec else "CUGA_SECRET_KEY"
    key_b64 = os.environ.get(key_env)
    if not key_b64:
        return None
    try:
        return Fernet(key_b64.encode() if isinstance(key_b64, str) else key_b64)
    except Exception:
        return None


def _is_prod(store) -> bool:
    return type(store).__name__ == "ProdRelationalStore"


def _placeholders_sqlite(sql: str) -> str:
    return sql


def _placeholders_pg(sql: str) -> str:
    i = [0]

    def repl(_):
        i[0] += 1
        return f"${i[0]}"

    return re.sub(r"\?", repl, sql)


async def ensure_schema(store) -> None:
    is_prod = _is_prod(store)
    blob_type = "BYTEA" if is_prod else "BLOB"
    json_type = "JSONB" if is_prod else "TEXT"
    await store.execute(
        f"""
        CREATE TABLE IF NOT EXISTS secrets (
            tenant_id TEXT NOT NULL DEFAULT '',
            instance_id TEXT NOT NULL DEFAULT '',
            agent_id TEXT NOT NULL DEFAULT '*',
            version TEXT NOT NULL DEFAULT '*',
            id TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT '',
            encrypted_value {blob_type} NOT NULL,
            description TEXT,
            tags {json_type},
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (tenant_id, instance_id, agent_id, version, id)
        )
        """
    )
    await store.commit()


async def get_secret(
    secret_id: str,
    *,
    tenant_id: str | None = None,
    instance_id: str | None = None,
    agent_id: str | None = None,
    version: str = "*",
) -> str | None:
    tenant_id = tenant_id or _tenant_id()
    instance_id = instance_id or _instance_id()
    agent_id = agent_id or "*"
    fernet = _fernet()
    if not fernet:
        logger.warning("CUGA_SECRET_KEY not set; cannot decrypt DB secrets")
        return None
    store = _get_store()
    try:
        await ensure_schema(store)
        ph = _placeholders_pg if _is_prod(store) else _placeholders_sqlite
        row = await store.fetchone(
            ph(
                "SELECT encrypted_value FROM secrets "
                "WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND version = ? AND id = ?"
            ),
            (tenant_id, instance_id, agent_id, version, secret_id),
        )
        if not row:
            if agent_id != "*":
                row = await store.fetchone(
                    ph(
                        "SELECT encrypted_value FROM secrets "
                        "WHERE tenant_id = ? AND instance_id = ? AND agent_id = '*' AND version = ? AND id = ?"
                    ),
                    (tenant_id, instance_id, version, secret_id),
                )
            if not row:
                return None
        enc = row.get("encrypted_value") if isinstance(row, dict) else row[0]
        if enc is None:
            return None
        dec = fernet.decrypt(enc)
        return dec.decode("utf-8")
    except Exception as e:
        logger.debug("get_secret failed: {}", e)
        return None
    finally:
        await store.close()


def get_secret_sync(
    secret_id: str,
    *,
    tenant_id: str | None = None,
    instance_id: str | None = None,
    agent_id: str | None = None,
    version: str = "*",
) -> str | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return asyncio.run(
            get_secret(
                secret_id, tenant_id=tenant_id, instance_id=instance_id, agent_id=agent_id, version=version
            )
        )
    result: list[Any] = [None]
    done = threading.Event()

    def run():
        nonlocal result
        result[0] = asyncio.run(
            get_secret(
                secret_id, tenant_id=tenant_id, instance_id=instance_id, agent_id=agent_id, version=version
            )
        )
        done.set()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    finished = done.wait(timeout=5.0)
    if not finished:
        raise TimeoutError("get_secret_sync timed out after 5s")
    return result[0]


async def set_secret(
    secret_id: str,
    value: str,
    *,
    tenant_id: str | None = None,
    instance_id: str | None = None,
    agent_id: str | None = None,
    version: str | None = None,
    created_by: str = "",
    description: str | None = None,
    tags: dict[str, Any] | None = None,
) -> None:
    tenant_id = tenant_id or _tenant_id()
    instance_id = instance_id or _instance_id()
    agent_id = agent_id or "*"
    version = version or "*"
    store = _get_store()
    fernet = _fernet()
    if not fernet:
        raise RuntimeError("CUGA_SECRET_KEY must be set to use the DB secrets backend")
    encrypted = fernet.encrypt(value.encode("utf-8"))
    now = datetime.utcnow().isoformat()
    tags_json = json.dumps(tags) if tags else None
    try:
        await ensure_schema(store)
        is_prod = _is_prod(store)
        ph = _placeholders_pg if is_prod else _placeholders_sqlite
        await store.execute(
            ph(
                """
                INSERT INTO secrets (tenant_id, instance_id, agent_id, version, id, created_by, encrypted_value, description, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, instance_id, agent_id, version, id)
                DO UPDATE SET encrypted_value = excluded.encrypted_value, description = excluded.description, tags = excluded.tags, updated_at = excluded.updated_at
                """
            ),
            (
                tenant_id,
                instance_id,
                agent_id,
                version,
                secret_id,
                created_by,
                encrypted,
                description or "",
                tags_json,
                now,
                now,
            ),
        )
        await store.commit()
    finally:
        await store.close()


async def list_secrets(
    *,
    tenant_id: str | None = None,
    instance_id: str | None = None,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    tenant_id = tenant_id or _tenant_id()
    instance_id = instance_id or _instance_id()
    store = _get_store()
    try:
        await ensure_schema(store)
        ph = _placeholders_pg if _is_prod(store) else _placeholders_sqlite
        if agent_id is not None:
            rows = await store.fetchall(
                ph(
                    "SELECT id, agent_id, version, created_by, description, tags, created_at, updated_at FROM secrets "
                    "WHERE tenant_id = ? AND instance_id = ? AND (agent_id = ? OR agent_id = '*')"
                ),
                (tenant_id, instance_id, agent_id),
            )
        else:
            rows = await store.fetchall(
                ph(
                    "SELECT id, agent_id, version, created_by, description, tags, created_at, updated_at FROM secrets "
                    "WHERE tenant_id = ? AND instance_id = ?"
                ),
                (tenant_id, instance_id),
            )
        return [
            {
                "id": r["id"] if isinstance(r, dict) else r[0],
                "agent_id": r["agent_id"] if isinstance(r, dict) else r[1],
                "version": r["version"] if isinstance(r, dict) else r[2],
                "created_by": r["created_by"] if isinstance(r, dict) else r[3],
                "description": r["description"] if isinstance(r, dict) else r[4],
                "tags": _parse_tags(r["tags"] if isinstance(r, dict) else r[5]),
                "created_at": r["created_at"] if isinstance(r, dict) else r[6],
                "updated_at": r["updated_at"] if isinstance(r, dict) else r[7],
            }
            for r in rows
        ]
    finally:
        await store.close()


async def delete_secret(
    secret_id: str,
    *,
    tenant_id: str | None = None,
    instance_id: str | None = None,
    agent_id: str | None = None,
    version: str | None = None,
) -> bool:
    tenant_id = tenant_id or _tenant_id()
    instance_id = instance_id or _instance_id()
    agent_id = agent_id or "*"
    version = version or "*"
    store = _get_store()
    try:
        await ensure_schema(store)
        ph = _placeholders_pg if _is_prod(store) else _placeholders_sqlite
        await store.execute(
            ph(
                "DELETE FROM secrets WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND version = ? AND id = ?"
            ),
            (tenant_id, instance_id, agent_id, version, secret_id),
        )
        await store.commit()
        return getattr(store, "_last_rowcount", 0) > 0
    finally:
        await store.close()


async def get_secret_metadata(
    secret_id: str,
    *,
    tenant_id: str | None = None,
    instance_id: str | None = None,
    agent_id: str | None = None,
    version: str | None = None,
) -> dict[str, Any] | None:
    tenant_id = tenant_id or _tenant_id()
    instance_id = instance_id or _instance_id()
    agent_id = agent_id or "*"
    version = version or "*"
    store = _get_store()
    try:
        await ensure_schema(store)
        ph = _placeholders_pg if _is_prod(store) else _placeholders_sqlite
        row = await store.fetchone(
            ph(
                "SELECT id, agent_id, version, created_by, description, tags, created_at, updated_at FROM secrets "
                "WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND version = ? AND id = ?"
            ),
            (tenant_id, instance_id, agent_id, version, secret_id),
        )
        if not row:
            return None
        r = row
        return {
            "id": r["id"] if isinstance(r, dict) else r[0],
            "agent_id": r["agent_id"] if isinstance(r, dict) else r[1],
            "version": r["version"] if isinstance(r, dict) else r[2],
            "created_by": r["created_by"] if isinstance(r, dict) else r[3],
            "description": r["description"] if isinstance(r, dict) else r[4],
            "tags": _parse_tags(r["tags"] if isinstance(r, dict) else r[5]),
            "created_at": r["created_at"] if isinstance(r, dict) else r[6],
            "updated_at": r["updated_at"] if isinstance(r, dict) else r[7],
        }
    finally:
        await store.close()
