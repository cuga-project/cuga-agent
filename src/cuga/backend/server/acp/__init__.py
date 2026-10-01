"""Inbound Agent Client Protocol support.

The optional ACP SDK and CUGA execution graph are imported only when the public
factory is invoked. Importing this package is therefore safe without ``cuga[acp]``.
"""

from __future__ import annotations

from typing import Any


def create_agent(runner: Any | None = None) -> Any:
    """Create the inbound ACP agent, optionally with an injected neutral runner."""
    from cuga.backend.server.acp.agent import CugaAcpAgent

    if runner is None:
        from cuga.backend.server.acp.runner import create_runner

        runner = create_runner()
    return CugaAcpAgent(runner)
