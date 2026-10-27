"""Unit tests for outbound ACP supervisor configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from cuga.supervisor_utils.supervisor_config import build_agents_from_list

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture(autouse=True)
def _workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem import paths

    monkeypatch.setattr(paths, "local_base_dir", lambda: tmp_path)


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
@pytest.mark.parametrize(
    ("unknown_key", "safe_name"),
    [("prompt_timout", "prompt_timout"), ("unsafe\nkey`", r"unsafe\?key\?")],
)
async def test_acp_rejects_unknown_key_by_safe_name(unknown_key: str, safe_name: str) -> None:
    protocol = {"enabled": True, "command": "agent", unknown_key: 1}

    with pytest.raises(
        ValueError,
        match=rf"Agent 'worker': Unknown acp_protocol configuration key\(s\): {safe_name}",
    ):
        await build_agents_from_list([{"name": "worker", "acp_protocol": protocol}])


@pytest.mark.unit
@pytest.mark.parametrize("enabled", ["false", "true", 0, 1])
async def test_acp_protocol_enabled_must_be_boolean(enabled: object) -> None:
    with pytest.raises(
        ValueError,
        match=r"Agent 'worker': acp_protocol enabled must be a boolean",
    ):
        await build_agents_from_list(
            [{"name": "worker", "acp_protocol": {"enabled": enabled, "command": "agent"}}]
        )


@pytest.mark.unit
def test_a2a_protocol_without_enabled_preserves_existing_disabled_behavior() -> None:
    from cuga.supervisor_utils.supervisor_config import _protocol_block

    block = {}

    assert _protocol_block({"a2a_protocol": block}, "a2a_protocol", "worker") is block


@pytest.mark.unit
async def test_non_boolean_a2a_enablement_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="a2a_protocol enabled must be a boolean"):
        await build_agents_from_list(
            [
                {
                    "name": "worker",
                    "acp_protocol": {"enabled": True, "command": "agent", "cwd": str(tmp_path)},
                    "a2a_protocol": {"enabled": "true", "endpoint": "http://localhost:9000"},
                }
            ]
        )


@pytest.mark.unit
@pytest.mark.parametrize("protocol_name", ["acp_protocol", "a2a_protocol"])
@pytest.mark.parametrize("protocol", [None, "enabled", [], 1])
async def test_protocol_block_must_be_mapping(protocol_name: str, protocol: object) -> None:
    with pytest.raises(
        ValueError,
        match=rf"Agent 'worker': {protocol_name} must be a mapping",
    ):
        await build_agents_from_list([{"name": "worker", protocol_name: protocol}])


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_value", [{"env": ["KEY=value"]}, {"prompt_timeout": True}, {"args": "--acp"}]
)
async def test_disabled_acp_still_rejects_invalid_field_values(invalid_value: dict) -> None:
    source = {
        "name": "worker",
        "acp_protocol": {"enabled": False, **invalid_value},
        "a2a_protocol": {"enabled": True, "endpoint": "http://localhost:9000", "transport": "http"},
    }

    with pytest.raises(ValueError):
        await build_agents_from_list([source])


@pytest.mark.unit
@pytest.mark.parametrize("invalid_key", ["prompt_timout", "endpoint"])
async def test_disabled_acp_still_rejects_invalid_keys(invalid_key: str) -> None:
    source = {
        "name": "worker",
        "acp_protocol": {"enabled": False, invalid_key: "invalid"},
        "a2a_protocol": {"enabled": True, "endpoint": "http://localhost:9000", "transport": "http"},
    }

    with pytest.raises(ValueError, match="Unknown acp_protocol|Obsolete remote ACP"):
        await build_agents_from_list([source])


@pytest.mark.unit
async def test_disabled_acp_preserves_enabled_a2a_with_existing_fields() -> None:
    source = {
        "name": "worker",
        "acp_protocol": {"enabled": False},
        "a2a_protocol": {"enabled": True, "endpoint": "http://localhost:9000", "transport": "http"},
    }

    agents = await build_agents_from_list([source])

    assert agents == {"worker": {"type": "external", "config": source}}


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
