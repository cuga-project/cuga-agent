"""Concurrent queries on ``LocalEmbeddingStore``, which runs each one in a worker thread on one connection."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("sqlite_vec")

from cuga.backend.storage.embedding.local import LocalEmbeddingStore
from cuga.backend.storage.policy.base import policy_embedding_schema

_STORES = 20
_CONCURRENT = 16
_SCOPE = {"tenant_id": "t", "instance_id": "i"}


def _store(tmp_path: Path, n: int) -> LocalEmbeddingStore:
    return LocalEmbeddingStore(str(tmp_path / f"p{n}.db"), "cuga_policies", policy_embedding_schema(4))


def _policy_row(policy_id: str) -> dict:
    return {
        "id": policy_id,
        **_SCOPE,
        "policy_type": "tool_approval",
        "enabled": True,
        "priority": 0,
        "policy_json": "{}",
    }


def _failures(results: list) -> list[str]:
    return [f"{type(r).__name__}: {r}" for r in results if isinstance(r, Exception)]


@pytest.mark.asyncio
async def test_concurrent_first_queries_find_the_table(tmp_path: Path) -> None:
    """The first queries on a new store must not see the connection before its table exists."""
    failures = []
    for n in range(_STORES):
        store = _store(tmp_path, n)
        results = await asyncio.gather(
            *(store.list(_SCOPE, limit=5) for _ in range(_CONCURRENT)), return_exceptions=True
        )
        failures += _failures(results)

    assert failures == []


@pytest.mark.asyncio
async def test_concurrent_reads_and_writes_share_the_connection(tmp_path: Path) -> None:
    failures = []
    for n in range(_STORES):
        store = _store(tmp_path, n)
        await store.list(_SCOPE, limit=5)
        writes = [store.add(f"p{k}", [0.0] * 4, _policy_row(f"p{k}")) for k in range(_CONCURRENT // 2)]
        reads = [store.search([0.0] * 4, 5, _SCOPE) for _ in range(_CONCURRENT // 2)]
        failures += _failures(await asyncio.gather(*writes, *reads, return_exceptions=True))
        stored = len(await store.list(_SCOPE, limit=50))
        if stored != _CONCURRENT // 2:
            failures.append(f"store {n} kept {stored} of {_CONCURRENT // 2} rows")

    assert failures == []
