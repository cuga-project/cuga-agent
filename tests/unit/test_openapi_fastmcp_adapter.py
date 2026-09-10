"""FastMCP 3.x OpenAPI adapter registration/call shape."""

from __future__ import annotations

import pytest

from cuga.backend.tools_env.registry.config.config_loader import ServiceConfig
from cuga.backend.tools_env.registry.mcp_manager.adapter import new_mcp_from_custom_parser
from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
from cuga.backend.tools_env.registry.mcp_manager.openapi_parser import SimpleOpenAPIParser
from cuga.backend.utils.consts import ServiceType

MINIMAL_OPENAPI = """
openapi: 3.0.0
info:
  title: Demo
  version: 1.0.0
servers:
  - url: http://127.0.0.1:9
paths:
  /accounts:
    get:
      operationId: listAccounts
      summary: List accounts
      description: List all accounts
      parameters:
        - name: limit
          in: query
          required: false
          schema:
            type: integer
      responses:
        '200':
          description: ok
"""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openapi_tools_register_without_kwargs_handlers():
    """FastMCP 3.x rejects **kwargs tool handlers; adapters must use typed params."""
    parser = SimpleOpenAPIParser.from_yaml(MINIMAL_OPENAPI)
    schemas = {
        "demo": ServiceConfig(type=ServiceType.OPENAPI, url="http://127.0.0.1:9/openapi.yaml"),
    }
    mcp = new_mcp_from_custom_parser("http://127.0.0.1:9", parser, "demo", schemas)
    tools = await mcp.list_tools()
    assert [t.name for t in tools] == ["demo_accounts"]
    schema = tools[0].to_mcp_tool().inputSchema
    assert "params" in schema.get("properties", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_manager_calls_local_openapi_tools_with_params_wrapper():
    parser = SimpleOpenAPIParser.from_yaml(MINIMAL_OPENAPI)
    schemas = {
        "demo": ServiceConfig(type=ServiceType.OPENAPI, url="http://127.0.0.1:9/openapi.yaml"),
    }
    mcp = new_mcp_from_custom_parser("http://127.0.0.1:9", parser, "demo", schemas)

    manager = MCPManager({})
    manager.servers["demo"] = mcp
    await manager._register_tools(mcp)

    result = await manager.call_tool("demo_accounts", {"limit": 5})
    assert isinstance(result, list)
    assert result and hasattr(result[0], "text")
    # Backend is unreachable; adapter should return a structured exception payload.
    assert "exception" in result[0].text or "Connection" in result[0].text
