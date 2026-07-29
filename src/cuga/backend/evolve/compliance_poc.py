"""Real-data bootstrap and durable simulated compliance ledger for the PoC."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

from cuga.backend.evolve.compliance_poc_sil_fixture import CAPTURED_SIL_CONVERSATIONS
from cuga.backend.evolve.integration import EvolveIntegration
from cuga.backend.server.conversation_history import get_conversation_db
from cuga.backend.storage import get_storage
from cuga.config import get_service_instance_id, get_tenant_id

POC_SEED = "cuga-compliance-poc-2026-07"
POLICY = {
    "rules": [
        {
            "name": "unused-guidelines",
            "entity_type": "guideline",
            "max_unused_days": 180,
            "action": "delete",
            "on_missing_access_signal": "skip",
        },
        {"name": "stale-guidelines", "entity_type": "guideline", "max_age_days": 90, "action": "flag"},
        {
            "name": "old-sessions",
            "entity_type": "trajectory",
            "max_age_days": 365,
            "action": "delete",
            "cascade_derived": True,
        },
    ]
}
SCHEDULER_CONNECTED = False
_REPORT_FIELDS = {
    "as_of",
    "completed_at",
    "dry_run",
    "errors",
    "run_id",
    "started_at",
    "summary",
    "warnings",
}
_REPORT_ITEM_FIELDS = {
    "entity_id",
    "entity_type",
    "action",
    "outcome",
    "rule",
    "reason",
    "detail",
    "created_at",
    "user_id",
    "agent_id",
    "session_id",
    "source_task_id",
}


def _retention_rule_summary(rule: dict[str, Any]) -> tuple[int, str]:
    days = int(rule.get("max_unused_days") or rule.get("max_age_days") or 0)
    entity_type = rule.get("entity_type")
    action = rule.get("action")

    if entity_type == "guideline" and action == "flag" and rule.get("max_age_days"):
        return 0, f"Guidance reviewed after {days} days"
    if entity_type == "guideline" and action == "delete" and rule.get("max_unused_days"):
        return 1, f"Unused guidance deleted after {days} days"
    if entity_type == "trajectory" and action == "delete" and rule.get("max_age_days"):
        duration = "one year" if days == 365 else f"{days} days"
        return 2, f"Conversations deleted after {duration}"
    return 99, f"{entity_type or 'Memory'} {action or 'reviewed'} after {days} days"


async def get_user_retention_summary(agent_id: str) -> dict[str, Any]:
    config = await get_automation_config(agent_id)
    rules = sorted((_retention_rule_summary(rule) for rule in POLICY["rules"]), key=lambda item: item[0])
    scheduled = bool(config.get("retention_enabled")) and SCHEDULER_CONNECTED
    return {
        "rules": [{"summary": summary, "scheduled": scheduled} for _, summary in rules],
    }


def _store():
    return get_storage().get_relational_store("memory_compliance")


def _scope() -> tuple[str, str]:
    return get_tenant_id(), get_service_instance_id()


async def _ensure_schema() -> None:
    store = _store()
    await store.execute(
        "CREATE TABLE IF NOT EXISTS compliance_runs (tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, run_id TEXT NOT NULL, agent_id TEXT NOT NULL, status TEXT NOT NULL, simulated INTEGER NOT NULL, report_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (tenant_id, instance_id, run_id))"
    )
    await store.execute(
        "CREATE TABLE IF NOT EXISTS compliance_events (tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, event_id TEXT NOT NULL, run_id TEXT NOT NULL, agent_id TEXT NOT NULL, event_type TEXT NOT NULL, entity_id TEXT, conversation_id TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (tenant_id, instance_id, event_id))"
    )
    await store.execute(
        "CREATE TABLE IF NOT EXISTS compliance_deliveries (tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, delivery_id TEXT NOT NULL, event_id TEXT NOT NULL, run_id TEXT NOT NULL, agent_id TEXT NOT NULL, status TEXT NOT NULL, simulated INTEGER NOT NULL, delivered_at TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY (tenant_id, instance_id, delivery_id))"
    )
    await store.execute(
        "CREATE TABLE IF NOT EXISTS compliance_automation_config (tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, agent_id TEXT NOT NULL, retention_enabled INTEGER NOT NULL, retention_frequency TEXT NOT NULL, retention_time TEXT NOT NULL, events_enabled INTEGER NOT NULL, event_destination TEXT NOT NULL, event_type TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (tenant_id, instance_id, agent_id))"
    )
    await store.execute(
        "CREATE TABLE IF NOT EXISTS compliance_seed_state (tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, seed_id TEXT NOT NULL, agent_id TEXT NOT NULL, user_id TEXT NOT NULL, completed_at TEXT NOT NULL, PRIMARY KEY (tenant_id, instance_id, seed_id, agent_id, user_id))"
    )
    await store.execute(
        "CREATE TABLE IF NOT EXISTS compliance_requests (tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, request_id TEXT NOT NULL, agent_id TEXT NOT NULL, user_id TEXT NOT NULL, entity_id TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (tenant_id, instance_id, request_id))"
    )
    await store.execute(
        "CREATE TABLE IF NOT EXISTS compliance_memory_usage (tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, usage_id TEXT NOT NULL, turn_id TEXT NOT NULL, agent_id TEXT NOT NULL, user_id TEXT NOT NULL, entity_id TEXT NOT NULL, thread_id TEXT NOT NULL, conversation_label TEXT NOT NULL, purpose TEXT NOT NULL, used_at TEXT NOT NULL, PRIMARY KEY (tenant_id, instance_id, usage_id))"
    )
    try:
        await store.execute(
            "ALTER TABLE compliance_automation_config ADD COLUMN events_enabled INTEGER NOT NULL DEFAULT 1"
        )
    except Exception:
        pass
    try:
        await store.execute(
            "ALTER TABLE compliance_automation_config ADD COLUMN event_type TEXT NOT NULL DEFAULT 'retention.outcome'"
        )
    except Exception:
        pass
    await store.commit()


async def record_memory_usage(
    *,
    turn_id: str,
    agent_id: str,
    user_id: str,
    entity_ids: list[str],
    thread_id: str,
    conversation_label: str,
    purpose: str = "prompt_context",
    used_at: str | None = None,
) -> dict[str, Any]:
    """Append one usage event per memory per turn.

    Deterministic IDs make graph retries idempotent without turning the usage
    count into mutable entity metadata.
    """
    await _ensure_schema()
    unique_ids = [entity_id for entity_id in dict.fromkeys(map(str, entity_ids)) if entity_id]
    moment = used_at or dt.datetime.now(dt.UTC).isoformat()
    tenant_id, instance_id = _scope()
    store = _store()
    recorded_ids: list[str] = []
    for entity_id in unique_ids:
        usage_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{turn_id}:memory:{entity_id}"))
        exists = await store.fetchone(
            "SELECT usage_id FROM compliance_memory_usage WHERE tenant_id = ? AND instance_id = ? AND usage_id = ?",
            (tenant_id, instance_id, usage_id),
        )
        if exists:
            recorded_ids.append(entity_id)
            continue
        await store.execute(
            "INSERT INTO compliance_memory_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tenant_id,
                instance_id,
                usage_id,
                turn_id,
                agent_id,
                user_id,
                entity_id,
                thread_id,
                conversation_label[:120],
                purpose,
                moment,
            ),
        )
        recorded_ids.append(entity_id)
    await store.commit()
    return {
        "turn_id": turn_id,
        "count": len(recorded_ids),
        "entity_ids": recorded_ids,
        "used_at": moment,
    }


async def get_turn_memory_usage(
    *,
    turn_id: str,
    agent_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Return the authenticated turn's prompt-context attribution."""
    await _ensure_schema()
    tenant_id, instance_id = _scope()
    rows = await _store().fetchall(
        "SELECT entity_id, used_at FROM compliance_memory_usage WHERE tenant_id = ? AND instance_id = ? AND turn_id = ? AND agent_id = ? AND user_id = ? ORDER BY entity_id",
        (tenant_id, instance_id, turn_id, agent_id, user_id),
    )
    entity_ids = [str(row["entity_id"]) for row in rows]
    return {
        "turn_id": turn_id,
        "count": len(entity_ids),
        "entity_ids": entity_ids,
        "used_at": rows[-1]["used_at"] if rows else None,
    }


