"""Validated configuration for one outbound ACP subprocess delegation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_DEFAULT_STARTUP_TIMEOUT = 15.0
_DEFAULT_PROMPT_TIMEOUT = 120.0
_DEFAULT_SHUTDOWN_GRACE_PERIOD = 5.0
_MAX_TIMEOUT = 3600.0
_MAX_ENV_NAMES = 64
_MAX_ENV_NAME_LENGTH = 256
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ACP_MAPPING_KEYS = frozenset(
    {
        "enabled",
        "command",
        "args",
        "cwd",
        "env",
        "startup_timeout",
        "prompt_timeout",
        "shutdown_grace_period",
    }
)
_OBSOLETE_REMOTE_ACP_KEYS = frozenset(
    {
        "endpoint",
        "agent_name",
        "verify_tls",
        "auth",
        "bearer_token",
        "bearer_token_env",
        "poll_interval",
        "polling_interval",
        "manifest",
        "capabilities",
        "transport",
    }
)


def _safe_key_name(key: object) -> str:
    if not isinstance(key, str):
        return f"<{type(key).__name__}>"
    sanitized = "".join(
        character if character.isprintable() and character not in "`" else "?" for character in key
    )
    return sanitized[:80] or "<empty>"


def validate_acp_protocol_mapping(mapping: Mapping[str, Any]) -> None:
    """Validate the protocol wrapper fields without requiring an enabled process command."""

    if not isinstance(mapping, Mapping):
        raise ValueError("acp_protocol must be a mapping")
    obsolete = sorted(key for key in mapping if isinstance(key, str) and key in _OBSOLETE_REMOTE_ACP_KEYS)
    if obsolete:
        joined = ", ".join(obsolete)
        raise ValueError(
            f"Obsolete remote ACP configuration key(s): {joined}. "
            "Migrate to Agent Client Protocol subprocess fields command, args, cwd, and env."
        )
    unknown = [
        _safe_key_name(key) for key in mapping if not isinstance(key, str) or key not in _ACP_MAPPING_KEYS
    ]
    if unknown:
        accepted = ", ".join(sorted(_ACP_MAPPING_KEYS))
        raise ValueError(
            f"Unknown acp_protocol configuration key(s): {', '.join(sorted(unknown))}. "
            f"Accepted keys: {accepted}."
        )
    enabled = mapping.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("acp_protocol enabled must be a boolean")
    if not enabled:
        disabled_mapping = dict(mapping)
        if "command" not in disabled_mapping:
            disabled_mapping["command"] = "disabled-acp-agent"
        ACPProcessConfig(
            command=disabled_mapping["command"],
            args=disabled_mapping.get("args", ()),
            cwd=disabled_mapping.get("cwd"),
            env=disabled_mapping.get("env", ()),
            startup_timeout=disabled_mapping.get("startup_timeout", _DEFAULT_STARTUP_TIMEOUT),
            prompt_timeout=disabled_mapping.get("prompt_timeout", _DEFAULT_PROMPT_TIMEOUT),
            shutdown_grace_period=disabled_mapping.get(
                "shutdown_grace_period", _DEFAULT_SHUTDOWN_GRACE_PERIOD
            ),
        )


def _string_tuple(
    value: Sequence[str],
    *,
    field_name: str,
    max_items: int | None = None,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be a sequence of strings")
    if max_items is not None and len(value) > max_items:
        raise ValueError(f"{field_name} contains too many entries")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} must contain only strings")
        result.append(item)
    return tuple(result)


def _timeout(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} timeout must be numeric")
    converted = float(value)
    if not math.isfinite(converted) or converted <= 0 or converted > _MAX_TIMEOUT:
        raise ValueError(
            f"{field_name} timeout must be greater than zero and at most {_MAX_TIMEOUT:g} seconds"
        )
    return converted


@dataclass(frozen=True)
class ACPProcessConfig:
    """Safe, normalized launch configuration for one ACP agent process."""

    command: str
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    env: tuple[str, ...] = ()
    startup_timeout: float = _DEFAULT_STARTUP_TIMEOUT
    prompt_timeout: float = _DEFAULT_PROMPT_TIMEOUT
    shutdown_grace_period: float = _DEFAULT_SHUTDOWN_GRACE_PERIOD
    display_name: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.command, str) or not self.command.strip():
            raise ValueError("command must be a non-empty string")
        object.__setattr__(self, "command", self.command.strip())
        object.__setattr__(self, "args", _string_tuple(self.args, field_name="args"))

        env = _string_tuple(self.env, field_name="env", max_items=_MAX_ENV_NAMES)
        if any(len(name) > _MAX_ENV_NAME_LENGTH or not _ENV_NAME.fullmatch(name) for name in env):
            raise ValueError("env entries must be environment variable names, not KEY=value values")
        object.__setattr__(self, "env", env)

        object.__setattr__(
            self,
            "startup_timeout",
            _timeout(self.startup_timeout, field_name="startup"),
        )
        object.__setattr__(
            self,
            "prompt_timeout",
            _timeout(self.prompt_timeout, field_name="prompt"),
        )
        object.__setattr__(
            self,
            "shutdown_grace_period",
            _timeout(self.shutdown_grace_period, field_name="shutdown grace period"),
        )

        from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
            VIRTUAL_WORKSPACE_ROOT,
            resolve_workspace_path,
            thread_workspace_root,
        )

        physical_root = thread_workspace_root(None).resolve()
        if self.cwd is None:
            cwd = physical_root
        else:
            raw_cwd = str(self.cwd).strip()
            normalized = raw_cwd.replace("\\", "/")
            candidate = Path(raw_cwd).expanduser()
            resolved_candidate = candidate.resolve(strict=False)
            if candidate.is_absolute() and not (
                normalized == VIRTUAL_WORKSPACE_ROOT
                or normalized.startswith(f"{VIRTUAL_WORKSPACE_ROOT}/")
                or resolved_candidate == physical_root
                or physical_root in resolved_candidate.parents
            ):
                raise ValueError("cwd must stay within the configured CUGA workspace")
            try:
                cwd = (
                    resolved_candidate
                    if candidate.is_absolute()
                    and physical_root in (resolved_candidate, *resolved_candidate.parents)
                    else resolve_workspace_path(raw_cwd, thread_id=None, operation="cwd")
                )
            except ValueError as exc:
                raise ValueError("cwd must stay within the configured CUGA workspace") from exc
        if self.cwd is not None and not cwd.is_dir():
            raise ValueError("cwd must resolve to an existing workspace directory")
        object.__setattr__(self, "cwd", cwd)


def validate_external_protocol_config(
    agent_config: Mapping[str, Any],
    *,
    require_enabled: bool = False,
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """Validate complete external protocol configuration before transport selection.

    A2A mappings without ``enabled`` retain the historical direct-wrapper behavior:
    a non-empty mapping is active. Explicit enablement, when supplied, must be Boolean.
    """

    if not isinstance(agent_config, Mapping):
        raise ValueError("external agent config must be a mapping")

    blocks: dict[str, Mapping[str, Any] | None] = {}
    enabled: list[str] = []
    for name in ("acp_protocol", "a2a_protocol"):
        if name not in agent_config:
            blocks[name] = None
            continue
        block = agent_config[name]
        if not isinstance(block, Mapping):
            raise ValueError(f"{name} must be a mapping")
        blocks[name] = block
        explicit = block.get("enabled")
        if name == "acp_protocol" and not isinstance(explicit, bool):
            raise ValueError("acp_protocol enabled must be a boolean")
        if name == "a2a_protocol" and "enabled" in block and not isinstance(explicit, bool):
            raise ValueError("a2a_protocol enabled must be a boolean")
        is_enabled = explicit if "enabled" in block else bool(block)
        if is_enabled:
            enabled.append(name)

    if len(enabled) > 1:
        raise ValueError("exactly one enabled protocol block is allowed")
    if require_enabled and not enabled:
        raise ValueError("exactly one enabled protocol block is required")
    if blocks["acp_protocol"] is not None:
        validate_acp_protocol_mapping(blocks["acp_protocol"])
    return blocks["acp_protocol"], blocks["a2a_protocol"]


def acp_process_config_from_mapping(
    mapping: Mapping[str, Any],
    *,
    name: str | None = None,
    description: str | None = None,
) -> ACPProcessConfig:
    """Convert one supervisor ``acp_protocol`` mapping to validated configuration."""

    validate_acp_protocol_mapping(mapping)
    return ACPProcessConfig(
        command=mapping.get("command", ""),
        args=mapping.get("args", ()),
        cwd=mapping.get("cwd"),
        env=mapping.get("env", ()),
        startup_timeout=mapping.get("startup_timeout", _DEFAULT_STARTUP_TIMEOUT),
        prompt_timeout=mapping.get("prompt_timeout", _DEFAULT_PROMPT_TIMEOUT),
        shutdown_grace_period=mapping.get("shutdown_grace_period", _DEFAULT_SHUTDOWN_GRACE_PERIOD),
        display_name=name,
        description=description,
    )
