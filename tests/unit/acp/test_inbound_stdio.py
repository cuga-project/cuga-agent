"""Unit and narrow real-pipe smoke coverage for the inbound ACP stdio entry point."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
from acp import PROTOCOL_VERSION, spawn_agent_process
from acp.schema import ClientCapabilities, Implementation, TextContentBlock

from cuga.backend.server.agent_protocol.events import AgentStreamEvent

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture(autouse=True)
def _restore_process_logging_state():
    """Keep in-process stdio tests from leaking child-process logging policy."""
    root = logging.getLogger()
    cuga_logger = logging.getLogger("cuga")
    stdio_logger = logging.getLogger("cuga.backend.server.acp.stdio")
    handlers = root.handlers[:]
    levels = (root.level, cuga_logger.level, stdio_logger.level)
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(levels[0])
        cuga_logger.setLevel(levels[1])
        stdio_logger.setLevel(levels[2])


class _Runner:
    def __init__(self, text: str = "smoke answer") -> None:
        self.text = text
        self.shutdown_calls = 0

    async def run(
        self,
        message: str,
        context_id: str | None = None,
        approval: dict[str, Any] | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        del message, context_id, approval
        yield AgentStreamEvent("final_answer", {"text": self.text}, final=True)

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


class _FailingShutdownRunner(_Runner):
    async def shutdown(self) -> None:
        raise RuntimeError("secret cleanup detail")


class _Client:
    def __init__(self) -> None:
        self.text: list[str] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        del session_id, kwargs
        self.text.append(update.content.text)

    async def request_permission(self, **kwargs: Any) -> Any:  # pragma: no cover - smoke does not request it
        del kwargs
        raise AssertionError("unexpected permission request")


@pytest.mark.unit
async def test_serve_constructs_agent_and_passes_it_to_sdk_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuga.backend.server.acp import stdio

    runner = _Runner()
    received: list[Any] = []
    streams = (object(), object())

    async def fake_streams() -> tuple[Any, Any]:
        return streams

    async def fake_runtime(agent: Any, **kwargs: Any) -> None:
        received.append((agent, kwargs))

    monkeypatch.setattr(stdio, "_stdio_streams", fake_streams)
    monkeypatch.setattr(stdio, "_load_runtime", lambda: fake_runtime)
    status = await stdio.serve(runner=runner)

    assert status == 0
    assert received[0][0]._runner is runner
    assert received[0][1] == {
        "input_stream": streams[1],
        "output_stream": streams[0],
        "use_unstable_protocol": False,
    }
    assert runner.shutdown_calls == 1


@pytest.mark.unit
async def test_serve_shuts_down_agent_when_runtime_is_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuga.backend.server.acp import stdio

    runner = _Runner()

    async def fake_streams() -> tuple[Any, Any]:
        return object(), object()

    async def cancelled_runtime(agent: Any, **kwargs: Any) -> None:
        del agent, kwargs
        raise asyncio.CancelledError

    monkeypatch.setattr(stdio, "_stdio_streams", fake_streams)
    monkeypatch.setattr(stdio, "_load_runtime", lambda: cancelled_runtime)
    with pytest.raises(asyncio.CancelledError):
        await stdio.serve(runner=runner)
    assert runner.shutdown_calls == 1


@pytest.mark.unit
async def test_serve_startup_failure_is_safe_and_stdout_clean(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cuga.backend.server.acp import stdio

    async def fake_streams() -> tuple[Any, Any]:
        return object(), object()

    async def failing_runtime(agent: Any, **kwargs: Any) -> None:
        del agent, kwargs
        raise RuntimeError("secret startup detail")

    monkeypatch.setattr(stdio, "_stdio_streams", fake_streams)
    monkeypatch.setattr(stdio, "_load_runtime", lambda: failing_runtime)
    assert await stdio.serve(runner=_Runner()) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "secret startup detail" not in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.unit
async def test_runtime_stdout_is_redirected_after_protocol_stream_binding(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cuga.backend.server.acp import stdio

    async def fake_streams() -> tuple[Any, Any]:
        print("before binding")
        return object(), object()

    async def noisy_runtime(agent: Any, **kwargs: Any) -> None:
        del agent, kwargs
        print("tool noise")

    monkeypatch.setattr(stdio, "_stdio_streams", fake_streams)
    monkeypatch.setattr(stdio, "_load_runtime", lambda: noisy_runtime)
    assert await stdio.serve(runner=_Runner()) == 0
    captured = capsys.readouterr()
    assert captured.out == "before binding\n"
    assert "tool noise" not in captured.err


@pytest.mark.unit
async def test_cleanup_failure_is_sanitized_and_returns_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cuga.backend.server.acp import stdio

    async def fake_streams() -> tuple[Any, Any]:
        return object(), object()

    async def fake_runtime(agent: Any, **kwargs: Any) -> None:
        del agent, kwargs

    monkeypatch.setattr(stdio, "_stdio_streams", fake_streams)
    monkeypatch.setattr(stdio, "_load_runtime", lambda: fake_runtime)
    assert await stdio.serve(runner=_FailingShutdownRunner()) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "secret cleanup detail" not in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.unit
async def test_logging_is_restrictive_bounded_and_configured_to_stderr() -> None:
    from cuga.backend.server.acp.stdio import configure_logging

    configure_logging()
    root = logging.getLogger()
    assert root.level > logging.CRITICAL
    assert logging.getLogger("cuga.backend.cuga_graph").getEffectiveLevel() > logging.CRITICAL
    assert root.handlers
    assert all(getattr(handler, "stream", sys.stderr) is sys.stderr for handler in root.handlers)


@pytest.mark.unit
async def test_console_wrapper_returns_async_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuga.backend.server.acp import stdio

    async def fake_serve(runner: Any | None = None) -> int:
        del runner
        return 7

    monkeypatch.setattr(stdio, "serve", fake_serve)
    assert await asyncio.to_thread(stdio.main) == 7


@pytest.mark.unit
async def test_package_and_stdio_imports_remain_lazy_without_optional_sdk() -> None:
    root = Path(__file__).parents[3]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    code = """
