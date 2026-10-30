"""Console entry point for ``cuga``.

``cuga acp`` is routed here, before the Typer CLI is imported: importing ``cuga.cli.main``
sets environment defaults, installs signal handlers and logs to stderr, none of which
belongs in a protocol-clean ACP stdio process. ``cuga acp`` runs exactly what ``cuga-acp`` runs.
"""

from __future__ import annotations

import sys


def main() -> None:
    if sys.argv[1:2] == ["acp"]:
        from cuga.backend.server.acp.stdio import main as acp_main

        raise SystemExit(acp_main())

    from cuga.cli import app

    app()
