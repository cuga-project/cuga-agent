"""Policy and public projections for manual Evolve retention."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_RETENTION_POLICY: dict[str, Any] = {
    "rules": [
        {"name": "orphaned-conversations", "source_deleted": True, "max_age_days": 7, "action": "delete"},
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


class RetentionReportItem(BaseModel):
    """Fields consumed from Evolve's engine and durable collection reports."""

    model_config = ConfigDict(strict=True, extra="ignore")

    entity_id: str
    entity_type: str | None = None
    action: str | None = None
    outcome: str | None = None
    rule: str | None = None
    reason: str | None = None


class RetentionReport(BaseModel):
    """Validate the MCP report once; ignore provider fields we do not expose."""

    model_config = ConfigDict(strict=True, extra="ignore")

    run_id: str | None = None
    policy_id: str | None = None
    policy_name: str | None = None
    initiated_by: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    flagged: list[RetentionReportItem] = Field(default_factory=list)
    deleted: list[RetentionReportItem] = Field(default_factory=list)
    skipped: list[RetentionReportItem] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _safe_report_reason(item: RetentionReportItem, bucket: str) -> str | None:
    rule = item.rule
    reason = item.reason
    if bucket == "skipped":
        if reason == "legal_hold":
            return "Deletion blocked by legal hold."
        if rule == "unused-guidelines" and reason == "unused":
            return "No recorded last-used date was available, so this guideline was kept instead of being deleted."
        if reason == "delete_failed":
            return "The memory could not be deleted, so it was kept."
        return "The retention action could not be applied safely, so this memory was kept."
    if bucket != "deleted":
        return None
    if rule == "unused-guidelines" and reason == "unused":
        return "Deleted because no use was recorded for more than 180 days."
    if rule == "old-sessions" and reason == "age":
        return "Deleted because the source conversation was more than one year old."
    if reason is not None and reason.startswith("cascade:"):
        return "Deleted because it was derived from a conversation deleted by the retention policy."
    if rule == "orphaned-conversations" and reason == "orphaned_conversation":
        return (
            "Deleted because the memory was more than 7 days old and its source conversation was unavailable."
        )
    return "Deleted because it matched a deletion rule in the retention policy."


def sanitize_retention_report(report: dict[str, Any]) -> dict[str, Any]:
    """Validate the wire report and project only content-free audit fields."""
    parsed = RetentionReport.model_validate(report)
    sanitized = parsed.model_dump(
        exclude={"flagged", "deleted", "skipped", "errors", "warnings"}, exclude_unset=True
    )
    sanitized["error_count"] = len(parsed.errors)
    sanitized["warning_count"] = len(parsed.warnings)
    for bucket in ("flagged", "deleted", "skipped"):
        items = []
        for item in getattr(parsed, bucket):
            projected = item.model_dump(exclude={"rule", "reason"}, exclude_unset=True)
            if reason := _safe_report_reason(item, bucket):
                projected["reason"] = reason
            items.append(projected)
        sanitized[bucket] = items
    return sanitized


def project_retention_report(report: dict[str, Any]) -> dict[str, Any]:
    buckets = {
        bucket: [
            {
                key: item[key]
                for key in ("entity_id", "entity_type", "action", "outcome", "reason")
                if key in item
            }
            for item in report.get(bucket, [])
            if isinstance(item, dict)
        ]
        for bucket in ("flagged", "deleted", "skipped")
    }
    return {
        **{
            key: report[key]
            for key in ("run_id", "policy_id", "policy_name", "initiated_by", "started_at", "completed_at")
            if key in report
        },
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


def project_retention_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Project an Evolve policy record without backend or namespace details."""
    definition = policy.get("policy")
    rules = definition.get("rules", []) if isinstance(definition, dict) else []
    projected_rules = [
        {
            key: rule[key]
            for key in (
                "name",
                "entity_type",
                "max_age_days",
                "max_unused_days",
                "action",
                "on_missing_access_signal",
                "cascade_derived",
                "source_deleted",
            )
            if key in rule
        }
        for rule in rules
        if isinstance(rule, dict)
    ]
    return {
        key: policy.get(key)
        for key in ("policy_id", "name", "description", "enabled", "created_at", "updated_at")
    } | {"rules": projected_rules}


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
                "entity_type": rule.get("entity_type"),
                "source_deleted": rule.get("source_deleted", False),
                "action": rule["action"],
                **({"description": rule["description"]} if "description" in rule else {}),
                **(
                    {"max_unused_days": rule["max_unused_days"]}
                    if "max_unused_days" in rule
                    else {"max_age_days": rule["max_age_days"]}
                ),
            }
            for rule in DEFAULT_RETENTION_POLICY["rules"]
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
