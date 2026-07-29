import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cuga.backend.evolve import retention_scheduling

pytestmark = pytest.mark.unit


def test_scheduler_occurrence_id_is_stable_for_the_schedule_period():
    config = {
        "retention_frequency": "Every week",
        "scheduler_flow_id": "flow-a",
    }

    first = retention_scheduling.scheduler_occurrence_id(
        config,
        dt.datetime(2026, 7, 27, 2, tzinfo=dt.UTC),
    )
    retry = retention_scheduling.scheduler_occurrence_id(
        config,
        dt.datetime(2026, 8, 2, 22, tzinfo=dt.UTC),
    )
    next_week = retention_scheduling.scheduler_occurrence_id(
        config,
        dt.datetime(2026, 8, 3, 2, tzinfo=dt.UTC),
    )

    assert first == retry
    assert first != next_week


@pytest.mark.asyncio
async def test_schedule_status_requires_provider_confirmation_for_next_occurrence():
    engine = AsyncMock()
    engine.gateway_token = "configured"
    engine.available.return_value = (True, "connected")
    engine.get_flow.return_value = {"status": "ENABLED"}
    config = {
        "retention_enabled": 1,
        "retention_frequency": "Every week",
        "retention_time": "02:00",
        "scheduler_provider": "Activepieces",
        "scheduler_flow_id": "flow-a",
        "scheduler_error": None,
    }
    with (
        patch.object(
            retention_scheduling,
            "_last_occurrence",
            new=AsyncMock(
                return_value={
                    "status": "completed",
                    "trigger_type": "simulation",
                    "scheduled_for": "2026-07-29T21:01:18+00:00",
                    "completed_at": "2026-07-29T21:01:19+00:00",
                }
            ),
        ),
        patch.object(retention_scheduling, "retention_automation_id", return_value="automation-a"),
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.get_compliance_status",
            new=AsyncMock(return_value={"healthy": True, "retention_available": True}),
        ),
    ):
        status = await retention_scheduling.get_schedule_status("agent-a", config, engine)

    assert status["scheduler_connected"] is True
    assert status["scheduler_confirmed_enabled"] is True
    assert status["scheduler_health"] == "healthy"
    assert status["scheduler_callback_ready"] is True
    assert status["retention_service_healthy"] is True
    assert status["scheduler_detail"] == (
        "Activepieces, the CUGA callback, and Evolve retention are ready"
    )
    assert status["last_occurrence_trigger"] == "simulation"
    assert status["next_occurrence_at"] is not None


@pytest.mark.asyncio
async def test_schedule_health_fails_when_evolve_is_unavailable():
    engine = AsyncMock()
    engine.gateway_token = "configured"
    engine.available.return_value = (True, "connected")
    engine.get_flow.return_value = {"status": "ENABLED"}
    config = {
        "retention_enabled": 1,
        "retention_frequency": "Every week",
        "retention_time": "02:00",
        "scheduler_flow_id": "flow-a",
    }
    with (
        patch.object(retention_scheduling, "_last_occurrence", new=AsyncMock(return_value=None)),
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.get_compliance_status",
            new=AsyncMock(return_value=None),
        ),
    ):
        status = await retention_scheduling.get_schedule_status("agent-a", config, engine)

    assert status["scheduler_confirmed_enabled"] is True
    assert status["scheduler_health"] == "unhealthy"
    assert status["retention_service_connected"] is False
    assert status["scheduler_detail"] == "The schedule is enabled, but CUGA cannot reach Evolve"


def test_managed_flow_projection_excludes_action_inputs_and_credentials():
    flow = {
        "id": "flow-a",
        "status": "ENABLED",
        "created": "2026-07-29T20:00:00Z",
        "version": {
            "displayName": "memory-retention-agent-a",
            "trigger": {
                "settings": {
                    "pieceName": "@activepieces/piece-schedule",
                    "triggerName": "cron_expression",
                    "input": {"cronExpression": "0 2 * * 0", "timezone": "UTC"},
                },
                "nextAction": {
                    "name": "step_1",
                    "displayName": "Run CUGA retention",
                    "settings": {
                        "pieceName": "@activepieces/piece-http",
                        "actionName": "send_request",
                        "input": {"headers": {"X-Gateway-Token": "must-not-leak"}},
                    },
                },
            },
        },
    }
    projected = retention_scheduling.sanitize_managed_flow(flow)

    trigger = projected["version"]["trigger"]
    assert trigger["settings"]["input"]["cronExpression"] == "0 2 * * 0"
    assert trigger["nextAction"]["settings"]["actionName"] == "send_request"
    assert "input" not in trigger["nextAction"]["settings"]
    assert "must-not-leak" not in str(projected)


@pytest.mark.asyncio
async def test_scheduler_registers_retention_as_a_canonical_read_only_subscription():
    engine = AsyncMock()
    engine.gateway_token = "configured"
    engine.create_callback_schedule_flow.return_value = "flow-a"
    engine.invoke_url = "http://cuga.test/invoke"
    store = MagicMock()
    config = {
        "retention_enabled": 1,
        "retention_frequency": "Every week",
        "retention_time": "02:00",
    }
    with (
        patch(
            "cuga.backend.evolve.compliance_poc.update_scheduler_state",
            new=AsyncMock(return_value={**config, "scheduler_flow_id": "flow-a"}),
        ),
        patch.object(
            retention_scheduling,
            "get_schedule_status",
            new=AsyncMock(return_value={"scheduler_confirmed_enabled": True}),
        ),
        patch.object(retention_scheduling, "retention_automation_id", return_value="automation-a"),
    ):
        await retention_scheduling.ActivepiecesRetentionScheduler(engine, store).reconcile(
            "agent-a", config
        )

    subscription = store.upsert.call_args.args[0]
    assert subscription.id == "memory-retention:agent-a"
    assert subscription.ap_flow_id == "flow-a"
    assert subscription.managed_by == "memory_compliance"
    assert subscription.read_only is True
    assert subscription.config["schedule"] == "Every week at 02:00 UTC"
