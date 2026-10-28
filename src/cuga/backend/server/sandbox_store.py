"""Persisted code-sandbox choice made in the Manage UI.

Independent of agent configs: one row per tenant+instance, no draft/publish
versions. Holds only the on/off choice; the connection comes from the mounted
sandbox secret or the settings.
"""

import json
from datetime import datetime
from typing import Any

from cuga.backend.storage import get_storage
from cuga.config import get_service_instance_id, get_tenant_id


def _get_store():
    return get_storage().get_relational_store("config")


async def _ensure_schema(store) -> None:
    ts_default = (
        "CURRENT_TIMESTAMP::text" if type(store).__name__ == "ProdRelationalStore" else "datetime('now')"
    )
    await store.execute(
        f"""
        CREATE TABLE IF NOT EXISTS sandbox_settings (
            tenant_id TEXT NOT NULL DEFAULT '',
            instance_id TEXT NOT NULL DEFAULT '',
            config_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT ({ts_default}),
            PRIMARY KEY (tenant_id, instance_id)
        )
        """
    )
    await store.commit()


async def load_sandbox_settings() -> dict[str, Any] | None:
    store = _get_store()
    await _ensure_schema(store)
    row = await store.fetchone(
        "SELECT config_json FROM sandbox_settings WHERE tenant_id = ? AND instance_id = ?",
        (get_tenant_id(), get_service_instance_id()),
    )
    if not row:
        return None
    cj = row["config_json"] if isinstance(row, dict) else row[0]
    return json.loads(cj)


async def save_sandbox_settings(config: dict[str, Any]) -> None:
    store = _get_store()
    await _ensure_schema(store)
    await store.execute(
        """
        INSERT INTO sandbox_settings (tenant_id, instance_id, config_json, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(tenant_id, instance_id)
        DO UPDATE SET config_json = excluded.config_json, updated_at = excluded.updated_at
        """,
        (get_tenant_id(), get_service_instance_id(), json.dumps(config), datetime.utcnow().isoformat()),
    )
    await store.commit()


async def delete_sandbox_settings() -> None:
    store = _get_store()
    await _ensure_schema(store)
    await store.execute(
        "DELETE FROM sandbox_settings WHERE tenant_id = ? AND instance_id = ?",
        (get_tenant_id(), get_service_instance_id()),
    )
    await store.commit()
