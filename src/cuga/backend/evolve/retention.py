"""CUGA default policy and capability projections for Evolve retention."""

from __future__ import annotations

from typing import Any
from copy import deepcopy


DEFAULT_RETENTION_POLICY: dict[str, Any] = {
    "rules": [
        {
            "name": "orphaned-conversations",
            "source_deleted": True,
            "min_source_deleted_days": 7,
            "action": "delete",
        },
        {
            "name": "unused-guidelines",
            "entity_type": "guideline",
            "max_unused_days": 180,
            "action": "delete",
            "on_missing_access_signal": "skip",
        },
        {
            "name": "stale-guidelines",
            "entity_type": "guideline",
            "max_age_days": 90,
            "action": "flag",
        },
        {
            "name": "old-sessions",
            "entity_type": "trajectory",
            "max_age_days": 365,
            "action": "delete",
            "cascade_derived": True,
        },
    ]
}
DEFAULT_RETENTION_POLICY_ID = "cuga-standard"
DEFAULT_RETENTION_POLICY_NAME = "Standard retention"
DEFAULT_RETENTION_POLICY_DESCRIPTION = "CUGA's default memory lifecycle policy"


def supports_durable_retention(status: dict[str, Any]) -> bool:
    """Evolve 1.2 implements collection and source receipts only on PostgreSQL."""
    return status.get("backend") == "postgres" and bool(status.get("retention_available"))


def default_retention_policy(status: dict[str, Any]) -> dict[str, Any]:
    policy = deepcopy(DEFAULT_RETENTION_POLICY)
    if not supports_durable_retention(status):
        policy["rules"] = [rule for rule in policy["rules"] if not rule.get("source_deleted")]
    return policy


def retention_capabilities(status: dict[str, Any]) -> dict[str, Any]:
    retention_available = bool(status.get("retention_available"))
    return {
        "retention_available": retention_available,
        "scheduling_supported": retention_available,
        "mark_sweep_supported": supports_durable_retention(status),
        "source_deletion_supported": supports_durable_retention(status),
        "schedule": {
            "state": "managed_by_evolve",
            "label": "Schedules are stored and executed by Evolve.",
        },
        "rules": [
            {
                "name": rule["name"],
                "entity_type": rule.get("entity_type"),
                "source_deleted": rule.get("source_deleted", False),
                "action": rule["action"],
                **({"description": rule["description"]} if "description" in rule else {}),
                **{
                    key: rule[key]
                    for key in ("max_age_days", "max_unused_days", "min_source_deleted_days")
                    if key in rule
                },
            }
            for rule in default_retention_policy(status)["rules"]
        ],
    }


def project_compliance_status(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "healthy": bool(result.get("healthy")),
        "evolve_version": result.get("evolve_version"),
        "backend": result.get("backend"),
        "retention_available": bool(result.get("retention_available")),
        "scheduling_supported": bool(result.get("retention_available")),
        "mark_sweep_supported": supports_durable_retention(result),
        "source_deletion_supported": supports_durable_retention(result),
        "plugins": [
            {key: plugin.get(key) for key in ("name", "protection_class", "hooks", "enabled", "healthy")}
            for plugin in result.get("plugins", [])
            if isinstance(plugin, dict)
        ],
    }
