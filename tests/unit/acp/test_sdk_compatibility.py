"""Unit tests verifying ACP SDK imports and basic object construction.

These tests are expected to FAIL with ImportError until the acp_sdk dependency
is added (Task 1.2). Do not use pytest.importorskip — a missing SDK must fail
loudly so CI catches it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from acp_sdk.client import Client  # noqa: F401
from acp_sdk.models import Message, Run, RunStatus  # noqa: F401
from acp_sdk.server import MemoryStore, create_app

pytestmark = pytest.mark.unit


@pytest.mark.unit
def test_acp_sdk_imports() -> None:
    """All required ACP SDK symbols must be importable."""
    assert Client is not None
    assert Message is not None
    assert Run is not None
    assert RunStatus is not None
    assert MemoryStore is not None
    assert create_app is not None


@pytest.mark.unit
def test_memory_store_construction() -> None:
    """MemoryStore must accept limit and ttl keyword arguments."""
    store = MemoryStore(limit=10, ttl=timedelta(seconds=60))
    assert store is not None


@pytest.mark.unit
def test_create_app_returns_fastapi_app() -> None:
    """create_app with a minimal decorated echo agent must return a FastAPI app."""
    from fastapi import FastAPI

    @create_app()
    async def echo_agent(input: Message) -> Message:  # noqa: A002
        return input

    assert isinstance(echo_agent, FastAPI)
