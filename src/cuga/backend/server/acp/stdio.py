"""Protocol-clean stdio entry point for the inbound CUGA ACP agent."""

from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
import logging
import sys
from typing import Any, Awaitable, Callable


class _DiscardTextStream:
    """Absorb untrusted library stdout without forwarding payloads to stderr."""

    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        return None


class _AcpDiagnosticFilter(logging.Filter):
    """Allow only fixed diagnostics emitted by the ACP adapter itself."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("cuga.backend.server.acp")


class _BoundedDiagnosticFormatter(logging.Formatter):
    """Render fixed-shape diagnostics without exception or payload expansion."""

    _LIMIT = 240

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return rendered.replace("\r", " ").replace("\n", " ")[: self._LIMIT]


def configure_logging() -> None:
    """Install restrictive, bounded stderr diagnostics before heavy imports."""
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.WARNING)
    handler.addFilter(_AcpDiagnosticFilter())
    handler.setFormatter(_BoundedDiagnosticFormatter("%(levelname)s %(name)s: %(message)s"))
    root.handlers[:] = [handler]
    # Silence all inherited third-party diagnostics by default: warning records
    # may contain prompts, outputs, credentials, or exception text.
    root.setLevel(logging.CRITICAL + 1)
    # Existing graph loggers include prompt and answer payloads even at WARNING.
    # The ACP process reports only its own fixed diagnostic messages.
    logging.getLogger("cuga").setLevel(logging.CRITICAL + 1)
    logging.getLogger(__name__).setLevel(logging.WARNING)


def _load_runtime() -> Callable[..., Awaitable[None]]:
    from acp import run_agent

    return run_agent


PREWARM_ENV = "CUGA_ACP_PREWARM"


def _start_warm(agent: Any) -> asyncio.Task[None] | None:
    """Start the agent's warm-up beside the connection, when the host asks for it.

    A host that starts the process ahead of its first prompt, as DAM does, sets
    ``CUGA_ACP_PREWARM=1``, so the first prompt finds the graph ready. A failed
    warm-up is only a diagnostic: the first prompt initializes the agent again
    and reports its own failure.
    """
    import os

    warm = getattr(agent, "warm", None)
    if os.environ.get(PREWARM_ENV, "").strip().lower() not in {"1", "true", "yes"} or not callable(warm):
        return None

    async def run() -> None:
        try:
            await warm()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.getLogger(__name__).warning("ACP warm-up failed (%s)", type(exc).__name__)

    return asyncio.create_task(run())


async def _stdio_streams() -> tuple[Any, Any]:
    from acp import stdio_streams

    return await stdio_streams()


async def serve(
    runner: Any | None = None,
    agent_factory: Callable[[Any | None], Any] | None = None,
    observers: list[Any] | None = None,
) -> int:
    """Run one injected or production CUGA adapter over SDK-managed stdio."""
    configure_logging()
    agent: Any | None = None
    warming: asyncio.Task[None] | None = None
    status = 0
    primary_cancelled = False
    try:
        from cuga.backend.server.acp import create_agent

        # Bind protocol streams to the original descriptors first, then discard
        # untrusted Python stdout writes so they reach neither JSON-RPC nor logs.
        reader, writer = await _stdio_streams()
        agent = (agent_factory or create_agent)(runner)
        runtime = _load_runtime()
        discard = _DiscardTextStream()
        with redirect_stdout(discard), redirect_stderr(discard):
            options = {"observers": list(observers)} if observers is not None else {}
            warming = _start_warm(agent)
            await runtime(
                agent,
                input_stream=writer,
                output_stream=reader,
                use_unstable_protocol=False,
                **options,
            )
    except asyncio.CancelledError:
        primary_cancelled = True
    except Exception as exc:
        logging.getLogger(__name__).error("ACP stdio startup failed (%s)", type(exc).__name__)
        status = 1
    finally:
        if warming is not None and not warming.done():
            warming.cancel()
            await asyncio.gather(warming, return_exceptions=True)
        if agent is not None:
            try:
                discard = _DiscardTextStream()
                with redirect_stdout(discard), redirect_stderr(discard):
                    await agent.shutdown()
            except asyncio.CancelledError:
                primary_cancelled = True
            except Exception as exc:
                logging.getLogger(__name__).error("ACP stdio cleanup failed (%s)", type(exc).__name__)
                status = 1
    if primary_cancelled:
        raise asyncio.CancelledError
    return status


def main(
    runner: Any | None = None,
    agent_factory: Callable[[Any | None], Any] | None = None,
    observers: list[Any] | None = None,
) -> int:
    """Synchronous console-script wrapper around :func:`serve`."""
    try:
        if agent_factory is None and observers is None:
            return asyncio.run(serve(runner=runner))
        return asyncio.run(serve(runner=runner, agent_factory=agent_factory, observers=observers))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
