"""
Persistent store for agent config versions.

This module manages agent configurations with version control:
- Single agent_id (e.g., 'cuga-default') with multiple versions
- Version column stores: 'draft', '1', '2', etc.
- Registry queries use format: 'agent_id--version' (e.g., 'cuga-default--draft')
- Automatic database migration for backward compatibility

Database Schema:
    agent_configs table (in cuga.db local / Postgres prod):
        - agent_id (TEXT), version (TEXT), config_json (TEXT), created_at, updated_at
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from cuga.backend.storage import get_storage
from cuga.config import get_service_instance_id, get_tenant_id, settings

logger = logging.getLogger(__name__)


def _parse_agent_id(agent_id: str) -> str:
    if '--' in agent_id:
        return agent_id.split('--')[0]
    return agent_id


def _get_store():
    return get_storage().get_relational_store("config")


def _instance_id() -> str:
    return get_service_instance_id()


def _tenant_id() -> str:
    return get_tenant_id()


def run_sync(coro: Any) -> Any:
    """Run an async config_store coroutine from sync CLI/bootstrap code.

    Safe when no event loop is running (``asyncio.run``) and when one already is
    (worker thread with its own loop), matching the pattern used elsewhere in
    the codebase for sync wrappers around async storage.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def should_preserve_existing_configs() -> bool:
    """Whether bootstrap should skip seeding when agent_configs rows already exist.

    Controlled by ``storage.preserve_configs_on_startup`` (``prod`` | ``local`` | ``any``).
    Default ``prod``: only preserve when ``storage.mode`` is ``prod``. Invalid values
    fall back to ``prod``.
    """
    storage = getattr(settings, "storage", None)
    preserve = (getattr(storage, "preserve_configs_on_startup", None) or "prod").strip().lower()
    mode = (getattr(storage, "mode", None) or "local").strip().lower()
    if preserve not in ("prod", "local", "any"):
        preserve = "prod"
    if preserve == "any":
        return True
    return preserve == mode


async def has_any_config(agent_id: str = "cuga-default") -> bool:
    """Return True if any draft or published row exists for tenant+instance+agent."""
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    tenant_id = _tenant_id()
    inst_id = _instance_id()
    await _ensure_schema(store)
    row = await store.fetchone(
        """
        SELECT 1 FROM agent_configs
        WHERE tenant_id = ? AND instance_id = ? AND agent_id = ?
        LIMIT 1
        """,
        (tenant_id, inst_id, base_agent_id),
    )
    return row is not None


async def resolve_preserve_existing(agent_id: str = "cuga-default") -> bool:
    """Return True (and log) when bootstrap should keep existing configs for agent_id."""
    if not should_preserve_existing_configs():
        return False
    if not await has_any_config(agent_id):
        return False
    logger.info(
        "Preserving existing agent configs from DB (mode=%s, tenant=%r, instance=%r, agent=%r)",
        getattr(settings.storage, "mode", "local"),
        get_tenant_id(),
        get_service_instance_id(),
        agent_id,
    )
    return True


async def _ensure_schema(store) -> None:
    is_prod = type(store).__name__ == "ProdRelationalStore"
    ts_default = "CURRENT_TIMESTAMP::text" if is_prod else "datetime('now')"
    if is_prod:
        await store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS agent_configs (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                agent_id TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT 'draft',
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT ({ts_default}),
                updated_at TEXT NOT NULL DEFAULT ({ts_default}),
                PRIMARY KEY (tenant_id, instance_id, agent_id, version)
            )
            """
        )
    else:
        await store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS agent_configs (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                agent_id TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT 'draft',
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT ({ts_default}),
                updated_at TEXT NOT NULL DEFAULT ({ts_default}),
                PRIMARY KEY (tenant_id, instance_id, agent_id, version)
            )
            """
        )
    await store.commit()


