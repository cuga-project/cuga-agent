"""Resolve resource IDs separately from LangGraph checkpoint IDs."""

from typing import Any, Mapping


def logical_thread_id(configurable: Mapping[str, Any] | None, fallback: str | None = None) -> str | None:
    """Prefer the logical ID; accept legacy SDK configs with only thread_id."""
    cfg = configurable or {}
    return cfg.get("logical_thread_id") or cfg.get("thread_id") or fallback


def workspace_thread_id(configurable: Mapping[str, Any] | None, fallback: str | None = None) -> str | None:
    """Spawned agents may share the parent workspace with a new logical thread."""
    cfg = configurable or {}
    return cfg.get("workspace_thread_id") or logical_thread_id(cfg, fallback)
