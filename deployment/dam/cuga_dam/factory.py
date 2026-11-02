"""Construct the DAM-configured ACP agent without changing CUGA's default."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def create_agent(runner: Any | None = None) -> Any:
    from cuga.backend.server.acp.agent import CugaAcpAgent

    from .runner import create_runner
    from .session_store import AcpSessionStore

    folder = os.environ.get("CUGA_FOLDER")
    store_path = str(Path(folder) / "acp-sessions.sqlite3") if folder else ":memory:"
    active_runner = runner if runner is not None else create_runner()
    return CugaAcpAgent(active_runner, session_store=AcpSessionStore(store_path))
