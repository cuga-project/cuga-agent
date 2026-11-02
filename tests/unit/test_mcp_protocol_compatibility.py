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
async def test_chat_agent_reuses_live_session_and_cleans_up(monkeypatch):
    from unittest.mock import AsyncMock, patch

    from cuga.backend.cuga_graph.nodes.chat.chat_agent.chat_agent import ChatAgent

    server = FastMCP("chat")

    @server.tool
    def greet(name: str) -> str:
        """Greet a user."""
        return f"hello {name}"

    monkeypatch.setenv("USE_LEGACY_EXECUTION", "false")
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.chat.chat_agent.chat_agent.check_sse_availability",
        AsyncMock(return_value=True),
    )
    agent = ChatAgent()
    assert not await agent._is_session_valid()
    async with serve_mcp(server, "sse") as url:
        agent.sse_url = url
        await agent.setup()
        client = agent.session
        try:
            assert await agent._is_session_valid()
            with patch.object(agent, "setup", new_callable=AsyncMock) as reconnect:
                for name in ("alice", "bob"):
                    result = await agent.execute_tool({"name": "greet", "args": {"name": name}})
                    assert f"hello {name}" in str(result)
                reconnect.assert_not_awaited()
        finally:
            await agent.cleanup()
        assert agent.session is None
        assert agent._connection is None
        assert not client.is_connected()
        assert not await agent._is_session_valid()


@pytest.mark.asyncio
async def test_chat_session_ping_failure_and_cancellation():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from cuga.backend.cuga_graph.nodes.chat.chat_agent.chat_agent import ChatAgent

    agent = object.__new__(ChatAgent)
    for failure in (TimeoutError(), RuntimeError("disconnected")):
        agent.session = SimpleNamespace(ping=AsyncMock(side_effect=failure))
        assert not await agent._is_session_valid()
    agent.session = SimpleNamespace(ping=AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await agent._is_session_valid()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["sse", "streamable_http"])
async def test_events_tools_reconnect_after_discovery_preserve_auth_and_results(monkeypatch, transport):
    from fastmcp.server.dependencies import get_http_headers
    from langchain_core.messages import ToolMessage
    import langchain.mcp

    from cuga.backend.events.tools_bridge import build_tools

    server = FastMCP("events")

    @server.tool
    def echo(value: str) -> dict:
        return {"value": value, "authorization": get_http_headers(include_all=True).get("authorization")}

    adapters = []
    original_adapter = langchain.mcp.MCPAdapter

    def record_adapter(target):
        adapter = original_adapter(target)
        adapters.append(adapter)
        return adapter

    monkeypatch.setattr(langchain.mcp, "MCPAdapter", record_adapter)
    async with serve_mcp(server, "sse" if transport == "sse" else "http") as url:
        tools = await build_tools(
            ["current_time"],
            {"events": {"url": url, "transport": transport, "headers": {"Authorization": "Bearer test"}}},
            tool_filter=["echo"],
        )
        # Raw names retain persisted tool filters; no surprise server-name prefix.
        assert [tool.name for tool in tools] == ["current_time", "echo"]
        assert not adapters[0].client.is_connected()
        tool = tools[1]
        assert tool.args["value"]["type"] == "string"
        for value in ("first", "second"):
            result = await tool.ainvoke(
                {"type": "tool_call", "id": value, "name": "echo", "args": {"value": value}}
            )
            assert isinstance(result, ToolMessage)
            assert result.status == "success"
            assert result.artifact["structured_content"] == {"value": value, "authorization": "Bearer test"}
            assert not adapters[0].client.is_connected()


