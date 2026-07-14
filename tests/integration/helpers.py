"""Shared integration-test helpers (import-safe under pytest importlib mode)."""

from __future__ import annotations

import uuid


def unique_collection(prefix: str = "test") -> str:
    """Generate a unique collection name matching adapter regex ``[A-Za-z0-9_]{1,63}``."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
