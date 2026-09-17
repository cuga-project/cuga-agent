"""ACP settings dataclass and validation helper.

This module describes CUGA's ACP integration configuration.  It does not
duplicate ACP wire models — it is a local CUGA config layer only.

No ``acp_sdk`` imports are allowed at module level.  The ACP SDK is imported
lazily inside the enabled branch.
"""

from __future__ import annotations

from dataclasses import dataclass

_RESERVED_PREFIXES: frozenset[str] = frozenset(
    {"/api", "/a2a", "/docs", "/health", "/openapi.json", "/redoc", "/run", "/stream"}
)


@dataclass
class ACPSettings:
    """Validated ACP integration settings."""

    enabled: bool
    path_prefix: str
    agent_name: str
    agent_description: str
    supervisor_config_path: str
    auto_approve: bool
    store: str
    store_limit: int
    store_ttl_seconds: int
    auth_required: bool
    enable_playground_cors: bool


def normalize_acp_settings(raw: object) -> ACPSettings:  # noqa: PLR0912
    """Validate and normalize ACP settings from dynaconf config object.

    Raises ``ValueError`` for any invalid configuration.
    """
    enabled: bool = bool(getattr(raw, "enabled", False))
    agent_name: str = str(getattr(raw, "agent_name", "cuga"))
    agent_description: str = str(getattr(raw, "agent_description", ""))
    supervisor_config_path: str = str(getattr(raw, "supervisor_config_path", ""))
    auto_approve: bool = bool(getattr(raw, "auto_approve", False))
    store: str = str(getattr(raw, "store", "memory"))
    store_limit: int = int(getattr(raw, "store_limit", 1000))
    store_ttl_seconds: int = int(getattr(raw, "store_ttl_seconds", 3600))
    auth_required: bool = bool(getattr(raw, "auth_required", True))
    enable_playground_cors: bool = bool(getattr(raw, "enable_playground_cors", False))

    # Validate agent name (non-empty string with no whitespace)
    if not agent_name or not agent_name.strip():
        raise ValueError("acp.agent_name must be a non-empty string")
    if agent_name != agent_name.strip():
        raise ValueError("acp.agent_name must not have leading or trailing whitespace")

    # Normalize path prefix
    raw_prefix: str = str(getattr(raw, "path_prefix", "/acp"))
    if not raw_prefix or raw_prefix.strip() == "":
        raise ValueError("acp.path_prefix must not be empty")

    # Ensure exactly one leading slash
    prefix = "/" + raw_prefix.lstrip("/")
    # Remove trailing slash
    prefix = prefix.rstrip("/") if prefix != "/" else prefix

    if prefix == "/":
        raise ValueError("acp.path_prefix must not be '/' (root path)")

    if not prefix:
        raise ValueError("acp.path_prefix must not be empty after normalization")

    if prefix in _RESERVED_PREFIXES:
        raise ValueError(
            f"acp.path_prefix '{prefix}' is reserved; choose a different prefix. "
            f"Reserved: {sorted(_RESERVED_PREFIXES)}"
        )

    # Validate store
    if store != "memory":
        raise ValueError(f"acp.store '{store}' is not supported in this release; use 'memory'")

    # Validate positive limits
    if store_limit <= 0:
        raise ValueError("acp.store_limit must be a positive integer")
    if store_ttl_seconds <= 0:
        raise ValueError("acp.store_ttl_seconds must be a positive integer")

    return ACPSettings(
        enabled=enabled,
        path_prefix=prefix,
        agent_name=agent_name,
        agent_description=agent_description,
        supervisor_config_path=supervisor_config_path,
        auto_approve=auto_approve,
        store=store,
        store_limit=store_limit,
        store_ttl_seconds=store_ttl_seconds,
        auth_required=auth_required,
        enable_playground_cors=enable_playground_cors,
    )
