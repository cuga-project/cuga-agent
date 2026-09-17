"""Integration test configuration and fixtures for ACP support."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

import pytest


@dataclass
class FakeStreamEvent:
    name: str
    data: Any = None
    final: bool = False


@pytest.fixture
def fake_event_stream() -> Any:
    """Mock event_stream function that simulates CUGA's event stream for ACP."""

    async def _event_stream(
        query: str,
        api_mode: bool = False,
        thread_id: str | None = None,
        agent: Any = None,
        disable_history: bool = False,
        user_id: str = "test_user",
        user_attachments: Any = None,
        resume: Any = None,
    ) -> AsyncIterator[bytes]:
        yield b"event: AgentThinking\ndata: Processing request...\n\n"
        answer = f"The answer to '{query}' is 42"
        payload = json.dumps({"data": answer, "variables": {}, "active_policies": []})
        yield f"event: Answer\ndata: {payload}\n\n".encode()

    return _event_stream


@pytest.fixture
def mock_app_state() -> Any:
    """Mock app_state with minimal required attributes for ACP runner."""

    class MockAppState:
        def __init__(self) -> None:
            self.agent = "mock_agent"
            self.output_format = None

    return MockAppState()


@pytest.fixture
def acp_settings() -> Any:
    """Minimal ACPSettings stub."""
    from cuga.backend.server.acp.settings import normalize_acp_settings

    @dataclass
    class _RawSettings:
        enabled: bool = True
        path_prefix: str = "/acp"
        agent_name: str = "cuga"
        agent_description: str = "CUGA agent exposed over ACP."
        supervisor_config_path: str = ""
        auto_approve: bool = False
        store: str = "memory"
        store_limit: int = 1000
        store_ttl_seconds: int = 3600
        auth_required: bool = False
        enable_playground_cors: bool = False

    return normalize_acp_settings(_RawSettings())
