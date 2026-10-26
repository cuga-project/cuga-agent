"""Regression tests for unbounded policy listing (issue #982).

``PolicyStorage.list_policies`` used to default to ``limit=100`` with no
``ORDER BY``, so enforcement lookups (tool approvals, intent guards,
playbooks, tool guides) never saw policies past the first 100 rows.
``limit=None`` must now read every row, and a capped listing must be
deterministic (ordered by id) instead of depending on storage order.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlite_vec")

from cuga.backend.cuga_graph.policy.models import PolicyType
from cuga.backend.storage.embedding.local import LocalEmbeddingStore
from cuga.backend.storage.policy.base import policy_embedding_schema
from cuga.backend.storage.policy.local import LocalPolicyStore

pytestmark = pytest.mark.unit

_DIM = 4


def _make_policy_store(tmp_path: Path) -> LocalPolicyStore:
    store = LocalPolicyStore(str(tmp_path / "policies.db"), "policies")
    asyncio.run(store.create_schema(_DIM))
    return store


def _policy_data(pid: str, *, enabled: bool = True, priority: int = 0) -> dict[str, Any]:
    return {
        "id": pid,
        "policy_type": "tool_approval",
        "enabled": enabled,
        "priority": priority,
        "policy_json": json.dumps({"id": pid, "type": "tool_approval"}),
        "embedding": [0.1, 0.2, 0.3, 0.4],
    }


def _seed(store: LocalPolicyStore, count: int, *, prefix: str = "policy") -> None:
    for i in range(count):
        asyncio.run(store.add_policy(_policy_data(f"{prefix}_{i:04d}")))


def test_list_policies_unbounded_reads_beyond_the_first_100(tmp_path: Path) -> None:
    """``limit=None`` is the enforcement read: it must see every stored policy."""
    store = _make_policy_store(tmp_path)
    _seed(store, 150)

    unbounded = asyncio.run(store.list_policies(None, True, None))
    assert len(unbounded) == 150

    by_type = asyncio.run(store.list_policies(PolicyType.TOOL_APPROVAL, True, None))
    assert len(by_type) == 150


def test_capped_listing_stays_at_100_but_is_deterministic(tmp_path: Path) -> None:
    """A capped listing keeps its cap yet no longer depends on storage order."""
    store = _make_policy_store(tmp_path)
    _seed(store, 150)

    capped = asyncio.run(store.list_policies(None, True, 100))
    assert len(capped) == 100
    assert [r["id"] for r in capped] == [f"policy_{i:04d}" for i in range(100)]

    again = asyncio.run(store.list_policies(None, True, 100))
    assert [r["id"] for r in again] == [r["id"] for r in capped]


def test_enabled_only_filter_holds_across_the_full_set(tmp_path: Path) -> None:
    store = _make_policy_store(tmp_path)
    for i in range(120):
        asyncio.run(store.add_policy(_policy_data(f"policy_{i:04d}", enabled=(i % 2 == 0))))

    enabled = asyncio.run(store.list_policies(None, True, None))
    assert len(enabled) == 60
    assert {r["id"] for r in enabled} == {f"policy_{i:04d}" for i in range(120) if i % 2 == 0}

    all_rows = asyncio.run(store.list_policies(None, False, None))
    assert len(all_rows) == 120


def test_embedding_store_list_without_limit_reads_everything(tmp_path: Path) -> None:
    """The embedding store contract behind the policy store (also used directly)."""
    schema = policy_embedding_schema(_DIM)
    store = LocalEmbeddingStore(str(tmp_path / "policies.db"), "policies", schema)

    def _meta(pid: str) -> dict[str, Any]:
        return {
            "id": pid,
            "tenant_id": "",
            "instance_id": "",
            "policy_type": "tool_approval",
            "enabled": True,
            "priority": 0,
        }

    for i in range(130):
        asyncio.run(store.add(f"policy_{i:04d}", [0.1, 0.2, 0.3, 0.4], _meta(f"policy_{i:04d}")))

    rows = asyncio.run(store.list({}, None))
    assert len(rows) == 130

    capped = asyncio.run(store.list({}, 100))
    assert [r["id"] for r in capped] == [f"policy_{i:04d}" for i in range(100)]
