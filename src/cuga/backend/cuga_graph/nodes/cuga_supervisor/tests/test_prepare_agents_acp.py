"""ACP-specific supervisor prompt preparation tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.mark.unit
async def test_acp_prompt_metadata_uses_configured_description_without_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.prepare_agents_and_prompt import (
        describe_external_agent,
    )

    spawn = AsyncMock(side_effect=AssertionError("prompt preparation must not spawn ACP"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    wrapped = {
        "type": "external",
        "config": {
            "name": "coding-agent",
            "description": "External ACP coding agent",
            "acp_protocol": {"enabled": True, "command": "agent", "cwd": str(tmp_path)},
        },
    }

    metadata = await describe_external_agent("fallback", wrapped)

    assert metadata.agent_type == "external"
    assert metadata.description == "External ACP coding agent"
    assert metadata.agent_card is None
    assert metadata.accepts_variables is False
    spawn.assert_not_awaited()


@pytest.mark.unit
async def test_acp_metadata_is_derived_per_agent_without_state_leakage(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.prepare_agents_and_prompt import (
        describe_external_agent,
    )

    first = {
        "type": "external",
        "config": {
            "name": "first",
            "description": "First description",
            "acp_protocol": {"enabled": True, "command": "one", "cwd": str(tmp_path)},
        },
    }
    second = {
        "type": "external",
        "config": {
            "name": "second",
            "description": "Second description",
            "acp_protocol": {"enabled": True, "command": "two", "cwd": str(tmp_path)},
        },
    }

    first_meta = await describe_external_agent("first", first)
    second_meta = await describe_external_agent("second", second)

    assert first_meta.description == "First description"
    assert second_meta.description == "Second description"
    assert first_meta is not second_meta
