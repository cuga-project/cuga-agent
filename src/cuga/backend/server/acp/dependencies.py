"""ACP authentication dependency.

Provides a FastAPI dependency list that enforces CUGA's ``require_chat_access``
when ``auth_required`` is True. Pass the result to ``acp_sdk.server.create_app``
via its ``dependencies`` argument so every SDK-registered endpoint — /ping,
discovery, runs, sessions, and resources — shares one auth policy.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends


def build_auth_dependencies(*, auth_required: bool) -> list[Any]:
    """Return a dependency list appropriate for the given auth setting.

    When *auth_required* is True the list contains ``Depends(require_chat_access)``
    sourced from CUGA's auth layer.  When False an empty list is returned so
    the ACP child application is reachable without credentials.

    The import of ``require_chat_access`` is deferred to this function so that
    the ACP package never pulls in auth machinery on disabled startup paths.
    """
    if not auth_required:
        return []

    from cuga.backend.server.auth.dependencies import require_chat_access

    return [Depends(require_chat_access)]
