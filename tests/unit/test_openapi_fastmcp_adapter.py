"""FastMCP 4.x OpenAPI adapter registration/call shape."""

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
    """FastMCP 4.x rejects **kwargs tool handlers; adapters must use typed params."""
    parser = SimpleOpenAPIParser.from_yaml(MINIMAL_OPENAPI)
    schemas = {
        "demo": ServiceConfig(type=ServiceType.OPENAPI, url="http://127.0.0.1:9/openapi.yaml"),
    }
    mcp = new_mcp_from_custom_parser("http://127.0.0.1:9", parser, "demo", schemas)
    tools = await mcp.list_tools()
    assert [t.name for t in tools] == ["demo_accounts"]
    schema = tools[0].to_mcp_tool().input_schema
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_openapi_tool_preserves_query_and_response(monkeypatch):
    import json
    from urllib.parse import parse_qs, urlparse

    import requests

    seen = []

    def request(method, url, **kwargs):
        seen.append((method, url, kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"accounts": [{"id": "a1"}]}'
        return response

    monkeypatch.setattr(requests, "request", request)
    schemas = {"demo": ServiceConfig(type=ServiceType.OPENAPI, url="http://example.test")}
    server = new_mcp_from_custom_parser(
        "http://example.test", SimpleOpenAPIParser.from_yaml(MINIMAL_OPENAPI), "demo", schemas
    )
    manager = MCPManager({})
    await manager._register_tools(server)
    result = await manager.call_tool("demo_accounts", {"limit": 5})
    assert parse_qs(urlparse(seen[0][1]).query) == {"limit": ["5"]}
    assert json.loads(result[0].text) == {"accounts": [{"id": "a1"}]}


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["success", "status", "timeout"])
async def test_crm_openapi_wrapper_with_legacy_httpx(monkeypatch, scenario):
    from types import SimpleNamespace

    from fastmcp import Client
    from fastmcp.exceptions import ToolError
    import httpx
    import yaml

    from cuga.demo_tools.crm import wrap_as_mcp

    def respond(request):
        if scenario == "timeout":
            raise httpx.ReadTimeout("request timed out", request=request)
        return httpx.Response(403 if scenario == "status" else 200, json={"accounts": ["a1"]})

    async with httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(respond)
    ) as legacy_client:
        monkeypatch.setattr(
            wrap_as_mcp,
            "httpx",
            SimpleNamespace(
                AsyncClient=lambda **kwargs: legacy_client,
                get=lambda url: SimpleNamespace(json=lambda: yaml.safe_load(MINIMAL_OPENAPI)),
            ),
        )
        async with Client(wrap_as_mcp.create_mcp_server()) as client:
            if scenario == "success":
                result = await client.call_tool("listAccounts", {"limit": 5})
                assert result.data == {"accounts": ["a1"]}
            else:
                with pytest.raises(ToolError):
                    await client.call_tool("listAccounts", {"limit": 5})
