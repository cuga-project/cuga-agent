"""Protocol-clean ACP stdio entry point for DAM's CUGA image."""

from __future__ import annotations

from cuga.backend.server.acp.stdio import main

from .factory import create_agent


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(agent_factory=create_agent))
