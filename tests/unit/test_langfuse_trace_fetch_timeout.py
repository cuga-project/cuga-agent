"""Issue #540: the Langfuse trace fetch must not block a task for minutes.

``LangfuseTraceHandler.get_langfuse_data`` is awaited inline in the per-task
eval path (``src/cuga/evaluation/evaluate_cuga.py:159``), so every second it
spends retrying is wall-clock added to the task before its result is returned.

Issue #318 added a fast path for *missing credentials*. These tests cover the
case that is still live: credentials are present but the Langfuse server does
not answer -- an unreachable host, a wrong port, or a trace that has not
propagated yet. The retry loop in ``extract_langfuse_data`` then runs its full
backoff schedule (2, 3, 4.5, 6.75, 10.1, 15.2, 22.8, 34.2, 51.3 = ~150s)
before giving up.

No Langfuse server, no API key and no network access are needed. The tests
point the client at a closed loopback port, which refuses instantly, and drive
the backoff on a virtual clock so the blocking cost is measured without being
spent.
"""

from __future__ import annotations

import asyncio
import socket
import time

import pytest

from cuga.evaluation.langfuse import get_langfuse_data as trace_fetch
from cuga.evaluation.langfuse.get_langfuse_data import LangfuseTraceHandler

pytestmark = pytest.mark.unit

# A task must not lose more than this to trace-fetch retries. Eval tasks run
# serially, so this cost is paid once per task on top of the task itself.
MAX_TRACE_FETCH_BUDGET_SECONDS = 10.0


def _closed_loopback_port() -> int:
    """Return a loopback port with nothing listening (connections are refused)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _VirtualClock:
    """Stands in for the ``time`` module inside the module under test."""

    def __init__(self) -> None:
        self.elapsed = 0.0

    def monotonic(self) -> float:
        return self.elapsed


@pytest.fixture
def unreachable_langfuse(monkeypatch):
    """Credentials present (so the #318 guard does not fire), host unreachable."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", f"http://127.0.0.1:{_closed_loopback_port()}")


@pytest.fixture
def virtual_clock(monkeypatch):
    """Make backoff sleeps free but keep them visible to the clock.

    ``raising=False`` so this still works against a build of the module that
    has no ``time`` import -- there the clock simply never advances, and the
    recorded sleeps alone show how long the caller would have blocked.
    """
    clock = _VirtualClock()

    async def _sleep(delay, *args, **kwargs):
        clock.elapsed += delay

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    monkeypatch.setattr(trace_fetch, "time", clock, raising=False)
    return clock


async def test_unreachable_langfuse_does_not_stall_the_task(unreachable_langfuse, virtual_clock):
    """With Langfuse unreachable, the fetch must give up inside the budget."""
    handler = LangfuseTraceHandler("0" * 32)

    started = time.monotonic()
    result = await handler.get_langfuse_data()
    connect_time = time.monotonic() - started

    # Nothing is fetched either way; the only question is what it costs.
    assert result is None

    assert virtual_clock.elapsed <= MAX_TRACE_FETCH_BUDGET_SECONDS, (
        f"trace fetch would block the task for {virtual_clock.elapsed:.1f}s of backoff "
        f"(budget {MAX_TRACE_FETCH_BUDGET_SECONDS:.0f}s); "
        f"the connection attempts themselves took only {connect_time:.2f}s"
    )


async def test_missing_credentials_still_returns_immediately(monkeypatch, virtual_clock):
    """Regression guard for the #318 fast path."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    handler = LangfuseTraceHandler("0" * 32)

    assert await handler.get_langfuse_data() is None
    assert virtual_clock.elapsed == 0.0


async def test_absent_trace_id_returns_immediately(virtual_clock):
    """No trace id means there is nothing to wait for."""
    assert await LangfuseTraceHandler("").get_langfuse_data() is None
    assert virtual_clock.elapsed == 0.0
