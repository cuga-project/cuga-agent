"""Unit tests for load test concurrency metrics."""

from system_tests.load.metrics import LoadTestConcurrencyReport, UserLoadTimings


def test_report_flags_tail_shift_and_low_speedup():
    batch_start = 100.0
    timings = [
        UserLoadTimings(
            user_id=0,
            thread_id="aaaa-bbbb",
            started_at=batch_start,
            primary_duration_s=1.0,
            followup_duration_s=0.5,
            total_duration_s=1.5,
            finished_at=batch_start + 1.5,
        ),
        UserLoadTimings(
            user_id=1,
            thread_id="cccc-dddd",
            started_at=batch_start,
            primary_duration_s=4.0,
            followup_duration_s=0.5,
            total_duration_s=4.5,
            finished_at=batch_start + 4.5,
        ),
    ]

    report = LoadTestConcurrencyReport.build(
        num_users=2,
        wall_clock_s=4.5,
        batch_start=batch_start,
        timings=timings,
    )

    assert any("tail shift" in warning for warning in report.warnings)
    assert any("tail shift" in warning and "user 1" in warning for warning in report.warnings)
    rendered = report.format_report(title="Test Report")
    assert "Concurrency speedup" in rendered
    assert "Finish timeline" in rendered
