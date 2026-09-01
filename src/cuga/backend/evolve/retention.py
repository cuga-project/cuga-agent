"""Policy and public projections for manual Evolve retention."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_RETENTION_POLICY: dict[str, Any] = {
    "rules": [
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

ORPHANED_CONVERSATION_GRACE_DAYS = 7
ORPHANED_CONVERSATION_RULE: dict[str, Any] = {
    "name": "orphaned-conversations",
    "entity_type": "memory",
    "action": "delete",
    "max_age_days": ORPHANED_CONVERSATION_GRACE_DAYS,
    "description": ("Delete memories whose source conversation remains unavailable after 7 days"),
}

_REPORT_FIELDS = {
    "as_of",
    "completed_at",
    "run_id",
    "started_at",
}
_REPORT_ITEM_FIELDS = {
    "action",
    "created_at",
    "entity_id",
    "entity_type",
    "outcome",
}


def _metadata(entity: dict[str, Any]) -> dict[str, Any]:
    value = entity.get("metadata")
    return value if isinstance(value, dict) else {}


def _string_value(*values: Any) -> str | None:
    return next((value.strip() for value in values if isinstance(value, str) and value.strip()), None)


def _created_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def retention_reference_time(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = _created_at(value)
    if parsed is None:
        raise ValueError("as_of must be an ISO-8601 timestamp")
    return parsed


def memory_title(entity: dict[str, Any]) -> str | None:
    """Return a short, non-content label suitable for persisted retention reports."""
    metadata = _metadata(entity)
    title = _string_value(entity.get("title"), metadata.get("title"), metadata.get("display_name"))
    return title[:200] if title else None


def find_orphaned_memory_entities(
    entities: list[dict[str, Any]],
    conversation_keys: set[tuple[str, str]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Find old memories that cannot resolve to a scoped CUGA conversation."""
    effective_now = now or datetime.now(timezone.utc)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=timezone.utc)
    else:
        effective_now = effective_now.astimezone(timezone.utc)
    cutoff = effective_now - timedelta(days=ORPHANED_CONVERSATION_GRACE_DAYS)

    trajectory_sources: dict[str, set[str]] = {}
    for entity in entities:
        if entity.get("type") != "trajectory":
            continue
        metadata = _metadata(entity)
        source = _string_value(
            metadata.get("thread_id"),
            metadata.get("session_id"),
            entity.get("session_id"),
        )
        if not source:
            continue
        for key in (
            entity.get("id"),
            entity.get("task_id"),
            metadata.get("task_id"),
            metadata.get("trace_id"),
        ):
            task_id = _string_value(key)
            if task_id:
                trajectory_sources.setdefault(task_id, set()).add(source)

    threads = {thread_id for thread_id, _ in conversation_keys}
    orphaned = []
    for entity in entities:
        if entity.get("type") == "trajectory":
            continue
        metadata = _metadata(entity)
        if metadata.get("legal_hold") is True:
            continue
        created_at = _created_at(entity.get("created_at"))
        if created_at is None or created_at > cutoff:
            continue

        sources = {
            source
            for source in (
                _string_value(metadata.get("thread_id")),
                _string_value(metadata.get("session_id"), entity.get("session_id")),
            )
            if source
        }
        source_task_id = _string_value(entity.get("source_task_id"), metadata.get("source_task_id"))
        if source_task_id:
            sources.update(trajectory_sources.get(source_task_id, set()))

        owner_id = _string_value(
            entity.get("user_id"),
            metadata.get("user_id"),
            metadata.get("owner_id"),
        )
        associated = (
            any((source, owner_id) in conversation_keys for source in sources)
            if owner_id
            else any(source in threads for source in sources)
        )
        if not associated:
            orphaned.append(entity)
    return orphaned


def sanitize_retention_report(report: dict[str, Any]) -> dict[str, Any]:
    """Remove memory content, ownership data, policy internals, and provider details."""
    sanitized = {key: report[key] for key in _REPORT_FIELDS if key in report}
    errors = report.get("errors")
    warnings = report.get("warnings")
    sanitized["error_count"] = len(errors) if isinstance(errors, list) else int(bool(errors))
    sanitized["warning_count"] = len(warnings) if isinstance(warnings, list) else int(bool(warnings))
    for bucket in ("flagged", "deleted", "skipped"):
        sanitized[bucket] = [
            (
                {
                    key: value
                    for key, value in item.items()
                    if key in _REPORT_ITEM_FIELDS and isinstance(value, (str, int, float, bool, type(None)))
                }
                | ({"title": title} if (title := memory_title(item)) else {})
            )
            for item in report.get(bucket, [])
            if isinstance(item, dict)
        ]
    return sanitized


def project_retention_report(report: dict[str, Any]) -> dict[str, Any]:
    buckets = {
        bucket: [
            {
                key: item[key]
                for key in ("entity_id", "entity_type", "action", "outcome", "title")
                if key in item
            }
            for item in report.get(bucket, [])
            if isinstance(item, dict)
        ]
        for bucket in ("flagged", "deleted", "skipped")
    }
    return {
        **{key: report[key] for key in ("run_id", "started_at", "completed_at") if key in report},
        **buckets,
        "summary": (
            f"Retention flagged {len(buckets['flagged'])} for review, "
            f"deleted {len(buckets['deleted'])}, and "
            f"{len(buckets['skipped'])} skipped."
        ),
        "errors": ["One or more memories could not be evaluated."] if report.get("error_count") else [],
        "warnings": ["Some memories were evaluated with incomplete usage data."]
        if report.get("warning_count")
        else [],
    }


def retention_capabilities(*, retention_available: bool) -> dict[str, Any]:
    return {
        "retention_available": retention_available,
        "scheduling_supported": False,
        "schedule": {
            "state": "unavailable",
            "label": "Scheduled retention is unavailable",
        },
        "rules": [
            {
                "name": rule["name"],
                "entity_type": rule["entity_type"],
                "action": rule["action"],
                **({"description": rule["description"]} if "description" in rule else {}),
                **(
                    {"max_unused_days": rule["max_unused_days"]}
                    if "max_unused_days" in rule
                    else {"max_age_days": rule["max_age_days"]}
                ),
            }
            for rule in [*DEFAULT_RETENTION_POLICY["rules"], ORPHANED_CONVERSATION_RULE]
        ],
    }


def project_compliance_status(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "healthy": bool(result.get("healthy")),
        "evolve_version": result.get("evolve_version"),
        "backend": result.get("backend"),
        "retention_available": bool(result.get("retention_available")),
        "scheduling_supported": False,
        "plugins": [
            {key: plugin.get(key) for key in ("name", "protection_class", "hooks", "enabled", "healthy")}
            for plugin in result.get("plugins", [])
            if isinstance(plugin, dict)
        ],
    }
