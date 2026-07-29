"""Replaceable scheduler adapter for CUGA memory retention."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Protocol


class RetentionScheduler(Protocol):
    async def reconcile(self, agent_id: str, config: dict[str, Any]) -> dict[str, Any]: ...
    async def status(self, agent_id: str, config: dict[str, Any]) -> dict[str, Any]: ...


def retention_automation_id(agent_id: str) -> str:
    from cuga.backend.evolve.compliance_poc import _scope

    tenant_id, instance_id = _scope()
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"cuga:memory-retention:{tenant_id}:{instance_id}:{agent_id}",
        )
    )


def _cron(config: dict[str, Any]) -> str:
    hour, minute = map(int, str(config["retention_time"]).split(":"))
    frequency = config["retention_frequency"]
    if frequency == "Every day":
        return f"{minute} {hour} * * *"
    if frequency == "Every month":
        return f"{minute} {hour} 1 * *"
    return f"{minute} {hour} * * 0"


def _next_occurrence(config: dict[str, Any], now: dt.datetime | None = None) -> str | None:
    if not config.get("retention_enabled"):
        return None
    now = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    hour, minute = map(int, str(config["retention_time"]).split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    frequency = config["retention_frequency"]
    if frequency == "Every day":
        if candidate <= now:
            candidate += dt.timedelta(days=1)
    elif frequency == "Every month":
        candidate = candidate.replace(day=1)
        if candidate <= now:
            year = candidate.year + (1 if candidate.month == 12 else 0)
            month = 1 if candidate.month == 12 else candidate.month + 1
            candidate = candidate.replace(year=year, month=month, day=1)
    else:
        candidate += dt.timedelta(days=(6 - candidate.weekday()) % 7)
        if candidate <= now:
            candidate += dt.timedelta(days=7)
    return candidate.isoformat().replace("+00:00", "Z")


def scheduler_occurrence_id(config: dict[str, Any], scheduled_for: dt.datetime) -> str:
    moment = scheduled_for.astimezone(dt.UTC)
    frequency = config["retention_frequency"]
    if frequency == "Every month":
        period = moment.strftime("%Y-%m")
    elif frequency == "Every week":
        year, week, _ = moment.isocalendar()
        period = f"{year}-W{week:02d}"
    else:
        period = moment.strftime("%Y-%m-%d")
    return f"activepieces:{config.get('scheduler_flow_id') or 'unbound'}:{period}"


async def _last_occurrence(agent_id: str) -> dict | None:
    from cuga.backend.evolve.compliance_poc import _ensure_schema, _scope, _store

    await _ensure_schema()
    tenant_id, instance_id = _scope()
    return await _store().fetchone(
        "SELECT status, trigger_type, scheduled_for, completed_at FROM compliance_occurrences WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? ORDER BY created_at DESC LIMIT 1",
        (tenant_id, instance_id, agent_id),
    )


async def get_schedule_status(
    agent_id: str,
    config: dict[str, Any] | None = None,
    engine: Any | None = None,
) -> dict[str, Any]:
    from cuga.backend.evolve.compliance_poc import get_automation_config

    config = config or await get_automation_config(agent_id)
    provider = config.get("scheduler_provider")
    flow_id = config.get("scheduler_flow_id")
    connected = False
    confirmed: bool | None = None
    callback_ready = False
    retention_service_connected = False
    retention_service_healthy = False
    health = "unavailable"
    detail = config.get("scheduler_error") or "No scheduler is configured"
    if engine is not None:
        provider = "Activepieces"
        connected, detail = await engine.available()
        health = "healthy" if connected else "unavailable"
        if connected and flow_id:
            flow = await engine.get_flow(flow_id)
            if flow is None:
                confirmed = False
                health = "unhealthy"
                detail = "The configured Activepieces schedule could not be found"
            else:
                confirmed = flow.get("status") == "ENABLED"
                health = "healthy" if confirmed or not config.get("retention_enabled") else "unhealthy"
                detail = (
                    "Activepieces confirmed the schedule is enabled"
                    if confirmed
                    else "Activepieces confirmed the schedule is disabled"
                )
        elif connected:
            confirmed = False
            health = "unhealthy" if config.get("retention_enabled") else "healthy"
            detail = "Activepieces is connected, but no retention schedule has been published"
    if config.get("retention_enabled") and confirmed is True:
        callback_ready = bool(getattr(engine, "gateway_token", None))
        from cuga.backend.evolve.integration import EvolveIntegration

        retention_status = await EvolveIntegration.get_compliance_status()
        retention_service_connected = isinstance(retention_status, dict)
        retention_service_healthy = bool(
            retention_service_connected
            and retention_status.get("healthy")
            and retention_status.get("retention_available")
        )
        if not callback_ready:
            health = "unhealthy"
            detail = "The schedule is enabled, but the CUGA retention callback is not configured"
        elif not retention_service_connected:
            health = "unhealthy"
            detail = "The schedule is enabled, but CUGA cannot reach Evolve"
        elif not retention_service_healthy:
            health = "unhealthy"
            detail = "The schedule is enabled, but Evolve retention is unavailable"
        else:
            health = "healthy"
            detail = "Activepieces, the CUGA callback, and Evolve retention are ready"
    last = await _last_occurrence(agent_id)
    return {
        "automation_id": retention_automation_id(agent_id),
        "scheduler_provider": provider,
        "scheduler_connected": connected,
        "scheduler_confirmed_enabled": confirmed,
        "scheduler_health": health,
        "scheduler_detail": detail,
        "scheduler_callback_ready": callback_ready,
        "retention_service_connected": retention_service_connected,
        "retention_service_healthy": retention_service_healthy,
        "last_occurrence_at": (last or {}).get("completed_at") or (last or {}).get("scheduled_for"),
        "last_occurrence_status": (last or {}).get("status"),
        "last_occurrence_trigger": (last or {}).get("trigger_type"),
        "next_occurrence_at": _next_occurrence(config) if confirmed is True else None,
    }


def sanitize_managed_flow(flow: dict[str, Any]) -> dict[str, Any]:
    """Keep Studio's normal AP shape while excluding action inputs and credentials."""

    def project_node(node: Any) -> dict[str, Any] | None:
        if not isinstance(node, dict):
            return None
        settings = node.get("settings") or {}
        safe_settings = {
            key: settings.get(key)
            for key in ("pieceName", "triggerName", "actionName")
            if settings.get(key) is not None
        }
        if settings.get("triggerName") and isinstance(settings.get("input"), dict):
            safe_settings["input"] = {
                key: settings["input"].get(key)
                for key in ("cronExpression", "timezone")
                if settings["input"].get(key) is not None
            }
        projected = {
            "name": node.get("name"),
            "displayName": node.get("displayName"),
            "settings": safe_settings,
        }
        next_action = project_node(node.get("nextAction"))
        if next_action is not None:
            projected["nextAction"] = next_action
        return projected

    version = flow.get("version") or {}
    return {
        "id": flow.get("id"),
        "status": flow.get("status"),
        "created": flow.get("created"),
        "version": {
            "displayName": version.get("displayName"),
            "trigger": project_node(version.get("trigger")),
        },
    }


