"""Inbound Agent Client Protocol support.

The optional ACP SDK and CUGA execution graph are imported only when the public
factory is invoked. Importing this package is therefore safe without ``cuga[acp]``.
"""

from __future__ import annotations

from typing import Any


def create_agent(runner: Any | None = None) -> Any:
    """Create the inbound ACP agent, optionally with an injected neutral runner."""
    import os
    from pathlib import Path

    from cuga.backend.server.acp.agent import CugaAcpAgent
    from cuga.backend.server.acp.session_store import AcpSessionStore

    if runner is None:
        from cuga.backend.server.acp.runner import create_runner

        runner = create_runner()
    folder = os.environ.get("CUGA_FOLDER")
    store_path = str(Path(folder) / "acp-sessions.sqlite3") if folder else ":memory:"
    return CugaAcpAgent(runner, session_store=AcpSessionStore(store_path))