@pytest.mark.asyncio
async def test_events_tool_errors_cancellation_and_transport_failures(monkeypatch):
    from langchain_core.tools import ToolException
    import langchain.mcp

    from cuga.backend.events.tools_bridge import load_mcp_tools

    server = FastMCP("events-errors")
    started = asyncio.Event()

    @server.tool
    def fail() -> str:
        raise ValueError("tool rejected input")

    @server.tool
    async def slow() -> str:
        started.set()
        await asyncio.Event().wait()
        return "unreachable"

    adapters = []
    original_adapter = langchain.mcp.MCPAdapter

    def record_adapter(target):
        adapter = original_adapter(target)
        adapters.append(adapter)
        return adapter

    monkeypatch.setattr(langchain.mcp, "MCPAdapter", record_adapter)
    async with serve_mcp(server, "http") as url:
        tools = {
            tool.name: tool for tool in await load_mcp_tools({"events": {"url": url, "transport": "http"}})
        }
        with pytest.raises(ToolException, match="tool rejected input"):
            await tools["fail"].ainvoke({})
        task = asyncio.create_task(tools["slow"].ainvoke({}))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not adapters[0].client.is_connected()
    # Transport errors remain exceptions, not successful empty results.
    with pytest.raises(Exception) as failure:
        await asyncio.wait_for(tools["fail"].ainvoke({}), timeout=5)
    assert not isinstance(failure.value, ToolException)


@pytest.mark.asyncio
async def test_chat_setup_cancellation_closes_owned_adapter(monkeypatch):
    from unittest.mock import AsyncMock
    from langchain.mcp import MCPAdapter

    from cuga.backend.cuga_graph.nodes.chat.chat_agent.chat_agent import ChatAgent

    started = asyncio.Event()

    async def wait_for_cancellation(self):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setenv("USE_LEGACY_EXECUTION", "false")
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.chat.chat_agent.chat_agent.check_sse_availability",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(MCPAdapter, "list_tools", wait_for_cancellation)
    agent = ChatAgent()
    async with serve_mcp(FastMCP("setup-cancel"), "sse") as url:
        agent.sse_url = url
        task = asyncio.create_task(agent.setup())
        await asyncio.wait_for(started.wait(), timeout=5)
        client = agent.session
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert agent.session is None
        assert agent._connection is None
        assert not client.is_connected()


@pytest.mark.asyncio
async def test_events_rejects_unsupported_legacy_options_with_existing_fallback(caplog):
    from cuga.backend.events.tools_bridge import load_mcp_tools

    assert await load_mcp_tools({"legacy": {"url": "http://127.0.0.1:9", "session_kwargs": {}}}) == []
    assert "Unsupported MCP options" in caplog.text


@pytest.mark.asyncio
async def test_installed_evolve_sse_reconnects_and_preserves_host_trace(tmp_path, monkeypatch):
    """Cross the CUGA consumer and installed Evolve server boundary, without an LLM."""
    pytest.importorskip("altk_evolve")
    from altk_evolve import telemetry
    from altk_evolve.config.evolve import EvolveConfig
    from altk_evolve.config.filesystem import FilesystemSettings
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.frontend.mcp import mcp_server
    import fastmcp.telemetry as fastmcp_telemetry
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    client = EvolveClient(
        config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path)))
    )
    monkeypatch.setattr(mcp_server, "get_client", lambda: client)
    monkeypatch.setenv("EVOLVE_OTEL_ENABLED", "false")
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("cuga.consumer.test")
    monkeypatch.setattr(telemetry, "_TRACER", tracer)
    monkeypatch.setattr(fastmcp_telemetry, "otel_get_tracer", lambda *args, **kwargs: tracer)
    try:
        async with serve_mcp(mcp_server.mcp, "sse") as url:
            config = ServiceConfig(type=ServiceType.MCP_SERVER, url=url, transport="sse")
            manager = MCPManager({"evolve": config})
            assert await manager._initialize_single_fastmcp_server("evolve", config)
            names = {tool["function"]["name"] for tool in manager.tools_by_server["evolve"]}
            assert "evolve_retrieve_user_facts" in names
            monkeypatch.setattr(settings.evolve, "url", url)
            monkeypatch.setattr(settings.evolve, "timeout", 5)
            # The real consumer closes each connection; the second call must reconnect.
            for user in ("private-alice", "private-bob"):
                with tracer.start_as_current_span("cuga.request") as parent:
                    result = await EvolveIntegration._call_tool_direct(
                        "retrieve_user_facts", {"namespace_id": "absent", "user_id": user}
                    )
                assert result["matched_count"] == 0
                memory = [
                    span
                    for span in exporter.get_finished_spans()
                    if span.name == "evolve.mcp.retrieve_user_facts"
                ][-1]
                assert memory.context.trace_id == parent.get_span_context().trace_id
                assert memory.parent is not None
                assert user not in str(memory.attributes)
    finally:
        provider.shutdown()