import builtins
import sys
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'acp' or name.startswith('acp.'):
        raise ModuleNotFoundError(name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
import cuga.backend.server.acp
import cuga.backend.server.acp.stdio
assert 'cuga.backend.server.main' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""


@pytest.mark.unit
async def test_sdk_driven_subprocess_smoke_stdout_is_protocol_only_and_stderr_has_no_secrets() -> None:
    root = Path(__file__).parents[3]
    client = _Client()
    prompt_secret = "JOB3_UNIQUE_PROMPT_SECRET_4f9d"
    output_secret = "JOB3_UNIQUE_OUTPUT_SECRET_85ac"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    script = f"""
import logging
from cuga.backend.server.acp.stdio import main
from cuga.backend.server.agent_protocol.events import AgentStreamEvent
class Runner:
    async def run(self, message, context_id=None, approval=None):
        logging.getLogger('cuga.backend.cuga_graph.payload').warning(message)
        third_party = logging.getLogger('third_party.payload')
        third_party.setLevel(logging.WARNING)
        third_party.warning(message)
        third_party.exception('{output_secret}')
        print(message)
        print('{output_secret}')
        import sys
        sys.stderr.write(message)
        sys.stderr.write('{output_secret}')
        yield AgentStreamEvent('final_answer', {{'text': '{output_secret}'}}, final=True)
raise SystemExit(main(runner=Runner()))
"""
    async with spawn_agent_process(
        client,
        sys.executable,
        "-c",
        script,
        env=env,
        cwd=root,
    ) as (connection, process):
        initialized = await connection.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
            client_info=Implementation(name="job3-smoke", version="1"),
        )
        session = await connection.new_session(cwd=str(root), mcp_servers=[])
        response = await connection.prompt(
            session.session_id,
            [TextContentBlock(type="text", text=prompt_secret)],
        )

    assert initialized.protocol_version == PROTOCOL_VERSION
    assert response.stop_reason == "end_turn"
    assert client.text == [output_secret]
    assert process.returncode == 0
    assert process.stderr is not None
    stderr = await process.stderr.read()
    assert prompt_secret.encode() not in stderr
    assert output_secret.encode() not in stderr