def project_managed_run_detail(run: dict[str, Any]) -> dict[str, Any]:
    """Return execution evidence without provider inputs, outputs, or credentials."""
    steps = []
    failed = False
    for name, step in (run.get("steps") or {}).items():
        if not isinstance(step, dict):
            continue
        status = step.get("status")
        failed = failed or status not in {None, "SUCCEEDED"}
        steps.append(
            {
                "name": step.get("displayName") or name,
                "status": status,
            }
        )
    succeeded = run.get("status") == "SUCCEEDED" and not failed
    return {
        "ok": True,
        "run": {
            "id": run.get("id"),
            "status": run.get("status"),
            "started_at": run.get("startTime"),
            "finished_at": run.get("finishTime"),
        },
        "answer": (
            "Activepieces completed the scheduled CUGA retention callback."
            if succeeded
            else None
        ),
        "trigger_payload": {
            "managed_by": "Memory compliance",
            "flow_id": run.get("flowId"),
            "steps": steps,
        },
        "error": "Activepieces reported a failed step." if failed else None,
    }


class ActivepiecesRetentionScheduler:
    def __init__(self, engine: Any, subscription_store: Any | None = None):
        self.engine = engine
        self.subscription_store = subscription_store

    def _register_subscription(
        self,
        agent_id: str,
        config: dict[str, Any],
        flow_id: str,
        *,
        enabled: bool,
    ) -> None:
        """Publish the owner-managed flow through the canonical Events index."""
        if self.subscription_store is None:
            return
        from cuga.backend.events.principal import DEFAULT
        from cuga.backend.events.subscriptions import Subscription

        self.subscription_store.upsert(
            Subscription(
                id=f"memory-retention:{agent_id}",
                mode="CRON",
                target_agent=agent_id,
                tenant=DEFAULT.scope,
                backend="activepieces",
                source_type="time",
                source_connector="memory compliance",
                ap_flow_id=flow_id,
                prompt="Apply the published memory retention policy",
                status="active" if enabled else "paused",
                dedup_key=f"managed:memory_compliance:{agent_id}",
                flow_name=f"memory-retention-{agent_id}",
                event="retention_schedule",
                config={
                    "schedule": (
                        f"{config.get('retention_frequency', 'Scheduled')} at "
                        f"{config.get('retention_time', '00:00')} UTC"
                    )
                },
                managed_by="memory_compliance",
                read_only=True,
            )
        )

    async def reconcile(self, agent_id: str, config: dict[str, Any]) -> dict[str, Any]:
        from cuga.backend.evolve.compliance_poc import update_scheduler_state

        now = dt.datetime.now(dt.UTC).isoformat()
        if not self.engine.gateway_token:
            config = await update_scheduler_state(
                agent_id,
                {
                    "scheduler_provider": "Activepieces",
                    "scheduler_checked_at": now,
                    "scheduler_error": "GATEWAY_TOKEN is required before publishing a schedule",
                },
            )
            return await get_schedule_status(agent_id, config, self.engine)
        flow_id = config.get("scheduler_flow_id")
        try:
            if not config.get("retention_enabled"):
                if flow_id:
                    await self.engine.set_flow_status(flow_id, False)
                    self._register_subscription(agent_id, config, flow_id, enabled=False)
                config = await update_scheduler_state(
                    agent_id,
                    {
                        "scheduler_provider": "Activepieces",
                        "scheduler_checked_at": now,
                        "scheduler_error": None,
                    },
                )
                return await get_schedule_status(agent_id, config, self.engine)
            automation_id = retention_automation_id(agent_id)
            callback_base = self.engine.invoke_url.rsplit("/invoke", 1)[0]
            flow_id = await self.engine.create_callback_schedule_flow(
                name=f"memory-retention-{agent_id}",
                callback_url=f"{callback_base}/api/internal/memory/automations/{automation_id}/runs",
                body={"automation_id": automation_id},
                cron=_cron(config),
                replace=True,
            )
            self._register_subscription(agent_id, config, flow_id, enabled=True)
            config = await update_scheduler_state(
                agent_id,
                {
                    "scheduler_provider": "Activepieces",
                    "scheduler_flow_id": flow_id,
                    "scheduler_checked_at": now,
                    "scheduler_error": None,
                },
            )
        except Exception as exc:
            config = await update_scheduler_state(
                agent_id,
                {
                    "scheduler_provider": "Activepieces",
                    "scheduler_checked_at": now,
                    "scheduler_error": f"{type(exc).__name__}: {exc}",
                },
            )
        return await get_schedule_status(agent_id, config, self.engine)

    async def status(self, agent_id: str, config: dict[str, Any]) -> dict[str, Any]:
        return await get_schedule_status(agent_id, config, self.engine)
