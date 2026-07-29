import datetime as dt
import json
from unittest.mock import ANY, AsyncMock, patch

import pytest

from cuga.backend.evolve.compliance_poc_sil_fixture import CAPTURED_SIL_CONVERSATIONS
from cuga.backend.evolve.compliance_poc import _conversation_specs, _entity_specs
from cuga.backend.evolve import compliance_poc
from cuga.backend.evolve.retention_jobs import RetentionOccurrence, run_retention_occurrence

pytestmark = pytest.mark.unit


def test_core_fixture_has_real_retention_signals_and_distinct_trace_fields():
    threads = [f"thread-{index}" for index in range(10)]
    specs = {spec["seed_key"]: spec for spec in _entity_specs(dt.datetime.now(dt.UTC), threads)}

    assert specs["old-session-t1"]["trace_id"] == "T1"
    assert specs["old-session-t2"]["trace_id"] == "T2"
    assert specs["t1-guideline"]["source_task_id"] == "T1"
    assert "source_task_id" not in specs["old-session-t1"]
    assert specs["unused-guideline"]["age"] == 240
    assert specs["unused-guideline"]["last_accessed"] == 190
    assert specs["stale-guideline"]["age"] == 200
    assert specs["stale-guideline"]["last_accessed"] == 30
    assert "last_accessed" not in specs["missing-access"]


def test_fixture_contains_fake_sensitive_candidates_only():
    contents = [spec["content"] for spec in _entity_specs(dt.datetime.now(dt.UTC), ["t"] * 10)]
    assert any("4111-1111-1111-1111" in content for content in contents)
    assert any("TEST-SECRET-0000" in content for content in contents)


def test_conversation_fixture_uses_distinct_sil_captures():
    transcripts = _conversation_specs()

    assert len(transcripts) == 10
    assert len({transcript[0][1] for transcript in transcripts}) == 10
    assert {len(transcript) for transcript in transcripts} == {2, 4}
    assert len({message for transcript in transcripts for _, message, _ in transcript}) == sum(
        len(transcript) for transcript in transcripts
    )
    assert all(turn["detail_events"] for capture in CAPTURED_SIL_CONVERSATIONS for turn in capture["turns"])


@pytest.mark.asyncio
async def test_user_retention_summary_is_derived_from_policy_and_scheduler_state():
    with patch.object(
        compliance_poc,
        "get_automation_config",
        new=AsyncMock(
            return_value={
                "retention_enabled": 1,
                "retention_frequency": "Every week",
                "retention_time": "02:00",
            }
        ),
    ):
        summary = await compliance_poc.get_user_retention_summary("agent-a")

    assert summary == {
        "schedule": {
            "state": "not_configured",
            "label": "Automatic cleanup is not configured",
            "detail": "No scheduler is configured",
        },
        "rules": [
            {
                "summary": "Guidance reviewed after 90 days",
                "scheduled": False,
                "state": "not_configured",
            },
            {
                "summary": "Unused guidance deleted after 180 days",
                "scheduled": False,
                "state": "not_configured",
            },
            {
                "summary": "Conversations deleted after one year",
                "scheduled": False,
                "state": "not_configured",
            },
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "status", "expected_state", "expected_label"),
    [
        (
            {"retention_enabled": 0},
            {"scheduler_connected": False, "scheduler_confirmed_enabled": None},
            "disabled",
            "Automatic cleanup is disabled",
        ),
        (
            {"retention_enabled": 1},
            {"scheduler_connected": False, "scheduler_confirmed_enabled": None},
            "unreachable",
            "Automatic cleanup cannot reach Activepieces",
        ),
        (
            {"retention_enabled": 1},
            {"scheduler_connected": True, "scheduler_confirmed_enabled": False},
            "needs_attention",
            "Automatic cleanup needs attention",
        ),
        (
            {"retention_enabled": 1},
            {
                "scheduler_connected": True,
                "scheduler_confirmed_enabled": True,
                "scheduler_health": "healthy",
            },
            "scheduled",
            "Automatic cleanup is scheduled",
        ),
    ],
)
async def test_user_retention_summary_preserves_scheduler_state(
    config,
    status,
    expected_state,
    expected_label,
):
    status = {**status, "scheduler_detail": "Provider detail"}
    with (
        patch.object(
            compliance_poc,
            "get_automation_config",
            new=AsyncMock(return_value=config),
        ),
        patch(
            "cuga.backend.evolve.retention_scheduling.get_schedule_status",
            new=AsyncMock(return_value=status),
        ),
    ):
        summary = await compliance_poc.get_user_retention_summary(
            "agent-a",
            engine=object(),
        )

    assert summary["schedule"] == {
        "state": expected_state,
        "label": expected_label,
        "detail": "Provider detail",
    }
    assert {rule["state"] for rule in summary["rules"]} == {expected_state}


