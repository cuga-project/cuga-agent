"""Regression test for issue #398: ``ReindexBusyError`` must map to a
distinct ``reindex_busy`` error code, not collapse to the generic
``reindex_failed`` toast.

User-visible symptom: clicking Re-index while an upload is still in
flight produced "Re-index didn't run / Re-index ran but didn't embed
anything. Check server logs and retry." That's misleading — the cause
is transient (workers in progress), the cure is "wait + retry", not
"check logs". The new code distinguishes the two.
"""

from __future__ import annotations

import asyncio

from cuga.backend.knowledge.engine import ReindexBusyError
from cuga.backend.server import manage_routes


class _FakeEngineBase:
    """Minimum engine surface the migration helper touches.

    ``vector_config_hash`` and ``knowledge_config_hash`` both empty so
    source==target and the copy branch is skipped — we want the test
    to land on the ``await live_engine.reindex(target)`` call we care
    about.
    """

    _reindex_in_progress: set = set()
    _reindex_deferred: set = set()
    _files_dir = None

    class _Config:
        def vector_config_hash(self):
            return ""

    _config = _Config()


class _FakeEngineBusy(_FakeEngineBase):
    async def reindex(self, _target: str) -> dict:
        raise ReindexBusyError("ingest worker for some-file.pdf still running")


class _FakeEngineGenericFail(_FakeEngineBase):
    async def reindex(self, _target: str) -> dict:
        raise RuntimeError("disk full")


class _FakeLiveState:
    knowledge_config_hash = ""


def _run_migrate(engine) -> dict:
    return asyncio.run(manage_routes._migrate_and_reindex_for_agent("agent_x", engine, _FakeLiveState()))


def test_reindex_busy_maps_to_distinct_error_code():
    """Issue #398: ReindexBusyError → ``error: "reindex_busy"`` (NOT
    ``reindex_failed``). Lets the FE show a warning-tone "wait then
    retry" toast instead of an error-tone "check logs" toast."""
    result = _run_migrate(_FakeEngineBusy())

    assert result.get("triggered") is False, result
    assert result.get("error") == "reindex_busy", (
        f"ReindexBusyError must NOT collapse to reindex_failed: {result!r}"
    )


def test_generic_failure_still_maps_to_reindex_failed():
    """Regression guard: only ReindexBusyError gets the new code. Any
    other engine.reindex exception still surfaces as the generic
    ``reindex_failed`` so operators still see the existing error path."""
    result = _run_migrate(_FakeEngineGenericFail())

    assert result.get("triggered") is False, result
    assert result.get("error") == "reindex_failed", (
        f"non-busy errors must keep the old generic code: {result!r}"
    )
