"""Unit tests for outbound ACP supervisor configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from cuga.supervisor_utils.supervisor_config import build_agents_from_list

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.mark.unit
async def test_valid_acp_process_config_loads_as_existing_external_shape(tmp_path: Path) -> None:
    source = {
        "name": "coding-agent",
        "description": "External ACP coding agent",
        "acp_protocol": {
            "enabled": True,
            "command": "external-agent",
            "args": ["--acp"],
            "cwd": str(tmp_path),
            "env": ["PROVIDER_API_KEY"],
            "startup_timeout": 15,
            "prompt_timeout": 120,
            "shutdown_grace_period": 5,
        },
    }

    agents = await build_agents_from_list([source])

    assert agents == {"coding-agent": {"type": "external", "config": source}}


@pytest.mark.unit
@pytest.mark.parametrize(
    "acp_protocol",
    [
        {"enabled": True, "command": ""},
        {"enabled": True, "command": "agent", "env": ["KEY=value"]},
        {"enabled": True, "endpoint": "https://legacy.example"},
    ],
)
async def test_invalid_or_legacy_acp_process_config_fails(acp_protocol: dict) -> None:
    with pytest.raises(ValueError):
        await build_agents_from_list([{"name": "worker", "acp_protocol": acp_protocol}])


@pytest.mark.unit
async def test_acp_and_a2a_cannot_both_be_enabled(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly one enabled protocol block"):
        await build_agents_from_list(
            [
                {
                    "name": "worker",
                    "acp_protocol": {"enabled": True, "command": "agent", "cwd": str(tmp_path)},
                    "a2a_protocol": {"enabled": True, "endpoint": "http://localhost:9000"},
                }
            ]
        )
