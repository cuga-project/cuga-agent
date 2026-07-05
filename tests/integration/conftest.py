"""Integration-test fixtures (live external services).

The fixtures here intentionally stay tiny — long-lived async pools
crossed event-loop boundaries in our first two CI runs (see the test
file's module-docstring). The tests now manage their own single-shot
``asyncpg.connect`` lifecycle inside per-call ``asyncio.run`` and don't
need a session-scoped pool from the fixture layer.
"""

from __future__ import annotations

import uuid

import pytest

from .helpers import unique_collection as _unique_collection


# ---------------------------------------------------------------------------
# pgvector live-DB helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_tenant() -> dict[str, str]:
    """Unique ``(tenant_id, instance_id)`` per test.

    The adapter scopes every insert by this pair, so distinct values give
    perfect row-level isolation without dropping the collection. Combined
    with a per-test ``collection`` name in the test body itself, this
    avoids any shared-state surprises between tests in the same session.
    """
    return {
        "tenant_id": f"test_{uuid.uuid4().hex[:8]}",
        "instance_id": f"test_{uuid.uuid4().hex[:8]}",
    }


__all__ = ["_unique_collection"]
