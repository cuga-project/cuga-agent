"""Persisted memory preferences, shared by replicas of a service instance."""

from cuga.backend.storage import get_storage
from cuga.config import get_service_instance_id, get_tenant_id, settings
from loguru import logger


async def _store():
    store = get_storage().get_relational_store("evolve_memory")
    await store.execute(
        "CREATE TABLE IF NOT EXISTS evolve_memory_preferences ("
        "tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, "
        "subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL, enabled INTEGER NOT NULL, "
        "PRIMARY KEY (tenant_id, instance_id, subject_kind, subject_id))"
    )
    await store.commit()
    return store


async def get_preferences(user_id: str) -> dict:
    store = await _store()
    rows = await store.fetchall(
        "SELECT subject_kind, enabled FROM evolve_memory_preferences "
        "WHERE tenant_id = ? AND instance_id = ? AND "
        "((subject_kind = 'instance' AND subject_id = '') OR "
        "(subject_kind = 'user' AND subject_id = ?))",
        (get_tenant_id(), get_service_instance_id(), user_id),
    )
    values = {row["subject_kind"]: bool(row["enabled"]) for row in rows}
    operator_default = bool(settings.evolve.enabled)
    instance_enabled = values.get("instance", operator_default)
    user_enabled = values.get("user", True)
    return {
        "operator_default": operator_default,
        "instance_override": values.get("instance"),
        "instance_enabled": instance_enabled,
        "user_enabled": user_enabled,
        "effective_enabled": instance_enabled and user_enabled,
    }


async def set_preference(*, user_id: str, enabled: bool | None, instance: bool = False) -> dict:
    store = await _store()
    scope = (
        get_tenant_id(),
        get_service_instance_id(),
        "instance" if instance else "user",
        "" if instance else user_id,
    )
    if enabled is None:
        await store.execute(
            "DELETE FROM evolve_memory_preferences WHERE tenant_id = ? AND instance_id = ? "
            "AND subject_kind = ? AND subject_id = ?",
            scope,
        )
    else:
        await store.execute(
            "INSERT INTO evolve_memory_preferences VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (tenant_id, instance_id, subject_kind, subject_id) "
            "DO UPDATE SET enabled = excluded.enabled",
            (*scope, int(enabled)),
        )
    await store.commit()
    return await get_preferences(user_id)


async def memory_enabled(user_id: str | None) -> bool:
    """Read current preferences for each operation; fail closed if storage is unavailable."""
    try:
        return (await get_preferences(user_id or "default_user"))["effective_enabled"]
    except Exception:
        logger.warning("Memory preferences unavailable; skipping automatic memory use")
        return False