def normalize_policies_for_save(config: dict[str, Any]) -> None:
    """Ensure config['policies'] is always { enablePolicies: bool, policies: list }. Mutates config in place."""
    if "policies" not in config:
        return
    p = config["policies"]
    if isinstance(p, list):
        config["policies"] = {"enablePolicies": True, "policies": p}
    elif isinstance(p, dict):
        policies_list = p.get("policies")
        if not isinstance(policies_list, list):
            config["policies"] = {"enablePolicies": p.get("enablePolicies", True), "policies": []}
        else:
            config["policies"] = {
                "enablePolicies": p.get("enablePolicies", True),
                "policies": policies_list,
            }


async def save_config(config: dict[str, Any], agent_id: str = "cuga-default") -> str:
    normalize_policies_for_save(config)
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    tenant_id = _tenant_id()
    inst_id = _instance_id()
    await _ensure_schema(store)
    row = await store.fetchone(
        """
        SELECT MAX(CAST(version AS INTEGER)) as max_ver
        FROM agent_configs
        WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND version != 'draft'
        """,
        (tenant_id, inst_id, base_agent_id),
    )
    max_ver = row["max_ver"] if row and "max_ver" in row else (row[0] if row else None)
    next_version = (max_ver or 0) + 1
    version_str = str(next_version)
    ts = "CURRENT_TIMESTAMP" if type(store).__name__ == "ProdRelationalStore" else "datetime('now')"
    await store.execute(
        f"""
        INSERT INTO agent_configs (tenant_id, instance_id, agent_id, version, config_json, updated_at)
        VALUES (?, ?, ?, ?, ?, {ts})
        """,
        (tenant_id, inst_id, base_agent_id, version_str, json.dumps(config)),
    )
    await store.commit()
    return version_str


async def update_published_config_at_version(config: dict[str, Any], agent_id: str, version: str) -> bool:
    """Replace config_json for an existing published version without bumping the version number."""
    if not version or version == "draft" or not str(version).isdigit():
        raise ValueError("version must be a numeric published version string")
    normalize_policies_for_save(config)
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    tenant_id = _tenant_id()
    inst_id = _instance_id()
    await _ensure_schema(store)
    now = datetime.utcnow().isoformat()
    await store.execute(
        """
        UPDATE agent_configs
        SET config_json = ?, updated_at = ?
        WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND version = ?
        """,
        (json.dumps(config), now, tenant_id, inst_id, base_agent_id, version),
    )
    await store.commit()
    return getattr(store, "_last_rowcount", 0) > 0


async def load_config(
    version: str | None = None, agent_id: str = "cuga-default"
) -> tuple[dict[str, Any] | None, str | None]:
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    tenant_id = _tenant_id()
    inst_id = _instance_id()
    await _ensure_schema(store)
    if version is not None and version != "draft":
        row = await store.fetchone(
            "SELECT config_json, version FROM agent_configs WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND version = ?",
            (tenant_id, inst_id, base_agent_id, version),
        )
    else:
        row = await store.fetchone(
            """
            SELECT config_json, version FROM agent_configs
            WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND version != 'draft'
            ORDER BY CAST(version AS INTEGER) DESC LIMIT 1
            """,
            (tenant_id, inst_id, base_agent_id),
        )
    if not row:
        return None, None
    cj = row["config_json"] if isinstance(row, dict) else row[0]
    ver = row["version"] if isinstance(row, dict) else row[1]
    return json.loads(cj), ver


async def list_versions(agent_id: str = "cuga-default") -> list[dict[str, Any]]:
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    tenant_id = _tenant_id()
    inst_id = _instance_id()
    await _ensure_schema(store)
    rows = await store.fetchall(
        """
        SELECT version, created_at FROM agent_configs
        WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND version != 'draft'
        ORDER BY CAST(version AS INTEGER) DESC LIMIT 100
        """,
        (tenant_id, inst_id, base_agent_id),
    )
    return [
        {
            "version": r["version"] if isinstance(r, dict) else r[0],
            "created_at": r["created_at"] if isinstance(r, dict) else r[1],
        }
        for r in rows
    ]


