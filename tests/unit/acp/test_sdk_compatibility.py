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
    """create_app with a minimal echo AgentManifest must return a FastAPI app."""
    from typing import Any, AsyncGenerator

    from fastapi import FastAPI

    from acp_sdk.models import AgentName
    from acp_sdk.server import AgentManifest

    class _EchoAgent(AgentManifest):
        @property
        def name(self) -> AgentName:
            return AgentName("echo")

        @property
        def description(self) -> str:
            return "Echo agent"

        @property
        def input_content_types(self) -> list[str]:
            return ["text/plain"]

        @property
        def output_content_types(self) -> list[str]:
            return ["text/plain"]

        async def run(
            self,
            input: list[Message],  # noqa: A002
            context: Any,
        ) -> AsyncGenerator[Any, Any]:
            for msg in input:
                yield msg

    store = MemoryStore(limit=10, ttl=timedelta(seconds=60))
    app = create_app(_EchoAgent(), store=store)
    assert isinstance(app, FastAPI)
