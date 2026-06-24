"""Timing and concurrency analysis helpers for load tests."""

from __future__ import annotations

from dataclasses import dataclass, field
import statistics
import time
from typing import Iterable


@dataclass
class UserLoadTimings:
    user_id: int
    thread_id: str
    started_at: float
    primary_finished_at: float | None = None
    state_checked_at: float | None = None
    finished_at: float | None = None
    primary_duration_s: float = 0.0
    state_check_duration_s: float = 0.0
    followup_duration_s: float = 0.0
    total_duration_s: float = 0.0

    def start_offset_s(self, batch_start: float) -> float:
        return self.started_at - batch_start

    def finish_offset_s(self, batch_start: float) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - batch_start


@dataclass
class LoadTestConcurrencyReport:
    num_users: int
    wall_clock_s: float
    batch_start: float
    user_timings: list[UserLoadTimings] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def build(
        cls, *, num_users: int, wall_clock_s: float, batch_start: float, timings: Iterable[UserLoadTimings]
    ):
        report = cls(
            num_users=num_users,
            wall_clock_s=wall_clock_s,
            batch_start=batch_start,
            user_timings=sorted(list(timings), key=lambda t: t.user_id),
        )
        report.warnings = report._detect_concurrency_issues()
        return report

    def _primary_durations(self) -> list[float]:
        return [t.primary_duration_s for t in self.user_timings if t.primary_duration_s > 0]

    def _total_durations(self) -> list[float]:
        return [t.total_duration_s for t in self.user_timings if t.total_duration_s > 0]

    def _detect_concurrency_issues(self) -> list[str]:
        warnings: list[str] = []
        primaries = self._primary_durations()
        if not primaries:
            return warnings

        median_primary = statistics.median(primaries)
        max_primary = max(primaries)
        min_primary = min(primaries)
        finish_offsets = [t.finish_offset_s(self.batch_start) for t in self.user_timings]
        finish_spread = max(finish_offsets) - min(finish_offsets) if finish_offsets else 0.0

        sequential_estimate = sum(self._total_durations())
        speedup = sequential_estimate / self.wall_clock_s if self.wall_clock_s > 0 else 0.0

        if self.num_users > 1 and speedup < self.num_users * 0.5:
            warnings.append(
                f"Low concurrency speedup ({speedup:.2f}x for {self.num_users} users; "
                f"sequential estimate {sequential_estimate:.2f}s vs wall {self.wall_clock_s:.2f}s)"
            )

        if median_primary > 0 and max_primary > median_primary * 1.5:
            slowest = max(self.user_timings, key=lambda t: t.primary_duration_s)
            pct = ((max_primary / median_primary) - 1) * 100
            warnings.append(
                f"Primary-task tail shift: user {slowest.user_id} took {max_primary:.2f}s "
                f"({pct:.0f}% above median {median_primary:.2f}s)"
            )

        if self.num_users > 1 and finish_spread > max(0.5, median_primary * 0.75):
            warnings.append(
                f"Finish-time spread of {finish_spread:.2f}s across users "
                f"(min finish {min(finish_offsets):.2f}s, max finish {max(finish_offsets):.2f}s)"
            )

        if self.num_users > 1 and max_primary - min_primary > 0.25:
            warnings.append(
                f"Primary duration range {min_primary:.2f}s–{max_primary:.2f}s "
                f"(delta {max_primary - min_primary:.2f}s)"
            )

        if len(primaries) > 1:
            stdev = statistics.pstdev(primaries)
            cv = stdev / statistics.mean(primaries) if statistics.mean(primaries) > 0 else 0.0
            if cv > 0.25:
                warnings.append(f"Uneven primary durations (CV {cv:.2f}, stdev {stdev:.2f}s)")

        return warnings

    def format_report(self, *, title: str = "Load Test Concurrency Report") -> str:
        primaries = self._primary_durations()
        followups = [t.followup_duration_s for t in self.user_timings if t.followup_duration_s > 0]
        totals = self._total_durations()
        finish_offsets = [t.finish_offset_s(self.batch_start) for t in self.user_timings]
        sequential_estimate = sum(totals)
        speedup = sequential_estimate / self.wall_clock_s if self.wall_clock_s > 0 else 0.0

        lines = [
            "",
            f"--- {title} ---",
            f"Users: {self.num_users}",
            f"Wall clock: {self.wall_clock_s:.2f}s",
            f"Sequential estimate (sum of user totals): {sequential_estimate:.2f}s",
            f"Concurrency speedup: {speedup:.2f}x",
        ]

        if primaries:
            lines.extend(
                [
                    f"Primary task: min {min(primaries):.2f}s | median {statistics.median(primaries):.2f}s | "
                    f"max {max(primaries):.2f}s | mean {statistics.mean(primaries):.2f}s",
                ]
            )
        if followups:
            lines.append(
                f"Followup task: min {min(followups):.2f}s | median {statistics.median(followups):.2f}s | "
                f"max {max(followups):.2f}s | mean {statistics.mean(followups):.2f}s"
            )
        if finish_offsets:
            lines.append(
                f"Finish offsets: earliest {min(finish_offsets):.2f}s | latest {max(finish_offsets):.2f}s | "
                f"spread {max(finish_offsets) - min(finish_offsets):.2f}s"
            )

        lines.append("")
        lines.append("Per-user timings:")
        lines.append(
            f"{'User':>4}  {'Primary':>8}  {'State':>8}  {'Followup':>8}  {'Total':>8}  {'Finish@':>8}  Thread"
        )
        for timing in self.user_timings:
            lines.append(
                f"{timing.user_id:4d}  "
                f"{timing.primary_duration_s:8.2f}  "
                f"{timing.state_check_duration_s:8.2f}  "
                f"{timing.followup_duration_s:8.2f}  "
                f"{timing.total_duration_s:8.2f}  "
                f"{timing.finish_offset_s(self.batch_start):8.2f}  "
                f"{timing.thread_id[:8]}..."
            )

        lines.append("")
        lines.append("Finish timeline (relative to batch start):")
        for timing in sorted(self.user_timings, key=lambda t: t.finish_offset_s(self.batch_start)):
            finish = timing.finish_offset_s(self.batch_start)
            bar_len = max(1, int(finish / self.wall_clock_s * 40)) if self.wall_clock_s > 0 else 1
            lines.append(f"  user {timing.user_id:2d}  |{'#' * bar_len:<40}| {finish:.2f}s")

        if self.warnings:
            lines.append("")
            lines.append("Concurrency observations (informational):")
            for warning in self.warnings:
                lines.append(f"  - {warning}")
        else:
            lines.append("")
            lines.append("Concurrency observations: no significant skew detected.")

        lines.append(f"--- End {title} ---")
        return "\n".join(lines)


def print_load_test_report(report: LoadTestConcurrencyReport, *, title: str | None = None) -> None:
    print(report.format_report(title=title or "Load Test Concurrency Report"))