async def get_latest_version(agent_id: str = "cuga-default") -> tuple[str | None, str | None]:
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    tenant_id = _tenant_id()
    inst_id = _instance_id()
    await _ensure_schema(store)
    row = await store.fetchone(
        """
        SELECT version, created_at FROM agent_configs
        WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND version != 'draft'
        ORDER BY CAST(version AS INTEGER) DESC LIMIT 1
        """,
        (tenant_id, inst_id, base_agent_id),
    )
    if not row:
        return None, None
    ver = row["version"] if isinstance(row, dict) else row[0]
    ca = row["created_at"] if isinstance(row, dict) else row[1]
    return ver, ca


async def save_draft(config: dict[str, Any], agent_id: str = "cuga-default") -> None:
    normalize_policies_for_save(config)
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    tenant_id = _tenant_id()
    inst_id = _instance_id()
    await _ensure_schema(store)
    now = datetime.utcnow().isoformat()
    await store.execute(
        """
        INSERT INTO agent_configs (tenant_id, instance_id, agent_id, version, config_json, updated_at)
        VALUES (?, ?, ?, 'draft', ?, ?)
        ON CONFLICT(tenant_id, instance_id, agent_id, version)
        DO UPDATE SET config_json = excluded.config_json, updated_at = excluded.updated_at
        """,
        (tenant_id, inst_id, base_agent_id, json.dumps(config), now),
    )
    await store.commit()


async def load_draft(agent_id: str = "cuga-default") -> dict[str, Any] | None:
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    tenant_id = _tenant_id()
    inst_id = _instance_id()
    await _ensure_schema(store)
    row = await store.fetchone(
        "SELECT config_json FROM agent_configs WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND version = 'draft'",
        (tenant_id, inst_id, base_agent_id),
    )
    if not row:
        return None
    cj = row["config_json"] if isinstance(row, dict) else row[0]
    return json.loads(cj)


async def get_agent_tools(agent_id: str, version: str = "draft") -> list[dict[str, Any]]:
    base_agent_id = _parse_agent_id(agent_id)
    if version == "draft":
        config = await load_draft(base_agent_id)
    else:
        config, _ = await load_config(version, base_agent_id)
    if not config:
        return []
    return config.get("tools", [])


async def list_agents_with_configs() -> list[dict[str, Any]]:
    store = _get_store()
    tenant_id = _tenant_id()
    inst_id = _instance_id()
    await _ensure_schema(store)
    rows = await store.fetchall(
        """
        SELECT DISTINCT agent_id, MAX(updated_at) as last_updated
        FROM agent_configs
        WHERE tenant_id = ? AND instance_id = ?
        GROUP BY agent_id
        ORDER BY agent_id
        """,
        (tenant_id, inst_id),
    )
    return [
        {
            "agent_id": r["agent_id"] if isinstance(r, dict) else r[0],
            "last_updated": r["last_updated"] if isinstance(r, dict) else r[1],
        }
        for r in rows
    ]


async def delete_all_configs(agent_id: str = "cuga-default") -> int:
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    tenant_id = _tenant_id()
    inst_id = _instance_id()
    await _ensure_schema(store)
    await store.execute(
        "DELETE FROM agent_configs WHERE tenant_id = ? AND instance_id = ? AND agent_id = ?",
        (tenant_id, inst_id, base_agent_id),
    )
    await store.commit()
    return getattr(store, "_last_rowcount", 0)


def reset_config_db() -> None:
    from cuga.config import DBS_DIR

    # Close/clear cached relational stores first so we don't leave connections pointing at
    # the deleted file; the next access reopens against the recreated DB.
    get_storage().invalidate_relational_stores()
    path = os.path.join(DBS_DIR, "cuga.db")
    # WAL mode makes this a three-file unit. Dropping only the main DB leaves an
    # orphaned -wal/-shm pair still describing pages the recreated file doesn't
    # have, so the next connection reads past EOF and raises
    # SQLITE_IOERR_SHORT_READ — surfaced as a bare "disk I/O error". A hard kill
    # (crash, SIGKILL, Ctrl-C mid-write) is enough to strand the sidecars.
    for target in (path, f"{path}-wal", f"{path}-shm"):
        if os.path.exists(target):
            os.remove(target)