async def get_memory_usage_summaries(
    *,
    agent_id: str,
    entity_ids: list[str],
    user_id: str | None = None,
    recent_limit: int = 3,
) -> dict[str, dict[str, Any]]:
    """Aggregate usage counts and recent linked conversations for inventory rows."""
    await _ensure_schema()
    wanted = set(map(str, entity_ids))
    if not wanted:
        return {}
    tenant_id, instance_id = _scope()
    store = _store()
    if user_id is None:
        rows = await store.fetchall(
            "SELECT entity_id, thread_id, conversation_label, used_at FROM compliance_memory_usage WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? ORDER BY used_at DESC",
            (tenant_id, instance_id, agent_id),
        )
        try:
            conversation_rows = await store.fetchall(
                "SELECT DISTINCT thread_id FROM conversation_history WHERE tenant_id = ? AND instance_id = ? AND agent_id = ?",
                (tenant_id, instance_id, agent_id),
            )
        except Exception:
            conversation_rows = []
    else:
        rows = await store.fetchall(
            "SELECT entity_id, thread_id, conversation_label, used_at FROM compliance_memory_usage WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND user_id = ? ORDER BY used_at DESC",
            (tenant_id, instance_id, agent_id, user_id),
        )
        try:
            conversation_rows = await store.fetchall(
                "SELECT DISTINCT thread_id FROM conversation_history WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND user_id = ?",
                (tenant_id, instance_id, agent_id, user_id),
            )
        except Exception:
            conversation_rows = []
    available_threads = {str(row["thread_id"]) for row in conversation_rows}

    summaries: dict[str, dict[str, Any]] = {}
    for row in rows:
        entity_id = str(row["entity_id"])
        if entity_id not in wanted:
            continue
        summary = summaries.setdefault(
            entity_id,
            {"count": 0, "last_used_at": None, "recent": []},
        )
        summary["count"] += 1
        if summary["last_used_at"] is None:
            summary["last_used_at"] = row["used_at"]
        if str(row["thread_id"]) in available_threads and len(summary["recent"]) < recent_limit:
            summary["recent"].append(
                {
                    "thread_id": row["thread_id"],
                    "conversation_label": row["conversation_label"],
                    "used_at": row["used_at"],
                }
            )
    return summaries


