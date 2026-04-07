"""
Launch helper for the optional Evolve MCP server.

This lets CUGA spawn Evolve through the existing MCP registry using a local
command without hard-coding a vendored repo path. The `evolve` package still
needs to be installed in the active environment.
"""

from __future__ import annotations

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Evolve MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse"),
        default="stdio",
        help="MCP transport to use (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for SSE transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8201,
        help="Port for SSE transport (default: 8201)",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()

    try:
        from evolve.frontend.mcp.mcp_server import mcp
    except ImportError as exc:  # pragma: no cover - exercised through manual startup
        print(
            "Unable to import the optional 'evolve' package required for the Evolve MCP server.",
            file=sys.stderr,
        )
        print(
            "Install the package first, for example by installing altk-evolve into the active environment, "
            "then retry this command.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="sse", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