@pytest.mark.asyncio
async def test_bootstrap_repairs_missing_seed_keys_and_scopes_namespace():
    store = AsyncMock()
    store.fetchone.return_value = None
    conversation_db = AsyncMock()
    created = []

    async def create_entity(**kwargs):
        created.append(kwargs)
        return {"id": f"entity-{len(created)}", "created_at": kwargs["created_at"]}

    with (
        patch.object(compliance_poc, "_ensure_schema", new=AsyncMock()),
        patch.object(compliance_poc, "_store", return_value=store),
        patch.object(compliance_poc, "get_conversation_db", return_value=conversation_db),
        patch.object(
            compliance_poc.EvolveIntegration,
            "list_entities",
            new=AsyncMock(
                return_value={
                    "items": [
                        {
                            "id": "existing",
                            "metadata": {
                                "seed_key": "old-session-t1",
                                "poc_seed_id": compliance_poc.POC_SEED,
                            },
                        }
                    ]
                }
            ),
        ) as list_entities,
        patch.object(compliance_poc.EvolveIntegration, "create_entity", new=create_entity),
        patch.object(
            compliance_poc.EvolveIntegration,
            "get_compliance_status",
            new=AsyncMock(return_value={"healthy": True}),
        ),
    ):
        result = await compliance_poc.bootstrap("agent-a", "user-a", "tenant-a", "Demo User")

    assert (
        result["created_entities"]
        == len(compliance_poc._entity_specs(dt.datetime.now(dt.UTC), result["conversation_ids"])) - 1
    )
    assert all(item["namespace_id"] == "tenant-a" for item in created)
    assert all(item["owner_id"] == "user-a" for item in created)
    assert all(
        item["metadata"]["agent_id"] == "agent-a" and item["metadata"]["user_id"] == "user-a"
        for item in created
    )
    conversation_db.save_conversation.assert_awaited()
    list_entities.assert_awaited_once_with(
        metadata_filters={
            "poc_seed_id": compliance_poc.POC_SEED,
            "agent_id": "agent-a",
            "user_id": "user-a",
        },
        limit=200,
        include_content=False,
        record_access=False,
        namespace_id="tenant-a",
    )


@pytest.mark.asyncio
async def test_bootstrap_recreates_memories_when_completed_seed_inventory_was_deleted():
    store = AsyncMock()
    store.fetchone.return_value = {"completed_at": "2026-07-29T00:00:00Z"}
    conversation_db = AsyncMock()
    created = []

    async def create_entity(**kwargs):
        created.append(kwargs)
        return {"id": f"entity-{len(created)}", "created_at": kwargs["created_at"]}

    with (
        patch.object(compliance_poc, "_ensure_schema", new=AsyncMock()),
        patch.object(compliance_poc, "_store", return_value=store),
        patch.object(compliance_poc, "get_conversation_db", return_value=conversation_db),
        patch.object(
            compliance_poc.EvolveIntegration,
            "list_entities",
            new=AsyncMock(return_value={"items": []}),
        ),
        patch.object(compliance_poc.EvolveIntegration, "create_entity", new=create_entity),
        patch.object(
            compliance_poc.EvolveIntegration,
            "get_compliance_status",
            new=AsyncMock(return_value={"healthy": True}),
        ),
    ):
        result = await compliance_poc.bootstrap("agent-a", "user-a", "tenant-a", "Demo User")

    assert result["already_completed"] is True
    assert result["created_entities"] == len(
        compliance_poc._entity_specs(dt.datetime.now(dt.UTC), result["conversation_ids"])
    )
    assert result["memory_count"] == len(created)
    assert not any(
        "INSERT INTO compliance_seed_state" in call.args[0] for call in store.execute.await_args_list
    )


