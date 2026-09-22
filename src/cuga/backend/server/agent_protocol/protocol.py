"""Protocol-neutral runner interface shared by A2A and ACP adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol

if TYPE_CHECKING:
    from cuga.backend.server.agent_protocol.events import AgentStreamEvent


class AgentRunner(Protocol):
    """Minimal contract every agent runner must satisfy.

    Both the A2A and ACP adapters accept anything that structurally
    matches this protocol — no shared base class required.
    """

    def run(
        self,
        message: str,
        context_id: str | None = None,
        approval: dict[str, Any] | None = None,
    ) -> AsyncIterator["AgentStreamEvent"]:  # pragma: no cover
        """Yield ``AgentStreamEvent`` objects for ``message``.

        ``context_id`` identifies the conversation thread; ``None`` means
        a stateless one-shot invocation.  ``approval`` carries a structured
        approve/deny signal for HITL flows; runners that do not support HITL
        may ignore it.
        """
        ...