async def get_automation_config(agent_id: str) -> dict[str, Any]:
    await _ensure_schema()
    tenant_id, instance_id = _scope()
    row = await _store().fetchone(
        "SELECT * FROM compliance_automation_config WHERE tenant_id = ? AND instance_id = ? AND agent_id = ?",
        (tenant_id, instance_id, agent_id),
    )
    if row:
        return dict(row)
    return {
        "agent_id": agent_id,
        "retention_enabled": 1,
        "retention_frequency": "Every week",
        "retention_time": "02:00",
        "events_enabled": 1,
        "event_destination": "Simulated event delivery",
        "event_type": "retention.outcome",
    }


async def update_automation_config(agent_id: str, values: dict[str, Any]) -> dict[str, Any]:
    await _ensure_schema()
    tenant_id, instance_id = _scope()
    current = await get_automation_config(agent_id)
    merged = {**current, **values}
    now = dt.datetime.now(dt.UTC).isoformat()
    store = _store()
    exists = await store.fetchone(
        "SELECT agent_id FROM compliance_automation_config WHERE tenant_id = ? AND instance_id = ? AND agent_id = ?",
        (tenant_id, instance_id, agent_id),
    )
    params = (
        int(bool(merged.get("retention_enabled", True))),
        str(merged.get("retention_frequency", "Every week")),
        str(merged.get("retention_time", "02:00")),
        int(bool(merged.get("events_enabled", True))),
        str(merged.get("event_destination", "Simulated event delivery")),
        str(merged.get("event_type", "retention.outcome")),
        now,
        tenant_id,
        instance_id,
        agent_id,
    )
    if exists:
        await store.execute(
            "UPDATE compliance_automation_config SET retention_enabled = ?, retention_frequency = ?, retention_time = ?, events_enabled = ?, event_destination = ?, event_type = ?, updated_at = ? WHERE tenant_id = ? AND instance_id = ? AND agent_id = ?",
            params,
        )
    else:
        await store.execute(
            "INSERT INTO compliance_automation_config (retention_enabled, retention_frequency, retention_time, events_enabled, event_destination, event_type, updated_at, tenant_id, instance_id, agent_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            params,
        )
    await store.commit()
    return await get_automation_config(agent_id)


