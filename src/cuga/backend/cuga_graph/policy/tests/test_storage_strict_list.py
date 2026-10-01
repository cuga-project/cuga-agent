"""``PolicyStorage.list_policies(strict=True)`` surfaces backend failures.

The default swallows them and returns ``[]`` — fine for lookups, wrong for a
guard that must fail closed and cannot tell "no policies" from "backend down".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.backend.cuga_graph.policy.storage import PolicyStorage

pytestmark = pytest.mark.unit


def _storage_with_broken_backend() -> PolicyStorage:
    storage = PolicyStorage.__new__(PolicyStorage)
    storage._connected = True
    storage._backend = MagicMock()
    storage._backend.list_policies = AsyncMock(side_effect=RuntimeError("backend down"))
    return storage


@pytest.mark.asyncio
async def test_default_swallows_backend_errors():
    assert await _storage_with_broken_backend().list_policies() == []


@pytest.mark.asyncio
async def test_strict_raises_backend_errors():
    with pytest.raises(RuntimeError, match="backend down"):
        await _storage_with_broken_backend().list_policies(strict=True)
