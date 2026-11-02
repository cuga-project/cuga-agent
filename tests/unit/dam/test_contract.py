"""Real ACP pipe contract for DAM's injected CUGA agent factory."""

from pathlib import Path

import pytest
from acp import PROTOCOL_VERSION
from acp.schema import ClientCapabilities, Implementation, TextContentBlock

from tests.fixtures.acp.fake_client import spawn_cuga_agent

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_real_pipe_lists_and_loads_session_after_process_restart(tmp_path: Path) -> None:
    store_folder = tmp_path / ".cuga"
    async with spawn_cuga_agent(store_folder=store_folder, dam=True) as (_client, connection, _process):
        capabilities = (
            await connection.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(name="contract-client", version="1"),
            )
        ).agent_capabilities
        assert capabilities.load_session is True
        assert capabilities.mcp_capabilities.http is True
        assert capabilities.mcp_capabilities.sse is True
        session_id = (await connection.new_session(cwd=str(tmp_path), mcp_servers=[])).session_id
        await connection.prompt(session_id, [TextContentBlock(type="text", text="hello")])

    async with spawn_cuga_agent(store_folder=store_folder, dam=True) as (client, connection, _process):
        await connection.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
            client_info=Implementation(name="contract-client", version="1"),
        )
        listed = await connection.list_sessions(cwd=".")
        assert session_id in [item.session_id for item in listed.sessions]
        await connection.load_session(cwd=str(tmp_path), session_id=session_id, mcp_servers=[])
        assert [update.session_update for update in client.updates[session_id]] == [
            "user_message_chunk",
            "agent_message_chunk",
            "agent_message_chunk",
        ]
        assert client.text(session_id) == "first:turn-1:hello"
