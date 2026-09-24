"""Protocol-clean stdio entry point for the inbound CUGA ACP agent."""

from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import logging
import sys
from typing import Any, Awaitable, Callable


def configure_logging() -> None:
    """Send Python diagnostics to stderr before loading optional/heavy modules."""
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


def _load_runtime() -> Callable[..., Awaitable[None]]:
    from acp import run_agent

    return run_agent


async def _stdio_streams() -> tuple[Any, Any]:
    from acp import stdio_streams

    return await stdio_streams()


async def serve(runner: Any | None = None) -> int:
    """Run one injected or production CUGA adapter over SDK-managed stdio."""
    configure_logging()
    agent: Any | None = None
    status = 0
    primary_cancelled = False
    try:
        from cuga.backend.server.acp import create_agent

        # Bind the protocol streams to the original descriptors first. Redirect
        # subsequent Python stdout writes from CUGA/tools to stderr so they can
        # never be interpreted as JSON-RPC frames by the peer.
        reader, writer = await _stdio_streams()
        agent = create_agent(runner)
        runtime = _load_runtime()
        with redirect_stdout(sys.stderr):
            await runtime(
                agent,
                input_stream=writer,
                output_stream=reader,
                use_unstable_protocol=False,
            )
    except asyncio.CancelledError:
        primary_cancelled = True
    except Exception as exc:
        logging.getLogger(__name__).error("ACP stdio startup failed (%s)", type(exc).__name__)
        status = 1
    finally:
        if agent is not None:
            try:
                await agent.shutdown()
            except asyncio.CancelledError:
                primary_cancelled = True
            except Exception as exc:
                logging.getLogger(__name__).error("ACP stdio cleanup failed (%s)", type(exc).__name__)
                status = 1
    if primary_cancelled:
        raise asyncio.CancelledError
    return status


def main(runner: Any | None = None) -> int:
    """Synchronous console-script wrapper around :func:`serve`."""
    try:
        return asyncio.run(serve(runner=runner))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
