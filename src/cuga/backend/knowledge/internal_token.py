"""Shared access to the internal token that guards localhost-only services.

The backend mints this token at startup, writes it to ``.cuga/.internal_token``
with mode 0600, and points ``CUGA_INTERNAL_TOKEN_FILE`` at it (see
``server/main.py``). Two parties read it back:

* the knowledge MCP server, which presents it to the backend, and
* the registry's MCP client, which presents it to the knowledge MCP server.

Both have to resolve the same path by the same rules, so the resolution lives
here instead of being written twice and drifting.
"""

from __future__ import annotations

import os
import time
from pathlib import Path


def resolve_env(key: str, default: str) -> str:
    """Resolve env var, handling the case where the value is itself an env var name.

    When a subprocess receives env like CUGA_INTERNAL_TOKEN_FILE=CUGA_INTERNAL_TOKEN_FILE
    (literal string instead of a path), detect this circular reference and fall back
    to default.
    """
    val = os.getenv(key, default)
    # Circular: env var value equals its own key name → unresolved, use default
    if val == key:
        return default
    # If the value looks like an unresolved env var name (no path separators,
    # all uppercase+underscore), try resolving it from the real environment
    if val and "/" not in val and "\\" not in val and val == val.upper() and "_" in val:
        resolved = os.getenv(val)
        if resolved and resolved != val:
            return resolved
        return default
    return val


def default_token_file() -> str:
    """Path the backend writes the token to, resolved the same way everywhere."""
    return resolve_env("CUGA_INTERNAL_TOKEN_FILE", str(Path.cwd() / ".cuga" / ".internal_token"))


def read_token_file(path: str | None = None, *, attempts: int = 3) -> str:
    """Read the token, retrying briefly while the backend is still starting.

    Raises ``RuntimeError`` when the file never appears — callers treat that as
    "not ready", never as "no token required".
    """
    resolved = path or default_token_file()
    for attempt in range(attempts):
        try:
            with open(resolved) as f:
                return f.read().strip()
        except FileNotFoundError:
            if attempt < attempts - 1:
                time.sleep(1)
    raise RuntimeError(f"Knowledge backend not ready — token file not found: {resolved}")
