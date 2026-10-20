"""Exercise CUGA consumers over real FastMCP transports without external services."""

import asyncio
from contextlib import asynccontextmanager
import json
import socket

from fastmcp import FastMCP
import pytest
import uvicorn

from cuga.backend.evolve.integration import EvolveIntegration
from cuga.backend.tools_env.registry.config.config_loader import ServiceConfig
from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
from cuga.backend.utils.consts import ServiceType
from cuga.config import settings

pytestmark = pytest.mark.unit


@asynccontextmanager
async def serve_mcp(server, transport):
    # Reserve the port until Uvicorn takes over; no free-port discovery race.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        app = server.http_app(transport=transport)
        host = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
        task = asyncio.create_task(host.serve(sockets=[listener]))
        try:

            async def wait_started():
                while not host.started:
                    if task.done():
                        await task
                        raise RuntimeError("MCP server exited before startup")
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_started(), timeout=10)
            yield f"http://127.0.0.1:{port}" + ("/sse" if transport == "sse" else "/mcp")
        finally:
            host.should_exit = True
            await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["sse", "http"])
async def test_registry_discovers_schemas_and_calls_real_mcp_server(transport):
    server = FastMCP("compatibility")

    @server.tool(name="echo-value")
    def echo(value: str) -> dict:
        return {"value": value}

    async with serve_mcp(server, transport) as url:
        config = ServiceConfig(type=ServiceType.MCP_SERVER, url=url, transport=transport)
        manager = MCPManager({"remote": config})
        assert await manager._initialize_single_fastmcp_server("remote", config)
        tool = manager.tools_by_server["remote"][0]["function"]
        assert tool["parameters"]["properties"]["value"]["type"] == "string"
        assert tool["outputSchema"] is not None
        # The dashed upstream name must survive CUGA's prefixed/sanitized name.
        result = await manager.call_tool("remote_echo_value", {"value": "hello"})
        assert json.loads(result[0].text) == {"value": "hello"}


@pytest.mark.asyncio
async def test_evolve_direct_sse_decodes_real_tool_result(monkeypatch):
    server = FastMCP("evolve-protocol")

    @server.tool
    def lookup(user_id: str) -> str:
        return json.dumps({"user_id": user_id, "matched_count": 1})

    async with serve_mcp(server, "sse") as url:
        monkeypatch.setattr(settings.evolve, "url", url)
        monkeypatch.setattr(settings.evolve, "timeout", 5)
        result = await EvolveIntegration._call_tool_direct("lookup", {"user_id": "alice"})
        assert result == {"user_id": "alice", "matched_count": 1}


@pytest.mark.asyncio
async def test_chat_agent_reuses_live_mcp2_session_and_detects_closed_session():
    from unittest.mock import AsyncMock

    from langchain_mcp_adapters.tools import load_mcp_tools
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    from cuga.backend.cuga_graph.nodes.chat.chat_agent.chat_agent import ChatAgent

    server = FastMCP("chat")

    @server.tool
    def greet(name: str) -> str:
        """Greet a user."""
        return f"hello {name}"

    agent = object.__new__(ChatAgent)
    agent.session = None
    assert not await agent._is_session_valid()
    agent._is_setup = True
    agent.use_regular_chat = False
    agent.tools = None
    agent.setup = AsyncMock(side_effect=AssertionError("healthy session must not reconnect"))
    async with serve_mcp(server, "sse") as url:
        async with sse_client(url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                agent.session = session
                agent.base_tools = await load_mcp_tools(session)
                assert await agent._is_session_valid()
                for name in ("alice", "bob"):
                    result = await agent.execute_tool({"name": "greet", "args": {"name": name}})
                    assert f"hello {name}" in str(result)
                agent.setup.assert_not_awaited()
        assert not await agent._is_session_valid()


@pytest.mark.asyncio
async def test_chat_session_ping_failure_and_cancellation():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from cuga.backend.cuga_graph.nodes.chat.chat_agent.chat_agent import ChatAgent

    agent = object.__new__(ChatAgent)
    for failure in (TimeoutError(), RuntimeError("disconnected")):
        agent.session = SimpleNamespace(send_ping=AsyncMock(side_effect=failure))
        assert not await agent._is_session_valid()
    agent.session = SimpleNamespace(send_ping=AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await agent._is_session_valid()


def test_events_mcp_bridge_adapter_imports_with_supported_sdk():
    # The events bridge imports this lazily when it connects an MCP source.
    # Keep this unskipped: a metadata-only successful lock cannot prove that
    # upstream adapters have migrated away from removed mcp.server.fastmcp APIs.
    from langchain_mcp_adapters.client import MultiServerMCPClient

    assert callable(MultiServerMCPClient)
