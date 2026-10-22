"""Scoped checkpoint keys for non-UI entry points (W07.1, design §11.1 step 4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from cuga.backend.server.conversation_threads import Scope

EntryPoint = Literal["run", "a2a", "acp", "ext", "draft"]

# Known entry-point prefixes — used by W12.5 for per-prefix pruning
ENTRY_POINT_PREFIXES = ("run:", "a2a:", "acp:", "ext:", "draft:")


def scoped_checkpoint_key(entry: EntryPoint, scope: "Scope", external_id: str) -> str:
    """Build the prefixed checkpoint key used as configurable.thread_id for non-UI entry points.

    ':' separated; components are percent-encoded so a crafted external_id
    cannot forge another scope.

    Web thread IDs are UUIDv4 and never contain ':', so a prefixed key can
    never equal a web thread ID.

    user_id is included for entry points with an authenticated user (/run).
    A2A (service-to-service) runs have no user concept, so the scope's
    user_id may be DEFAULT_USER_ID; it is still included for consistency and
    future traceability.
    """

    def _escape(s: str) -> str:
        return s.replace("%", "%25").replace(":", "%3A")

    parts = [
        entry,
        _escape(scope.tenant_id),
        _escape(scope.instance_id),
        _escape(scope.agent_id),
        _escape(external_id),
    ]
    return ":".join(parts)