def _thread_id(index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{POC_SEED}:thread:{index}"))


def _conversation_specs() -> list[list[tuple[str, str, int]]]:
    """Return the user-visible messages from the captured SIL trajectories."""
    conversations: list[list[tuple[str, str, int]]] = []
    for capture in CAPTURED_SIL_CONVERSATIONS:
        transcript: list[tuple[str, str, int]] = []
        for turn_index, turn in enumerate(capture["turns"]):
            minute = turn_index * 3
            transcript.extend(
                [
                    ("user", turn["query"], minute),
                    ("assistant", turn["answer"], minute + 1),
                ]
            )
        conversations.append(transcript)
    return conversations


def _conversation_iso(value: dt.datetime) -> str:
    """Match the naive UTC timestamp format used by existing CUGA conversations."""
    return value.astimezone(dt.UTC).replace(tzinfo=None).isoformat()


def _conversation_started(now: dt.datetime, index: int) -> dt.datetime:
    if index >= 8:
        return now - dt.timedelta(hours=1)
    return now - dt.timedelta(days=30 + index * 42)


def sanitize_retention_report(report: dict[str, Any]) -> dict[str, Any]:
    sanitized = {key: report[key] for key in _REPORT_FIELDS if key in report}
    for bucket in ("flagged", "deleted", "skipped"):
        sanitized[bucket] = [
            {key: value for key, value in item.items() if key in _REPORT_ITEM_FIELDS}
            for item in report.get(bucket, [])
            if isinstance(item, dict)
        ]
    return sanitized


def project_retention_report(report: dict[str, Any]) -> dict[str, Any]:
    buckets = {
        bucket: [
            {
                key: item[key]
                for key in ("entity_id", "action", "outcome")
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
            for key in ("run_id", "completed_at", "dry_run")
            if key in report
        },
        **buckets,
        "summary": (
            f"Retention evaluation found {len(buckets['flagged'])} for review, "
            f"{len(buckets['deleted'])} deletion matches, and "
            f"{len(buckets['skipped'])} kept because evidence was incomplete."
        ),
        "errors": (
            ["One or more memories could not be evaluated."] if report.get("errors") else []
        ),
        "warnings": (
            ["Some memories were evaluated with incomplete usage data."]
            if report.get("warnings")
            else []
        ),
    }


def _entity_specs(now: dt.datetime, threads: list[str]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        {
            "seed_key": "old-session-t1",
            "type": "trajectory",
            "age": 400,
            "thread": threads[0],
            "trace_id": "T1",
            "last_accessed": 400,
            "content": "Northstar support handoff with Morgan Lee as escalation owner.",
        },
        {
            "seed_key": "old-session-t2",
            "type": "trajectory",
            "age": 500,
            "thread": threads[1],
            "trace_id": "T2",
            "last_accessed": 480,
            "content": "Redwood billing dispute preserved while Legal reviews the matter.",
        },
        {
            "seed_key": "t1-guideline",
            "type": "guideline",
            "age": 220,
            "thread": threads[0],
            "source_task_id": "T1",
            "content": "Keep the support handoff concise and include next steps.",
        },
        {
            "seed_key": "t1-fact",
            "type": "fact",
            "age": 210,
            "thread": threads[0],
            "source_task_id": "T1",
            "content": "The account review happens with the support lead.",
        },
        {
            "seed_key": "t1-preference",
            "type": "guideline",
            "age": 180,
            "thread": threads[0],
            "source_task_id": "T1",
            "content": "The stakeholder prefers a short weekly summary.",
        },
        {
            "seed_key": "t2-legal-hold",
            "type": "guideline",
            "age": 300,
            "thread": threads[1],
            "source_task_id": "T2",
            "legal_hold": True,
            "last_accessed": 300,
            "content": "Archived guidance retained under a legal hold.",
        },
        {
            "seed_key": "unused-guideline",
            "type": "guideline",
            "age": 240,
            "thread": threads[2],
            "source_task_id": "UNUSED",
            "content": "Prefer a weekly project digest.",
            "last_accessed": 190,
        },
        {
            "seed_key": "stale-guideline",
            "type": "guideline",
            "age": 200,
            "thread": threads[3],
            "source_task_id": "STALE",
            "content": "Use a short summary before listing detailed findings.",
            "last_accessed": 30,
        },
        {
            "seed_key": "missing-access",
            "type": "guideline",
            "age": 400,
            "thread": threads[4],
            "source_task_id": "MISSING",
            "content": "This candidate intentionally has no usage signal.",
        },
        {
            "seed_key": "kept-guideline",
            "type": "guideline",
            "age": 30,
            "thread": threads[5],
            "source_task_id": "KEEP",
            "content": "Keep current project updates concise.",
            "last_accessed": 2,
        },
        {
            "seed_key": "kept-fact",
            "type": "fact",
            "age": 10,
            "thread": threads[6],
            "source_task_id": "KEEP",
            "content": "The team reviews ownership on Tuesdays.",
            "last_accessed": 1,
        },
        {
            "seed_key": "kept-policy",
            "type": "policy",
            "age": 95,
            "thread": threads[7],
            "source_task_id": "KEEP",
            "content": "The duty manager decides requests outside the 30-day refund policy.",
            "last_accessed": 40,
        },
        {
            "seed_key": "synthetic-card",
            "type": "fact",
            "age": 160,
            "thread": threads[2],
            "source_task_id": "SYNTHETIC-PII",
            "content": "Synthetic test candidate only: fake card 4111-1111-1111-1111; never use as real payment data.",
            "last_accessed": 3,
        },
        {
            "seed_key": "synthetic-token",
            "type": "guideline",
            "age": 30,
            "thread": threads[5],
            "source_task_id": "SYNTHETIC-SECRET",
            "content": "Synthetic test candidate only: fake token TEST-SECRET-0000; never use as a credential.",
            "last_accessed": 3,
        },
    ]
    core_titles = [
        "Northstar support handoff",
        "Redwood billing dispute",
        "Concise support handoffs",
        "Thursday account review",
        "Weekly summary preference",
        "Redwood legal hold guidance",
        "Weekly project digest",
        "Executive update format",
        "Helios deployment guidance",
        "Concise launch updates",
        "Tuesday ownership review",
        "Refund exception policy",
        "Payment-data redaction test",
        "Credential redaction test",
    ]
    for spec, title in zip(specs, core_titles, strict=True):
        spec["title"] = title

    specs.extend(
        [
            {
                "seed_key": "live-saved-preference",
                "type": "fact",
                "age": 1,
                "thread": threads[9],
                "title": "Customer-impact-first handoffs",
                "category": "preference",
                "content": (
                    "Prefers escalation handoff summaries to begin with customer impact, "
                    "followed by technical details, then owner and next step"
                ),
                "last_accessed": 0,
                "metadata": {
                    "source": "cuga-lite",
                    "key": "escalation_handoff_format",
                    "value": (
                        "begin with customer impact, then technical details, then owner "
                        "and next step"
                    ),
                },
            },
            {
                "seed_key": "live-derived-guideline",
                "type": "guideline",
                "age": 1,
                "thread": threads[9],
                "source_task_id": "demo",
                "title": "Preference acknowledgement",
                "category": "guidance",
                "content": (
                    "When acknowledging user preferences for response formatting, "
                    "restate the requested changes in your own words to confirm "
                    "understanding and commit to future adaptation."
                ),
                "last_accessed": 0,
                "metadata": {
                    "creation_mode": "auto-mcp",
                    "task_description": (
                        "Acknowledge and interpret user feedback to update future "
                        "response patterns"
                    ),
                    "rationale": (
                        "This demonstrates the AI's attentiveness, reassures the user "
                        "their feedback will influence future interactions, and prevents "
                        "misunderstandings."
                    ),
                    "trigger": (
                        "When the user provides explicit feedback or requests changes "
                        "to response formatting."
                    ),
                    "implementation_steps": [
                        "Identify and extract the user's preference or instruction from their feedback.",
                        "Paraphrase the preference to confirm understanding.",
                        "Explicitly state commitment to incorporate the feedback into future responses.",
                    ],
                    "generation_method": "regular",
                    "support": 1,
                },
            },
        ]
    )

    extras = [
        ("Northstar escalation owner", "Morgan Lee owns the Northstar support escalation.", "work"),
        (
            "Northstar open issue",
            "The unresolved Northstar login issue should lead the next handoff.",
            "work",
        ),
        (
            "Northstar action format",
            "Every Northstar handoff action should name an owner and next step.",
            "preference",
        ),
        (
            "Redwood response boundary",
            "Redwood billing material should be reviewed before answering counsel.",
            "guidance",
        ),
        ("Redwood summary privacy", "Redwood summaries should omit customer payment details.", "guidance"),
        (
            "Vendor review checklist",
            "Payment-vendor reviews cover data handling, credentials, incident response, and evidence ownership.",
            "work",
        ),
        (
            "Synthetic-data rule",
            "Security demonstrations should use clearly fake customer and credential values.",
            "guidance",
        ),
        (
            "Atlas decision first",
            "Atlas executive updates should lead with the identity-cutover decision.",
            "preference",
        ),
        ("Atlas completed work", "The Atlas schema migration is complete.", "work"),
        ("Atlas pending work", "Two Atlas validation tasks still need owners.", "work"),
        (
            "Helios usage uncertainty",
            "The old Helios deployment guideline has no reliable usage timestamp.",
            "guidance",
        ),
        (
            "Helios retention caution",
            "Do not automatically delete Helios guidance when usage evidence is incomplete.",
            "guidance",
        ),
        ("Mobile launch health", "Crash-free sessions improved for the mobile launch.", "work"),
        ("Accessibility audit", "The mobile accessibility audit passed.", "work"),
        ("Release approval", "Mobile release approval is the remaining open decision.", "work"),
        ("Ownership cadence", "The team reviews ownership every Tuesday.", "work"),
        ("Ownership facilitator", "Priya facilitates the Tuesday ownership review.", "work"),
        ("Risk review handoff", "Unresolved ownership items move to Thursday risk review.", "guidance"),
        (
            "Refund documentation",
            "Support documents the reason and purchase date for refund exceptions.",
            "guidance",
        ),
        (
            "Refund decision owner",
            "The duty manager decides requests outside the 30-day refund policy.",
            "work",
        ),
        (
            "No refund promises",
            "Support should not promise an exception before duty-manager approval.",
            "guidance",
        ),
        ("Weekly summary length", "Weekly support summaries should stay under five bullets.", "preference"),
        (
            "Executive summary order",
            "Executive updates should give a short summary before detailed findings.",
            "preference",
        ),
        (
            "Action ownership",
            "Open action items should identify one directly responsible person.",
            "guidance",
        ),
        (
            "Customer-safe summaries",
            "Customer summaries should describe outcomes without exposing payment data.",
            "guidance",
        ),
        (
            "Demo credential safety",
            "Synthetic credential examples must never be treated as working secrets.",
            "guidance",
        ),
        (
            "Exception decision boundary",
            "Saved policy guidance should state when a human decision is still required.",
            "guidance",
        ),
    ]
    for index, (title, content, category) in enumerate(extras):
        specs.append(
            {
                "seed_key": f"extra-{index:02d}",
                "type": "guideline" if category in {"guidance", "preference"} else "fact",
                "age": 15 + index * 3,
                "thread": threads[index % len(threads)],
                "source_task_id": f"EXTRA-{index // 4}",
                "title": title,
                "category": category,
                "content": content,
                "last_accessed": 4 + index % 20,
            }
        )
    return specs


async def _save_demo_conversations(
    *,
    agent_id: str,
    user_id: str,
    threads: list[str],
    now: dt.datetime,
) -> None:
    db = get_conversation_db()
    for index, (thread_id, transcript) in enumerate(
        zip(threads, _conversation_specs(), strict=True)
    ):
        started = _conversation_started(now, index)
        messages = [
            {
                "role": role,
                "content": content,
                "timestamp": _conversation_iso(started + dt.timedelta(minutes=minute)),
            }
            for role, content, minute in transcript
        ]
        if not await db.save_conversation(agent_id, thread_id, 1, user_id, messages):
            raise RuntimeError(f"Unable to import demonstration conversation {thread_id}")


async def _seed_demo_conversation_evidence(
    *,
    agent_id: str,
    user_id: str,
    entities: list[dict[str, Any]],
    threads: list[str],
) -> dict[str, int]:
    """Create replayable chat disclosures and their matching usage evidence."""
    transcripts = _conversation_specs()
    db = get_conversation_db()
    store = _store()
    tenant_id, instance_id = _scope()
    await store.execute(
        "DELETE FROM compliance_memory_usage WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND user_id = ? AND purpose IN (?, ?)",
        (
            tenant_id,
            instance_id,
            agent_id,
            user_id,
            "imported_conversation_context",
            "demo_conversation_context",
        ),
    )
    recorded_usage = 0
    answer_count = 0
    now = dt.datetime.now(dt.UTC)
    eligible_by_thread: dict[str, list[str]] = {thread_id: [] for thread_id in threads}
    entity_id_by_seed_key: dict[str, str] = {}
    for entity in entities:
        metadata = entity.get("metadata") or {}
        entity_id = str(entity.get("id") or "")
        thread_id = str(metadata.get("session_id") or metadata.get("thread_id") or "")
        seed_key = str(metadata.get("seed_key") or "")
        if entity_id and seed_key:
            entity_id_by_seed_key[seed_key] = entity_id
        if (
            entity_id
            and thread_id in eligible_by_thread
            and seed_key not in {"missing-access", "unused-guideline"}
        ):
            eligible_by_thread[thread_id].append(entity_id)

    for conversation_index, (thread_id, transcript) in enumerate(
        zip(threads, transcripts, strict=True)
    ):
        started = _conversation_started(now, conversation_index)
        candidates = eligible_by_thread[thread_id]
        events: list[dict[str, Any]] = []
        assistant_index = 0
        for role, content, minute in transcript:
            timestamp = _conversation_iso(started + dt.timedelta(minutes=minute))
            if role == "user":
                events.append(
                    {
                        "event_name": "UserMessage",
                        "event_data": content,
                        "timestamp": timestamp,
                        "sequence": len(events),
                    }
                )
                continue

            captured_turn = CAPTURED_SIL_CONVERSATIONS[conversation_index]["turns"][
                assistant_index
            ]
            explicit_seed_keys = captured_turn["memory_seed_keys"]
            disclosure_ids = [
                entity_id_by_seed_key[seed_key]
                for seed_key in explicit_seed_keys
                if seed_key in entity_id_by_seed_key
            ]
            if not disclosure_ids and candidates:
                disclosure_ids = [
                    candidates[(assistant_index + offset) % len(candidates)]
                    for offset in range(min(3, len(candidates)))
                ]
            turn_id = f"{POC_SEED}:conversation:{conversation_index}:answer:{assistant_index}"
            usage = await record_memory_usage(
                turn_id=turn_id,
                agent_id=agent_id,
                user_id=user_id,
                entity_ids=disclosure_ids,
                thread_id=thread_id,
                conversation_label=transcript[0][1],
                purpose="demo_conversation_context",
                used_at=(started + dt.timedelta(minutes=minute)).isoformat(),
            )
            answer_payload: dict[str, Any] = {
                "data": content,
                "variables": {},
                "active_policies": [],
            }
            if usage["count"]:
                answer_payload["memory_usage"] = usage
            saved_ids = [
                entity_id_by_seed_key[seed_key]
                for seed_key in captured_turn.get("memory_saved_seed_keys", [])
                if seed_key in entity_id_by_seed_key
            ]
            if saved_ids:
                answer_payload["memory_saved"] = {
                    "turn_id": turn_id,
                    "count": len(saved_ids),
                    "entity_ids": saved_ids,
                }
            for detail_event in captured_turn["detail_events"]:
                events.append(
                    {
                        "event_name": detail_event["event_name"],
                        "event_data": detail_event["event_data"],
                        "timestamp": timestamp,
                        "sequence": len(events),
                    }
                )
            events.append(
                {
                    "event_name": "Answer",
                    "event_data": json.dumps(answer_payload),
                    "timestamp": timestamp,
                    "sequence": len(events),
                }
            )
            recorded_usage += usage["count"]
            answer_count += 1
            assistant_index += 1

        await store.execute(
            "DELETE FROM stream_events WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND user_id = ?",
            (tenant_id, instance_id, agent_id, thread_id, user_id),
        )
        await store.commit()
        if not await db.save_stream_events(agent_id, thread_id, user_id, events):
            raise RuntimeError(f"Unable to import demonstration events for {thread_id}")
    await store.commit()
    return {
        "answer_count": answer_count,
        "usage_count": recorded_usage,
    }


async def bootstrap(
    agent_id: str,
    user_id: str,
    namespace_id: str | None,
    user_name: str = "Demo user",
) -> dict[str, Any]:
    await _ensure_schema()
    tenant_id, instance_id = _scope()
    store = _store()
    completed = await store.fetchone(
        "SELECT completed_at FROM compliance_seed_state WHERE tenant_id = ? AND instance_id = ? AND seed_id = ? AND agent_id = ? AND user_id = ?",
        (tenant_id, instance_id, POC_SEED, agent_id, user_id),
    )
    now = dt.datetime.now(dt.UTC)
    threads = [_thread_id(index) for index in range(len(_conversation_specs()))]
    await _save_demo_conversations(
        agent_id=agent_id,
        user_id=user_id,
        threads=threads,
        now=now,
    )
    if completed:
        inventory = await EvolveIntegration.list_entities(
            metadata_filters={"poc_seed_id": POC_SEED, "agent_id": agent_id, "user_id": user_id},
            limit=200,
            include_content=False,
            record_access=False,
            namespace_id=namespace_id,
        )
        items = inventory.get("items", []) if isinstance(inventory, dict) else []
        entity_ids = [item["id"] for item in items if item.get("id")]
        demo_evidence = await _seed_demo_conversation_evidence(
            agent_id=agent_id,
            user_id=user_id,
            entities=items,
            threads=threads,
        )
        return {
            "seed_id": POC_SEED,
            "agent_id": agent_id,
            "namespace_id": namespace_id,
            "conversation_ids": threads,
            "entity_ids": entity_ids,
            "created_entities": 0,
            "memory_count": len(items),
            "seeded_answer_count": demo_evidence["answer_count"],
            "usage_count": demo_evidence["usage_count"],
            "already_completed": True,
            "protection_status": await EvolveIntegration.get_compliance_status(namespace_id=namespace_id),
            "synthetic_values_are_fake": True,
        }
    inventory = await EvolveIntegration.list_entities(
        metadata_filters={"poc_seed_id": POC_SEED, "agent_id": agent_id, "user_id": user_id},
        limit=200,
        include_content=False,
        record_access=False,
        namespace_id=namespace_id,
    )
    if inventory is None:
        raise RuntimeError("Evolve memory service is unavailable")
    existing = {
        (item.get("metadata") or {}).get("seed_key"): item.get("id")
        for item in ((inventory or {}).get("items", []) if isinstance(inventory, dict) else [])
    }
    entity_ids = [value for value in existing.values() if value]
    usage_entities = [
        item
        for item in ((inventory or {}).get("items", []) if isinstance(inventory, dict) else [])
        if item.get("id")
    ]
    created = 0
    for spec in _entity_specs(now, threads):
        if spec["seed_key"] in existing:
            continue
        metadata: dict[str, Any] = {
            **spec.get("metadata", {}),
            "poc_seed_id": POC_SEED,
            "seed_key": spec["seed_key"],
            "agent_id": agent_id,
            "user_id": user_id,
            "user_name": user_name,
            "session_id": spec["thread"],
            "thread_id": spec["thread"],
            "title": spec.get("title") or spec["seed_key"].replace("-", " ").title(),
            "category": spec.get("category") or ("preference" if spec["type"] == "guideline" else "work"),
            "legal_hold": bool(spec.get("legal_hold")),
        }
        if spec.get("trace_id"):
            metadata["trace_id"] = spec["trace_id"]
        if spec.get("source_task_id"):
            metadata["source_task_id"] = spec["source_task_id"]
        if "last_accessed" in spec:
            metadata["last_accessed"] = (now - dt.timedelta(days=spec["last_accessed"])).isoformat()
        result = await EvolveIntegration.create_entity(
            content=spec["content"],
            entity_type=spec["type"],
            metadata=metadata,
            owner_id=user_id,
            namespace_id=namespace_id,
            created_at=(now - dt.timedelta(days=spec["age"])).isoformat(),
        )
        if isinstance(result, dict) and result.get("id"):
            entity_ids.append(result["id"])
            usage_entities.append(
                {
                    "id": result["id"],
                    "metadata": {
                        "seed_key": spec["seed_key"],
                        "session_id": spec["thread"],
                    },
                }
            )
            created += 1
        else:
            raise RuntimeError(f"Evolve could not import demonstration memory {spec['seed_key']}")
    status = await EvolveIntegration.get_compliance_status(namespace_id=namespace_id)
    demo_evidence = await _seed_demo_conversation_evidence(
        agent_id=agent_id,
        user_id=user_id,
        entities=usage_entities,
        threads=threads,
    )
    await store.execute(
        "INSERT INTO compliance_seed_state VALUES (?, ?, ?, ?, ?, ?)",
        (tenant_id, instance_id, POC_SEED, agent_id, user_id, dt.datetime.now(dt.UTC).isoformat()),
    )
    await store.commit()
    return {
        "seed_id": POC_SEED,
        "agent_id": agent_id,
        "namespace_id": namespace_id,
        "conversation_ids": threads,
        "entity_ids": entity_ids,
        "created_entities": created,
        "memory_count": len(entity_ids),
        "seeded_answer_count": demo_evidence["answer_count"],
        "usage_count": demo_evidence["usage_count"],
        "protection_status": status,
        "synthetic_values_are_fake": True,
    }


async def run_simulated_schedule(
    agent_id: str,
    namespace_id: str | None,
    user_id: str | None = None,
    *,
    dry_run: bool = True,
    as_of: str | None = None,
) -> dict[str, Any]:
    await _ensure_schema()
    config = await get_automation_config(agent_id)
    if not config.get("retention_enabled", 1):
        raise ValueError("Retention scheduling is disabled for this agent")
    run_id = str(uuid.uuid4())
    metadata_filters = {"agent_id": agent_id}
    if user_id:
        metadata_filters["user_id"] = user_id
    report = await EvolveIntegration.run_retention(
        POLICY,
        dry_run=dry_run,
        as_of=as_of,
        run_id=run_id,
        namespace_id=namespace_id,
        metadata_filters=metadata_filters,
    )
    if not isinstance(report, dict) or report.get("error"):
        raise RuntimeError("Evolve retention service is unavailable")
    report = sanitize_retention_report(report)
    report["trigger"] = "scheduled (simulated)"
    report["scheduled_for"] = (
        as_of or f"{dt.datetime.now(dt.UTC).date().isoformat()}T{config['retention_time']}:00Z"
    )
    report["destination"] = config["event_destination"]
    report["event_type"] = config["event_type"]
    report["events_enabled"] = bool(config.get("events_enabled", 1))
    run_id = str(report.get("run_id") or run_id)
    tenant_id, instance_id = _scope()
    now = dt.datetime.now(dt.UTC).isoformat()
    store = _store()
    exists = await store.fetchone(
        "SELECT run_id FROM compliance_runs WHERE tenant_id = ? AND instance_id = ? AND run_id = ?",
        (tenant_id, instance_id, run_id),
    )
    values = (tenant_id, instance_id, run_id, agent_id, "completed", 1, json.dumps(report), now)
    if exists:
        await store.execute(
            "UPDATE compliance_runs SET status = ?, simulated = ?, report_json = ?, created_at = ? WHERE tenant_id = ? AND instance_id = ? AND run_id = ?",
            ("completed", 1, json.dumps(report), now, tenant_id, instance_id, run_id),
        )
    else:
        await store.execute("INSERT INTO compliance_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values)
    filters = {"agent_id": agent_id}
    if user_id:
        filters["user_id"] = user_id
    inventory = await EvolveIntegration.list_entities(
        metadata_filters=filters,
        limit=200,
        include_content=False,
        record_access=False,
        namespace_id=namespace_id,
    )
    by_id = {
        str(item.get("id")): item
        for item in ((inventory or {}).get("items", []) if isinstance(inventory, dict) else [])
    }
    outcomes = [
        *(report.get("flagged") or []),
        *(report.get("deleted") or []),
        *(report.get("skipped") or []),
    ]
    for index, outcome in enumerate(outcomes):
        entity_id = str(outcome.get("entity_id") or "")
        metadata = (by_id.get(entity_id) or {}).get("metadata") or {}
        conversation_id = metadata.get("session_id") or metadata.get("thread_id")
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:event:{index}:{entity_id}"))
        payload = {
            "simulated": True,
            "run_id": run_id,
            "entity_id": entity_id,
            "conversation_id": conversation_id,
            "action": outcome.get("action"),
            "rule": outcome.get("rule"),
            "outcome": outcome.get("outcome"),
            "event_type": config["event_type"],
            "destination": config["event_destination"],
        }
        event_exists = await store.fetchone(
            "SELECT event_id FROM compliance_events WHERE tenant_id = ? AND instance_id = ? AND event_id = ?",
            (tenant_id, instance_id, event_id),
        )
        if not event_exists:
            await store.execute(
                "INSERT INTO compliance_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant_id,
                    instance_id,
                    event_id,
                    run_id,
                    agent_id,
                    "retention.outcome",
                    entity_id,
                    conversation_id,
                    json.dumps(payload),
                    now,
                ),
            )
        if config.get("events_enabled", 1):
            delivery_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event_id}:delivery"))
            delivery_exists = await store.fetchone(
                "SELECT delivery_id FROM compliance_deliveries WHERE tenant_id = ? AND instance_id = ? AND delivery_id = ?",
                (tenant_id, instance_id, delivery_id),
            )
            if not delivery_exists:
                await store.execute(
                    "INSERT INTO compliance_deliveries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        tenant_id,
                        instance_id,
                        delivery_id,
                        event_id,
                        run_id,
                        agent_id,
                        "simulated-delivered",
                        1,
                        now,
                        json.dumps(
                            {
                                "simulated": True,
                                "event_id": event_id,
                                "event_type": config["event_type"],
                                "destination": config["event_destination"],
                                "run_id": run_id,
                                "entity_id": entity_id,
                                "conversation_id": conversation_id,
                            }
                        ),
                    ),
                )
    await store.commit()
    return report


