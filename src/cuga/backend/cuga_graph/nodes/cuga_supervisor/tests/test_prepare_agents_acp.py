"""ACP-specific supervisor prompt preparation tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture(autouse=True)
def _workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem import paths

    monkeypatch.setattr(paths, "local_base_dir", lambda: tmp_path)


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


@pytest.mark.unit
@pytest.mark.parametrize(
    "wrapped",
    [
        {"type": "external", "config": None},
        {"type": "external", "config": {"acp_protocol": "enabled", "a2a_protocol": []}},
    ],
)
async def test_malformed_external_protocol_wrappers_fail_closed_without_spawning(
    wrapped: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.prepare_agents_and_prompt import (
        describe_external_agent,
    )

    spawn = AsyncMock(side_effect=AssertionError("prompt preparation must not spawn malformed ACP"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)

    with pytest.raises(ValueError, match="config must be a mapping|protocol must be a mapping"):
        await describe_external_agent("worker", wrapped)
    spawn.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize(
    "wrapped",
    [
        {"type": "external", "config": {"a2a_protocol": {"enabled": False}}},
        {
            "type": "external",
            "config": {
                "acp_protocol": {"enabled": False},
                "a2a_protocol": {"enabled": False},
            },
        },
        {"type": "external", "config": {"a2a_protocol": {}}},
    ],
)
async def test_prompt_preparation_rejects_zero_enabled_protocols(wrapped: dict) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.prepare_agents_and_prompt import (
        describe_external_agent,
    )

    with pytest.raises(ValueError, match="exactly one enabled protocol block is required"):
        await describe_external_agent("worker", wrapped)


@pytest.mark.unit
@pytest.mark.parametrize(
    "acp_protocol",
    [
        {"enabled": True},
        {"enabled": True, "command": "agent", "prompt_timout": 5},
        {"enabled": True, "endpoint": "https://legacy.example"},
    ],
)
async def test_enabled_invalid_acp_config_is_rejected_without_spawning(
    acp_protocol: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.prepare_agents_and_prompt import (
        describe_external_agent,
    )

    spawn = AsyncMock(side_effect=AssertionError("prompt preparation must not spawn invalid ACP"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)

    with pytest.raises(ValueError):
        await describe_external_agent(
            "worker",
            {"type": "external", "config": {"name": "worker", "acp_protocol": acp_protocol}},
        )

    spawn.assert_not_awaited()
