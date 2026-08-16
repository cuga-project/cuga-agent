"""Timings-only tool tracking: records name/duration, never payloads.

Used when tracking is forced for the run receipt (advanced_features.run_receipt)
without the caller opting into track_tool_calls — a metrics flag must not turn
on capture of tool arguments/results into checkpointed thread state.
"""

from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker


def _record_one():
    ToolCallTracker.record_call(
        tool_name="get_accounts",
        arguments={"customer": "acme", "ssn": "123-45-6789"},  # pragma: allowlist secret
        result={"secret": "payload"},  # pragma: allowlist secret
        app_name="crm",
        operation_id="getAccounts",
        duration_ms=42.0,
        error="boom",
    )


def test_timings_only_drops_arguments_results_and_error():
    ToolCallTracker.start_tracking(enabled=True, timings_only=True)
    _record_one()
    calls = ToolCallTracker.stop_tracking()

    assert len(calls) == 1
    record = calls[0]
    assert record["name"] == "get_accounts"
    assert record["app_name"] == "crm"
    assert record["duration_ms"] == 42.0
    assert record["arguments"] is None
    assert record["result"] is None
    assert record["error"] is None


def test_full_tracking_still_captures_payloads():
    ToolCallTracker.start_tracking(enabled=True)
    _record_one()
    calls = ToolCallTracker.stop_tracking()

    assert calls[0]["arguments"] == {"customer": "acme", "ssn": "123-45-6789"}  # pragma: allowlist secret
    assert calls[0]["result"] == {"secret": "payload"}  # pragma: allowlist secret
    assert calls[0]["error"] == "boom"


def test_timings_only_resets_after_stop():
    ToolCallTracker.start_tracking(enabled=True, timings_only=True)
    ToolCallTracker.stop_tracking()

    ToolCallTracker.start_tracking(enabled=True)
    _record_one()
    calls = ToolCallTracker.stop_tracking()
    assert calls[0]["arguments"] is not None
