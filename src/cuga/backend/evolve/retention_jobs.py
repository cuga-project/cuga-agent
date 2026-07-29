"""Idempotent retention execution shared by real schedulers and the PoC clock."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from typing import Literal, Protocol

from cuga.backend.evolve.integration import EvolveIntegration


class OccurrenceConflictError(ValueError):
    pass


class OccurrenceInProgressError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class RetentionOccurrence:
    automation_id: str
    occurrence_id: str
    scheduled_for: str
    trigger: Literal["scheduler", "simulation", "run_now"]

    def fingerprint(self) -> str:
        value = json.dumps(dataclasses.asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


class LifecyclePublisher(Protocol):
    async def publish(
        self,
        *,
        agent_id: str,
        run_id: str,
        report: dict,
        config: dict,
        namespace_id: str | None,
        user_id: str | None,
        simulated: bool,
        recorded_at: str,
    ) -> None: ...


class LocalLedgerPublisher:
    """Persist sanitized lifecycle records without claiming external delivery."""

    async def publish(
        self,
        *,
        agent_id: str,
        run_id: str,
        report: dict,
        config: dict,
        namespace_id: str | None,
        user_id: str | None,
        simulated: bool,
        recorded_at: str,
    ) -> None:
        from cuga.backend.evolve import compliance_poc as poc

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
        tenant_id, instance_id = poc._scope()
        store = poc._store()
        for index, outcome in enumerate(outcomes):
            entity_id = str(outcome.get("entity_id") or "")
            metadata = (by_id.get(entity_id) or {}).get("metadata") or {}
            conversation_id = metadata.get("session_id") or metadata.get("thread_id")
            event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:event:{index}:{entity_id}"))
            payload = {
                "simulated": simulated,
                "run_id": run_id,
                "entity_id": entity_id,
                "conversation_id": conversation_id,
                "action": outcome.get("action"),
                "rule": outcome.get("rule"),
                "outcome": outcome.get("outcome"),
                "event_type": config["event_type"],
                "destination": config["event_destination"],
            }
            if not await store.fetchone(
                "SELECT event_id FROM compliance_events WHERE tenant_id = ? AND instance_id = ? AND event_id = ?",
                (tenant_id, instance_id, event_id),
            ):
                await store.execute(
                    "INSERT INTO compliance_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        tenant_id,
                        instance_id,
                        event_id,
                        run_id,
                        agent_id,
                        config["event_type"],
                        entity_id,
                        conversation_id,
                        json.dumps(payload),
                        recorded_at,
                    ),
                )
            if not config.get("events_enabled", 1):
                continue
            delivery_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event_id}:local-ledger"))
            if await store.fetchone(
                "SELECT delivery_id FROM compliance_deliveries WHERE tenant_id = ? AND instance_id = ? AND delivery_id = ?",
                (tenant_id, instance_id, delivery_id),
            ):
                continue
            delivery = {
                "simulated": simulated,
                "event_id": event_id,
                "event_type": config["event_type"],
                "destination": config["event_destination"],
                "run_id": run_id,
                "entity_id": entity_id,
                "conversation_id": conversation_id,
            }
            await store.execute(
                "INSERT INTO compliance_deliveries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant_id,
                    instance_id,
                    delivery_id,
                    event_id,
                    run_id,
                    agent_id,
                    "simulated-delivered" if simulated else "recorded-locally",
                    int(simulated),
                    recorded_at,
                    json.dumps(delivery),
                ),
            )


async def _existing_occurrence(occurrence: RetentionOccurrence) -> dict | None:
    from cuga.backend.evolve import compliance_poc as poc

    tenant_id, instance_id = poc._scope()
    return await poc._store().fetchone(
        "SELECT * FROM compliance_occurrences WHERE tenant_id = ? AND instance_id = ? AND automation_id = ? AND occurrence_id = ?",
        (tenant_id, instance_id, occurrence.automation_id, occurrence.occurrence_id),
    )


def _reuse_existing(row: dict, occurrence: RetentionOccurrence) -> dict:
    if row["request_fingerprint"] != occurrence.fingerprint():
        raise OccurrenceConflictError("Occurrence identifier was reused with different inputs")
    if row["status"] == "completed" and row.get("report_json"):
        return json.loads(row["report_json"])
    if row["status"] == "running":
        raise OccurrenceInProgressError("Retention occurrence is already running")
    raise RuntimeError(row.get("error_message") or "Previous retention occurrence failed")


async def run_retention_occurrence(
    occurrence: RetentionOccurrence,
    *,
    agent_id: str,
    namespace_id: str | None,
    user_id: str | None = None,
    dry_run: bool = True,
    publisher: LifecyclePublisher | None = None,
) -> dict:
    from cuga.backend.evolve import compliance_poc as poc

    await poc._ensure_schema()
    config = await poc.get_automation_config(agent_id)
    if not config.get("retention_enabled", 1):
        raise ValueError("Retention scheduling is disabled for this agent")
    existing = await _existing_occurrence(occurrence)
    retry_failed = bool(existing and existing.get("status") == "failed")
    if existing and not retry_failed:
        return _reuse_existing(existing, occurrence)
    if existing and existing["request_fingerprint"] != occurrence.fingerprint():
        raise OccurrenceConflictError("Occurrence identifier was reused with different inputs")

    tenant_id, instance_id = poc._scope()
    store = poc._store()
    now = poc.dt.datetime.now(poc.dt.UTC).isoformat()
    run_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{tenant_id}:{instance_id}:{occurrence.automation_id}:{occurrence.occurrence_id}",
        )
    )
    try:
        if retry_failed:
            await store.execute(
                "UPDATE compliance_occurrences SET status = ?, run_id = ?, report_json = ?, "
                "error_message = ?, created_at = ?, completed_at = ? "
                "WHERE tenant_id = ? AND instance_id = ? AND automation_id = ? "
                "AND occurrence_id = ? AND status = ?",
                (
                    "running",
                    run_id,
                    None,
                    None,
                    now,
                    None,
                    tenant_id,
                    instance_id,
                    occurrence.automation_id,
                    occurrence.occurrence_id,
                    "failed",
                ),
            )
        else:
            await store.execute(
                "INSERT INTO compliance_occurrences VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant_id,
                    instance_id,
                    occurrence.automation_id,
                    occurrence.occurrence_id,
                    agent_id,
                    occurrence.fingerprint(),
                    occurrence.trigger,
                    occurrence.scheduled_for,
                    "running",
                    run_id,
                    None,
                    None,
                    now,
                    None,
                ),
            )
        await store.commit()
    except Exception:
        concurrent = await _existing_occurrence(occurrence)
        if concurrent:
            return _reuse_existing(concurrent, occurrence)
        raise

    metadata_filters = {"agent_id": agent_id}
    if user_id:
        metadata_filters["user_id"] = user_id
    try:
        report = await EvolveIntegration.run_retention(
            poc.POLICY,
            dry_run=dry_run,
            as_of=occurrence.scheduled_for,
            run_id=run_id,
            namespace_id=namespace_id,
            metadata_filters=metadata_filters,
        )
        if not isinstance(report, dict) or report.get("error"):
            raise RuntimeError("Evolve retention service is unavailable")
        report = poc.sanitize_retention_report(report)
        report.update(
            {
                "trigger": occurrence.trigger,
                "scheduled_for": occurrence.scheduled_for,
                "destination": config["event_destination"],
                "event_type": config["event_type"],
                "events_enabled": bool(config.get("events_enabled", 1)),
            }
        )
        run_id = str(report.get("run_id") or run_id)
        completed_at = poc.dt.datetime.now(poc.dt.UTC).isoformat()
        if await store.fetchone(
            "SELECT run_id FROM compliance_runs WHERE tenant_id = ? AND instance_id = ? AND run_id = ?",
            (tenant_id, instance_id, run_id),
        ):
            await store.execute(
                "UPDATE compliance_runs SET status = ?, simulated = ?, report_json = ?, created_at = ? WHERE tenant_id = ? AND instance_id = ? AND run_id = ?",
                (
                    "completed",
                    int(occurrence.trigger == "simulation"),
                    json.dumps(report),
                    completed_at,
                    tenant_id,
                    instance_id,
                    run_id,
                ),
            )
        else:
            await store.execute(
                "INSERT INTO compliance_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant_id,
                    instance_id,
                    run_id,
                    agent_id,
                    "completed",
                    int(occurrence.trigger == "simulation"),
                    json.dumps(report),
                    completed_at,
                ),
            )
        await (publisher or LocalLedgerPublisher()).publish(
            agent_id=agent_id,
            run_id=run_id,
            report=report,
            config=config,
            namespace_id=namespace_id,
            user_id=user_id,
            simulated=occurrence.trigger == "simulation",
            recorded_at=completed_at,
        )
        await store.execute(
            "UPDATE compliance_occurrences SET status = ?, run_id = ?, report_json = ?, error_message = ?, completed_at = ? WHERE tenant_id = ? AND instance_id = ? AND automation_id = ? AND occurrence_id = ?",
            (
                "completed",
                run_id,
                json.dumps(report),
                None,
                completed_at,
                tenant_id,
                instance_id,
                occurrence.automation_id,
                occurrence.occurrence_id,
            ),
        )
        await store.commit()
        return report
    except Exception as exc:
        failed_at = poc.dt.datetime.now(poc.dt.UTC).isoformat()
        await store.execute(
            "UPDATE compliance_occurrences SET status = ?, error_message = ?, completed_at = ? WHERE tenant_id = ? AND instance_id = ? AND automation_id = ? AND occurrence_id = ?",
            (
                "failed",
                type(exc).__name__,
                failed_at,
                tenant_id,
                instance_id,
                occurrence.automation_id,
                occurrence.occurrence_id,
            ),
        )
        await store.commit()
        raise