@pytest.mark.asyncio
async def test_bootstrap_runtime_retries_scheduler_without_ui():
    engine = object()
    scheduler = AsyncMock()
    scheduler.reconcile.side_effect = [
        {
            "scheduler_connected": True,
            "scheduler_confirmed_enabled": False,
            "scheduler_detail": "Pieces are still loading",
        },
        {
            "scheduler_connected": True,
            "scheduler_confirmed_enabled": True,
            "scheduler_health": "healthy",
            "scheduler_detail": "Schedule enabled",
        },
    ]
    config = {"retention_enabled": 1}
    with (
        patch.object(
            compliance_poc,
            "bootstrap",
            new=AsyncMock(return_value={"memory_count": 43}),
        ) as bootstrap,
        patch.object(
            compliance_poc,
            "get_automation_config",
            new=AsyncMock(return_value=config),
        ),
        patch(
            "cuga.backend.evolve.retention_scheduling.ActivepiecesRetentionScheduler",
            return_value=scheduler,
        ),
        patch.object(compliance_poc.asyncio, "sleep", new=AsyncMock()) as sleep,
    ):
        result = await compliance_poc.bootstrap_runtime(
            "agent-a",
            "user-a",
            "tenant-a",
            "Demo User",
            engine=engine,
            scheduler_attempts=3,
            scheduler_retry_seconds=0.01,
        )

    bootstrap.assert_awaited_once_with("agent-a", "user-a", "tenant-a", "Demo User")
    assert scheduler.reconcile.await_count == 2
    sleep.assert_awaited_once_with(0.01)
    assert result["scheduler"]["scheduler_confirmed_enabled"] is True


@pytest.mark.asyncio
async def test_schedule_persists_linked_private_ledger_payloads():
    store = AsyncMock()
    store.fetchone.return_value = None
    report = {
        "run_id": "run-a",
        "flagged": [
            {
                "entity_id": "entity-a",
                "action": "flag",
                "rule": "stale-guidelines",
                "outcome": "flagged",
                "content_preview": "must not enter the ledger",
                "metadata": {"private": "value"},
            }
        ],
        "deleted": [],
        "skipped": [],
        "summary": "one flagged",
    }
    with (
        patch.object(compliance_poc, "_ensure_schema", new=AsyncMock()),
        patch.object(compliance_poc, "_store", return_value=store),
        patch.object(compliance_poc, "_scope", return_value=("tenant-a", "instance-a")),
        patch.object(
            compliance_poc,
            "get_automation_config",
            new=AsyncMock(
                return_value={
                    "retention_enabled": 1,
                    "retention_time": "02:00",
                    "events_enabled": 1,
                    "event_destination": "demo-bus",
                    "event_type": "retention.outcome",
                }
            ),
        ),
        patch.object(
            compliance_poc.EvolveIntegration,
            "run_retention",
            new=AsyncMock(return_value=report),
        ) as run_retention,
        patch.object(
            compliance_poc.EvolveIntegration,
            "list_entities",
            new=AsyncMock(
                return_value={"items": [{"id": "entity-a", "metadata": {"session_id": "thread-a"}}]}
            ),
        ),
    ):
        result = await compliance_poc.run_simulated_schedule("agent-a", "tenant-a", "user-a")

    assert result["run_id"] == "run-a"
    statements = [call.args[0] for call in store.execute.await_args_list]
    delivery_call = next(
        call for call in store.execute.await_args_list if "compliance_deliveries" in call.args[0]
    )
    payload = json.loads(delivery_call.args[1][-1])
    assert "content" not in json.dumps(payload)
    assert "content_preview" not in json.dumps(payload)
    assert "entity-a" in json.dumps(payload)
    assert "content_preview" not in json.dumps(result)
    run_call = next(call for call in store.execute.await_args_list if "compliance_runs" in call.args[0])
    assert "content_preview" not in run_call.args[1][6]
    assert any("compliance_runs" in statement for statement in statements)
    assert any("compliance_events" in statement for statement in statements)
    run_retention.assert_awaited_once_with(
        compliance_poc.POLICY,
        dry_run=True,
        as_of=ANY,
        run_id=ANY,
        namespace_id="tenant-a",
        metadata_filters={"agent_id": "agent-a", "user_id": "user-a"},
    )


