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
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OBSOLETE_BEEAI_KEYS = frozenset(
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


def _string_tuple(value: Sequence[str], *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be a sequence of strings")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must contain only strings")
    return tuple(value)


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

        env = _string_tuple(self.env, field_name="env")
        if any(not _ENV_NAME.fullmatch(name) for name in env):
            raise ValueError("env entries must be environment variable names, not KEY=value values")
        object.__setattr__(self, "env", env)

        if self.cwd is not None:
            cwd = Path(self.cwd).expanduser().resolve(strict=False)
            if not cwd.is_dir():
                raise ValueError("cwd must resolve to an existing directory")
            object.__setattr__(self, "cwd", cwd)

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


def acp_process_config_from_mapping(
    mapping: Mapping[str, Any],
    *,
    name: str | None = None,
    description: str | None = None,
) -> ACPProcessConfig:
    """Convert one supervisor ``acp_protocol`` mapping to validated configuration."""

    if not isinstance(mapping, Mapping):
        raise ValueError("acp_protocol must be a mapping")
    obsolete = sorted(_OBSOLETE_BEEAI_KEYS.intersection(mapping))
    if obsolete:
        joined = ", ".join(obsolete)
        raise ValueError(
            f"Obsolete BeeAI ACP configuration key(s): {joined}. "
            "Migrate to Agent Client Protocol subprocess fields command, args, cwd, and env."
        )
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
