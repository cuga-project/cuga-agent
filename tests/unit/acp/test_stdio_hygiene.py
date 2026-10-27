"""Raw-frame and diagnostic hygiene contracts for the ACP stdio process."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Any

import pytest
from acp import PROTOCOL_VERSION, RequestError, connect_to_agent
from acp.schema import ClientCapabilities, Implementation, TextContentBlock

from tests.fixtures.acp.fake_client import FakeClient, cuga_agent_launch

pytestmark = pytest.mark.unit


@asynccontextmanager
async def _raw_cuga_agent(
    scenario: str,
) -> AsyncIterator[tuple[FakeClient, Any, Any, list[bytes]]]:
    """Tee physical stdout lines into an SDK connection for exact hygiene checks."""
    command, env, root = cuga_agent_launch(scenario)
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=root,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    raw_lines: list[bytes] = []
    sdk_reader = asyncio.StreamReader()

    async def tee_stdout() -> None:
        while line := await process.stdout.readline():
            raw_lines.append(line)
            sdk_reader.feed_data(line)
        sdk_reader.feed_eof()

    tee_task = asyncio.create_task(tee_stdout())
    client = FakeClient()
    connection = connect_to_agent(client, process.stdin, sdk_reader, receive_timeout=10)
    try:
        yield client, connection, process, raw_lines
    finally:
        await connection.close()
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except (TimeoutError, asyncio.TimeoutError):
            process.kill()
            await process.wait()
        await asyncio.wait_for(tee_task, timeout=2)


def _assert_protocol_only(raw_lines: list[bytes]) -> None:
    assert raw_lines
    for line in raw_lines:
        frame = json.loads(line)
        assert isinstance(frame, dict)
        assert frame.get("jsonrpc") == "2.0"


@pytest.mark.asyncio
async def test_every_stdout_frame_is_json_rpc_and_stderr_redacts_runtime_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "ACP_STDIO_SECRET_9917"
    monkeypatch.setenv("CUGA_ACP_FIXTURE_SECRET", secret)
    async with _raw_cuga_agent("noise") as (
        client,
        connection,
        process,
        raw_lines,
    ):
        initialized = await asyncio.wait_for(
            connection.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(name="hygiene-client", version="1"),
            ),
            timeout=10,
        )
        session = await asyncio.wait_for(
            connection.new_session(cwd=str(tmp_path), mcp_servers=[]), timeout=10
        )
        response = await asyncio.wait_for(
            connection.prompt(session.session_id, [TextContentBlock(type="text", text=secret)]),
            timeout=10,
        )

    assert initialized.protocol_version == PROTOCOL_VERSION
    assert response.stop_reason == "end_turn"
    assert client.text(session.session_id).endswith(secret)
    _assert_protocol_only(raw_lines)
    assert process.stderr is not None
    stderr = await process.stderr.read()
    assert len(stderr) <= 8192
    assert secret.encode() not in stderr
    assert b"Traceback" not in stderr


@pytest.mark.asyncio
async def test_internal_exception_returns_protocol_error_without_stdout_contamination(
    tmp_path: Path,
) -> None:
    async with _raw_cuga_agent("failure") as (
        _client,
        connection,
        process,
        raw_lines,
    ):
        await asyncio.wait_for(
            connection.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(name="hygiene-client", version="1"),
            ),
            timeout=10,
        )
        session = await asyncio.wait_for(
            connection.new_session(cwd=str(tmp_path), mcp_servers=[]), timeout=10
        )
        with pytest.raises(RequestError, match="Internal error"):
            await asyncio.wait_for(
                connection.prompt(
                    session.session_id,
                    [TextContentBlock(type="text", text="private prompt")],
                ),
                timeout=10,
            )

    _assert_protocol_only(raw_lines)
    assert process.stderr is not None
    stderr = await process.stderr.read()
    assert len(stderr) <= 8192
    assert b"private fixture failure" not in stderr
    assert b"private prompt" not in stderr
    assert b"Traceback" not in stderr