async def record_user_request(
    agent_id: str,
    user_id: str,
    entity_id: str,
    action: str,
    status: str,
) -> dict[str, Any]:
    await _ensure_schema()
    tenant_id, instance_id = _scope()
    item = {
        "record_type": "user_request",
        "request_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "user_id": user_id,
        "entity_id": entity_id,
        "action": action,
        "status": status,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    await _store().execute(
        "INSERT INTO compliance_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            tenant_id,
            instance_id,
            item["request_id"],
            agent_id,
            user_id,
            entity_id,
            action,
            status,
            item["created_at"],
        ),
    )
    await _store().commit()
    return item


async def list_ledger(kind: str, agent_id: str, limit: int = 100) -> list[dict[str, Any]]:
    await _ensure_schema()
    tenant_id, instance_id = _scope()
    store = _store()
    if kind == "activity":
        rows = await store.fetchall(
            "SELECT * FROM compliance_runs WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? ORDER BY created_at DESC LIMIT ?",
            (tenant_id, instance_id, agent_id, limit),
        )
        result = []
        for row in rows:
            report = project_retention_report(json.loads(row["report_json"]))
            outcomes = [
                *(report.get("flagged") or []),
                *(report.get("deleted") or []),
                *(report.get("skipped") or []),
            ]
            result.append(
                {
                    "record_type": "retention_run",
                    "run_id": row["run_id"],
                    "status": row["status"],
                    "simulated": row["simulated"],
                    "created_at": row["created_at"],
                    "report": report,
                    "affected_entity_ids": [
                        entry.get("entity_id") for entry in outcomes if entry.get("entity_id")
                    ],
                }
            )
        requests = await store.fetchall(
            "SELECT * FROM compliance_requests WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? ORDER BY created_at DESC LIMIT ?",
            (tenant_id, instance_id, agent_id, limit),
        )
        result.extend(
            {
                "record_type": "user_request",
                "request_id": row["request_id"],
                "entity_id": row["entity_id"],
                "action": row["action"],
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in requests
        )
        return sorted(result, key=lambda item: item["created_at"], reverse=True)[:limit]
    rows = await store.fetchall(
        "SELECT * FROM compliance_deliveries WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? ORDER BY delivered_at DESC LIMIT ?",
        (tenant_id, instance_id, agent_id, limit),
    )
    return [
        {
            "delivery_id": row["delivery_id"],
            "event_id": row["event_id"],
            "run_id": row["run_id"],
            "status": row["status"],
            "simulated": row["simulated"],
            "delivered_at": row["delivered_at"],
            "payload": {
                key: value
                for key, value in json.loads(row["payload_json"]).items()
                if key
                in {
                    "conversation_id",
                    "destination",
                    "entity_id",
                    "event_id",
                    "event_type",
                    "run_id",
                    "simulated",
                }
            },
        }
        for row in rows
    ]