@pytest.mark.asyncio
async def test_schedule_does_not_persist_when_evolve_is_unavailable():
    store = AsyncMock()
    store.fetchone.return_value = None
    with (
        patch.object(compliance_poc, "_ensure_schema", new=AsyncMock()),
        patch.object(compliance_poc, "_store", return_value=store),
        patch.object(
            compliance_poc,
            "get_automation_config",
            new=AsyncMock(
                return_value={
                    "retention_enabled": 1,
                    "retention_time": "02:00",
                    "events_enabled": 1,
                    "event_destination": "demo-bus",
                    "event_type": "retention.outcome",
                }
            ),
        ),
        patch.object(
            compliance_poc.EvolveIntegration,
            "run_retention",
            new=AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(RuntimeError, match="unavailable"):
            await compliance_poc.run_simulated_schedule("agent-a", "tenant-a", "user-a")

    statements = [call.args[0] for call in store.execute.await_args_list]
    assert any("INSERT INTO compliance_occurrences" in statement for statement in statements)
    assert any("UPDATE compliance_occurrences SET status" in statement for statement in statements)
    assert not any("compliance_runs" in statement for statement in statements)
    assert not any("compliance_events" in statement for statement in statements)


@pytest.mark.asyncio
async def test_completed_occurrence_is_reused_without_running_evolve_again():
    occurrence = RetentionOccurrence(
        automation_id="automation-a",
        occurrence_id="activepieces:flow-a:2026-W31",
        scheduled_for="2026-07-29T02:00:00Z",
        trigger="scheduler",
    )
    stored_report = {"run_id": "run-a", "flagged": [], "deleted": [], "skipped": []}
    store = AsyncMock()
    store.fetchone.return_value = {
        "request_fingerprint": occurrence.fingerprint(),
        "status": "completed",
        "report_json": json.dumps(stored_report),
    }
    with (
        patch.object(compliance_poc, "_ensure_schema", new=AsyncMock()),
        patch.object(compliance_poc, "_store", return_value=store),
        patch.object(compliance_poc, "_scope", return_value=("tenant-a", "instance-a")),
        patch.object(
            compliance_poc,
            "get_automation_config",
            new=AsyncMock(return_value={"retention_enabled": 1}),
        ),
        patch.object(
            compliance_poc.EvolveIntegration,
            "run_retention",
            new=AsyncMock(),
        ) as run_retention,
    ):
        result = await run_retention_occurrence(
            occurrence,
            agent_id="agent-a",
            namespace_id="tenant-a",
        )

    assert result == stored_report
    run_retention.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_occurrence_is_retried_with_the_same_idempotency_key():
    occurrence = RetentionOccurrence(
        automation_id="automation-a",
        occurrence_id="activepieces:flow-a:2026-W31",
        scheduled_for="2026-07-29T02:00:00Z",
        trigger="scheduler",
    )
    store = AsyncMock()
    store.fetchone.side_effect = [
        {
            "request_fingerprint": occurrence.fingerprint(),
            "status": "failed",
            "error_message": "RuntimeError",
        },
        None,
    ]
    publisher = AsyncMock()
    report = {
        "run_id": "run-a",
        "flagged": [],
        "deleted": [],
        "skipped": [],
    }
    with (
        patch.object(compliance_poc, "_ensure_schema", new=AsyncMock()),
        patch.object(compliance_poc, "_store", return_value=store),
        patch.object(compliance_poc, "_scope", return_value=("tenant-a", "instance-a")),
        patch.object(
            compliance_poc,
            "get_automation_config",
            new=AsyncMock(
                return_value={
                    "retention_enabled": 1,
                    "event_destination": "local-ledger",
                    "event_type": "retention.outcome",
                }
            ),
        ),
        patch.object(
            compliance_poc.EvolveIntegration,
            "run_retention",
            new=AsyncMock(return_value=report),
        ) as run_retention,
    ):
        result = await run_retention_occurrence(
            occurrence,
            agent_id="agent-a",
            namespace_id="tenant-a",
            publisher=publisher,
        )

    assert result["run_id"] == "run-a"
    run_retention.assert_awaited_once()
    assert "UPDATE compliance_occurrences SET status" in store.execute.await_args_list[0].args[0]
    assert store.execute.await_args_list[0].args[1][-1] == "failed"


@pytest.mark.asyncio
async def test_activity_ledger_returns_public_projection_only():
    store = AsyncMock()
    store.fetchall.side_effect = [
        [
            {
                "tenant_id": "tenant-a",
                "instance_id": "instance-a",
                "run_id": "run-a",
                "agent_id": "agent-a",
                "status": "completed",
                "simulated": 1,
                "created_at": "2026-07-24T12:00:00Z",
                "report_json": json.dumps(
                    {
                        "run_id": "run-a",
                        "completed_at": "2026-07-24T12:00:00Z",
                        "dry_run": True,
                        "policy": {"private": "must not leak"},
                        "metadata_filters": {"user_id": "must not leak"},
                        "flagged": [
                            {
                                "entity_id": "entity-a",
                                "action": "flag",
                                "outcome": "would_flag",
                                "content_preview": "must not leak",
                                "metadata": {"value": "must not leak"},
                            }
                        ],
                        "deleted": [],
                        "skipped": [],
                        "errors": ["private backend error"],
                        "warnings": ["private plugin warning"],
                    }
                ),
            }
        ],
        [
            {
                "tenant_id": "tenant-a",
                "instance_id": "instance-a",
                "request_id": "request-a",
                "agent_id": "agent-a",
                "user_id": "user-a",
                "entity_id": "entity-b",
                "action": "forget",
                "status": "completed",
                "created_at": "2026-07-24T11:00:00Z",
            }
        ],
    ]
    with (
        patch.object(compliance_poc, "_ensure_schema", new=AsyncMock()),
        patch.object(compliance_poc, "_store", return_value=store),
        patch.object(compliance_poc, "_scope", return_value=("tenant-a", "instance-a")),
    ):
        result = await compliance_poc.list_ledger("activity", "agent-a")

    assert result == [
        {
            "record_type": "retention_run",
            "run_id": "run-a",
            "status": "completed",
            "simulated": 1,
            "created_at": "2026-07-24T12:00:00Z",
            "report": {
                "run_id": "run-a",
                "completed_at": "2026-07-24T12:00:00Z",
                "dry_run": True,
                "flagged": [
                    {
                        "entity_id": "entity-a",
                        "action": "flag",
                        "outcome": "would_flag",
                    }
                ],
                "deleted": [],
                "skipped": [],
                "summary": (
                    "Retention evaluation found 1 for review, 0 deletion matches, and "
                    "0 kept because evidence was incomplete."
                ),
                "errors": ["One or more memories could not be evaluated."],
                "warnings": ["Some memories were evaluated with incomplete usage data."],
            },
            "affected_entity_ids": ["entity-a"],
        },
        {
            "record_type": "user_request",
            "request_id": "request-a",
            "entity_id": "entity-b",
            "action": "forget",
            "status": "completed",
            "created_at": "2026-07-24T11:00:00Z",
        },
    ]
    assert "private" not in json.dumps(result)
    assert "tenant_id" not in json.dumps(result)
    assert "user_id" not in json.dumps(result)


@pytest.mark.asyncio
async def test_delivery_ledger_drops_database_and_unknown_payload_fields():
    store = AsyncMock()
    store.fetchall.return_value = [
        {
            "tenant_id": "tenant-a",
            "instance_id": "instance-a",
            "delivery_id": "delivery-a",
            "event_id": "event-a",
            "run_id": "run-a",
            "agent_id": "agent-a",
            "status": "simulated-delivered",
            "simulated": 1,
            "delivered_at": "2026-07-24T12:00:00Z",
            "payload_json": json.dumps(
                {
                    "event_id": "event-a",
                    "run_id": "run-a",
                    "entity_id": "entity-a",
                    "event_type": "retention.outcome",
                    "private": {"content": "must not leak"},
                }
            ),
        }
    ]
    with (
        patch.object(compliance_poc, "_ensure_schema", new=AsyncMock()),
        patch.object(compliance_poc, "_store", return_value=store),
        patch.object(compliance_poc, "_scope", return_value=("tenant-a", "instance-a")),
    ):
        result = await compliance_poc.list_ledger("deliveries", "agent-a")

    assert result == [
        {
            "delivery_id": "delivery-a",
            "event_id": "event-a",
            "run_id": "run-a",
            "status": "simulated-delivered",
            "simulated": 1,
            "delivered_at": "2026-07-24T12:00:00Z",
            "payload": {
                "event_id": "event-a",
                "run_id": "run-a",
                "entity_id": "entity-a",
                "event_type": "retention.outcome",
            },
        }
    ]
    assert "private" not in json.dumps(result)
    assert "payload_json" not in json.dumps(result)


@pytest.mark.asyncio
async def test_memory_usage_is_append_only_and_deduplicated_per_turn():
    store = AsyncMock()
    store.fetchone.return_value = None
    with (
        patch.object(compliance_poc, "_ensure_schema", new=AsyncMock()),
        patch.object(compliance_poc, "_store", return_value=store),
        patch.object(compliance_poc, "_scope", return_value=("tenant-a", "instance-a")),
    ):
        result = await compliance_poc.record_memory_usage(
            turn_id="turn-a",
            agent_id="agent-a",
            user_id="user-a",
            entity_ids=["entity-a", "entity-a", "entity-b"],
            thread_id="thread-a",
            conversation_label="Prepare the renewal summary",
            used_at="2026-07-25T12:00:00+00:00",
        )

    assert result["entity_ids"] == ["entity-a", "entity-b"]
    inserts = [
        call
        for call in store.execute.await_args_list
        if "INSERT INTO compliance_memory_usage" in call.args[0]
    ]
    assert len(inserts) == 2
    assert {call.args[1][6] for call in inserts} == {"entity-a", "entity-b"}


@pytest.mark.asyncio
async def test_memory_usage_summary_is_scoped_and_derived_from_events():
    store = AsyncMock()
    store.fetchall.side_effect = [
        [
            {
                "entity_id": "entity-a",
                "thread_id": "thread-missing",
                "conversation_label": "Deleted conversation",
                "used_at": "2026-07-26T12:00:00+00:00",
            },
            {
                "entity_id": "entity-a",
                "thread_id": "thread-new",
                "conversation_label": "Newer conversation",
                "used_at": "2026-07-25T12:00:00+00:00",
            },
            {
                "entity_id": "entity-a",
                "thread_id": "thread-old",
                "conversation_label": "Older conversation",
                "used_at": "2026-07-20T12:00:00+00:00",
            },
            {
                "entity_id": "not-requested",
                "thread_id": "thread-other",
                "conversation_label": "Other conversation",
                "used_at": "2026-07-24T12:00:00+00:00",
            },
        ],
        [{"thread_id": "thread-new"}, {"thread_id": "thread-old"}],
    ]
    with (
        patch.object(compliance_poc, "_ensure_schema", new=AsyncMock()),
        patch.object(compliance_poc, "_store", return_value=store),
        patch.object(compliance_poc, "_scope", return_value=("tenant-a", "instance-a")),
    ):
        result = await compliance_poc.get_memory_usage_summaries(
            agent_id="agent-a",
            user_id="user-a",
            entity_ids=["entity-a"],
        )

    assert result["entity-a"]["count"] == 3
    assert result["entity-a"]["last_used_at"] == "2026-07-26T12:00:00+00:00"
    assert [entry["thread_id"] for entry in result["entity-a"]["recent"]] == [
        "thread-new",
        "thread-old",
    ]
    assert store.fetchall.await_args_list[0].args[1] == (
        "tenant-a",
        "instance-a",
        "agent-a",
        "user-a",
    )
    assert store.fetchall.await_args_list[1].args[1] == (
        "tenant-a",
        "instance-a",
        "agent-a",
        "user-a",
    )


@pytest.mark.asyncio
async def test_demo_conversations_seed_matching_answer_disclosures_and_usage():
    store = AsyncMock()
    conversation_db = AsyncMock()
    conversation_db.save_stream_events.return_value = True
    threads = [f"thread-{index}" for index in range(10)]
    entities = [
        {
            "id": f"entity-{index}",
            "metadata": {
                "seed_key": f"eligible-{index}",
                "session_id": thread_id,
            },
        }
        for index, thread_id in enumerate(threads)
    ]
    seed_keys = list(
        dict.fromkeys(
            seed_key
            for capture in CAPTURED_SIL_CONVERSATIONS
            for turn in capture["turns"]
            for seed_key in turn["memory_seed_keys"]
        )
    )
    entities.extend(
        {
            "id": f"live-entity-{index}",
            "metadata": {
                "seed_key": seed_key,
                "session_id": threads[0],
            },
        }
        for index, seed_key in enumerate(seed_keys)
    )

    async def record_usage(**kwargs):
        return {
            "turn_id": kwargs["turn_id"],
            "count": len(kwargs["entity_ids"]),
            "entity_ids": kwargs["entity_ids"],
            "used_at": kwargs["used_at"],
        }

    with (
        patch.object(compliance_poc, "_store", return_value=store),
        patch.object(compliance_poc, "_scope", return_value=("tenant-a", "instance-a")),
        patch.object(compliance_poc, "get_conversation_db", return_value=conversation_db),
        patch.object(compliance_poc, "record_memory_usage", new=record_usage),
    ):
        result = await compliance_poc._seed_demo_conversation_evidence(
            agent_id="agent-a",
            user_id="user-a",
            entities=entities,
            threads=threads,
        )

    expected_usage_count = sum(
        len(turn["memory_seed_keys"]) for capture in CAPTURED_SIL_CONVERSATIONS for turn in capture["turns"]
    )
    assert result == {"answer_count": 11, "usage_count": expected_usage_count}
    assert conversation_db.save_stream_events.await_count == 10
    calls = conversation_db.save_stream_events.await_args_list
    for capture, call in zip(CAPTURED_SIL_CONVERSATIONS, calls, strict=True):
        events = call.args[3]
        answers = [event for event in events if event["event_name"] == "Answer"]
        assert len(answers) == len(capture["turns"])
        for turn, answer in zip(capture["turns"], answers, strict=True):
            payload = json.loads(answer["event_data"])
            assert payload["memory_usage"]["count"] == len(turn["memory_seed_keys"])
            assert payload["memory_usage"]["entity_ids"]
            saved_seed_keys = turn["memory_saved_seed_keys"]
            if saved_seed_keys:
                assert payload["memory_saved"]["count"] == len(saved_seed_keys)
                assert payload["memory_saved"]["entity_ids"]
            else:
                assert "memory_saved" not in payload
        assert [event["event_name"] for event in events] == [
            event_name
            for turn in capture["turns"]
            for event_name in [
                "UserMessage",
                *[detail["event_name"] for detail in turn["detail_events"]],
                "Answer",
            ]
        ]
