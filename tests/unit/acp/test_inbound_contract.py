"""Real-pipe contracts for CUGA as an inbound ACP v1 agent."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from acp import PROTOCOL_VERSION, RequestError
from acp.schema import ClientCapabilities, ImageContentBlock, Implementation, TextContentBlock

from tests.fixtures.acp.fake_client import spawn_cuga_agent

pytestmark = pytest.mark.unit


async def _initialize(connection: Any) -> Any:
    return await connection.initialize(
        protocol_version=PROTOCOL_VERSION,
        client_capabilities=ClientCapabilities(),
        client_info=Implementation(name="contract-client", version="1"),
    )


async def _session(connection: Any, root: Path) -> str:
    response = await connection.new_session(cwd=str(root), mcp_servers=[])
    return response.session_id


@pytest.mark.asyncio
async def test_initialize_advertises_truthful_text_only_capabilities() -> None:
    async with spawn_cuga_agent() as (_client, connection, _process):
        response = await _initialize(connection)

    assert response.protocol_version == PROTOCOL_VERSION
    assert response.agent_info.name == "cuga"
    capabilities = response.agent_capabilities
    assert capabilities.load_session is False
    assert capabilities.prompt_capabilities.image is False
    assert capabilities.prompt_capabilities.audio is False
    assert capabilities.prompt_capabilities.embedded_context is False
    assert capabilities.mcp_capabilities.http is False
    assert capabilities.mcp_capabilities.sse is False
    assert capabilities.mcp_capabilities.acp is False


@pytest.mark.asyncio
async def test_sessions_are_isolated_while_turns_reuse_one_cuga_context(tmp_path: Path) -> None:
    async with spawn_cuga_agent() as (client, connection, _process):
        await _initialize(connection)
        first = await _session(connection, tmp_path)
        second = await _session(connection, tmp_path)

        first_response = await connection.prompt(first, [TextContentBlock(type="text", text="alpha")])
        second_response = await connection.prompt(second, [TextContentBlock(type="text", text="beta")])
        followup_response = await connection.prompt(first, [TextContentBlock(type="text", text="gamma")])

    assert [first_response.stop_reason, second_response.stop_reason, followup_response.stop_reason] == [
        "end_turn",
        "end_turn",
        "end_turn",
    ]
    assert client.text(first) == "first:turn-1:alphafirst:turn-2:gamma"
    assert client.text(second) == "first:turn-1:beta"


@pytest.mark.asyncio
async def test_ordered_updates_and_exactly_one_terminal_response(tmp_path: Path) -> None:
    frames: list[Any] = []
    async with spawn_cuga_agent(observers=[frames.append]) as (client, connection, _process):
        await _initialize(connection)
        session_id = await _session(connection, tmp_path)
        response = await connection.prompt(
            session_id,
            [TextContentBlock(type="text", text="one"), TextContentBlock(type="text", text="two")],
        )

    assert response.stop_reason == "end_turn"
    assert [update.content.text for update in client.updates[session_id]] == [
        "first:",
        "turn-1:one\n\ntwo",
    ]
    incoming = [event.message for event in frames if event.direction.value == "incoming"]
    assert incoming
    assert all(frame.get("jsonrpc") == "2.0" for frame in incoming)
    terminal = [frame for frame in incoming if frame.get("result", {}).get("stopReason") == "end_turn"]
    assert len(terminal) == 1


@pytest.mark.asyncio
async def test_rich_content_and_unknown_sessions_return_protocol_errors(tmp_path: Path) -> None:
    async with spawn_cuga_agent() as (_client, connection, _process):
        await _initialize(connection)
        session_id = await _session(connection, tmp_path)
        with pytest.raises(RequestError):
            await connection.prompt(
                session_id,
                [ImageContentBlock(type="image", data="AA==", mimeType="image/png")],
            )
        with pytest.raises(RequestError):
            await connection.prompt(
                "unknown-session",
                [TextContentBlock(type="text", text="hello")],
            )


@pytest.mark.asyncio
async def test_same_session_rejects_concurrent_prompt(tmp_path: Path) -> None:
    async with spawn_cuga_agent(scenario="delay") as (client, connection, _process):
        await _initialize(connection)
        session_id = await _session(connection, tmp_path)
        first = asyncio.create_task(
            connection.prompt(session_id, [TextContentBlock(type="text", text="first")])
        )
        await client.wait_for_update(session_id)
        with pytest.raises(RequestError):
            await connection.prompt(session_id, [TextContentBlock(type="text", text="second")])
        await connection.cancel(session_id)
        assert (await asyncio.wait_for(first, timeout=3)).stop_reason == "cancelled"


@pytest.mark.asyncio
async def test_different_sessions_run_concurrently_and_cancel_independently(tmp_path: Path) -> None:
    async with spawn_cuga_agent(scenario="delay") as (client, connection, _process):
        await _initialize(connection)
        first_session = await _session(connection, tmp_path)
        second_session = await _session(connection, tmp_path)
        first = asyncio.create_task(
            connection.prompt(first_session, [TextContentBlock(type="text", text="first")])
        )
        second = asyncio.create_task(
            connection.prompt(second_session, [TextContentBlock(type="text", text="second")])
        )
        await asyncio.gather(
            client.wait_for_update(first_session),
            client.wait_for_update(second_session),
        )
        await asyncio.gather(connection.cancel(first_session), connection.cancel(second_session))
        responses = await asyncio.wait_for(asyncio.gather(first, second), timeout=3)

    assert [response.stop_reason for response in responses] == ["cancelled", "cancelled"]


@pytest.mark.asyncio
async def test_client_eof_cancels_active_work_and_reaps_child(tmp_path: Path) -> None:
    prompt = None
    async with spawn_cuga_agent(scenario="delay") as (client, connection, process):
        await _initialize(connection)
        session_id = await _session(connection, tmp_path)
        prompt = asyncio.create_task(
            connection.prompt(session_id, [TextContentBlock(type="text", text="still running")])
        )
        await client.wait_for_update(session_id)

    assert process.returncode == 0
    assert prompt is not None
    with pytest.raises(ConnectionError):
        await prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected"),
    [("allow", "permission-allowed"), ("reject", "permission-denied"), ("cancel", "permission-denied")],
)
async def test_permission_choices_resume_the_original_turn(
    tmp_path: Path, decision: str, expected: str
) -> None:
    async with spawn_cuga_agent(scenario="permission", permission=decision) as (
        client,
        connection,
        _process,
    ):
        await _initialize(connection)
        session_id = await _session(connection, tmp_path)
        response = await connection.prompt(session_id, [TextContentBlock(type="text", text="operate")])

    assert response.stop_reason == "end_turn"
    assert client.text(session_id) == expected
    assert len(client.permission_requests) == 1


@pytest.mark.asyncio
async def test_eof_reaps_child_and_empty_stream_has_deterministic_fallback(tmp_path: Path) -> None:
    async with spawn_cuga_agent(scenario="empty") as (client, connection, process):
        await _initialize(connection)
        session_id = await _session(connection, tmp_path)
        response = await connection.prompt(session_id, [TextContentBlock(type="text", text="nothing")])
        assert response.stop_reason == "end_turn"
        assert client.text(session_id) == "Agent completed without a response."

    assert process.returncode == 0
