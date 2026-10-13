"""Inputs the agentic_quality judge reads: the agent's real tool calls (with
results) and the registry's tool catalog."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from cuga.backend.cuga_graph.utils.agent_loop import AgentLoop
from cuga.evaluation.agentic_quality_judge import catalog
from cuga.evaluation.agentic_quality_judge.catalog import fetch_tool_catalog
from cuga.evaluation.agentic_quality_judge.tracking import (
    enable_tool_call_tracking,
    extract_tool_calls_with_results,
)

pytestmark = pytest.mark.unit


class _FakeGraph:
    def __init__(self):
        self.configs = []

    def astream(self, input, config=None, **kwargs):
        self.configs.append(config)
        return iter(())


def test_tracking_turns_on_track_tool_calls_for_every_graph_run(monkeypatch):
    def fake_init(self, graph):
        self.graph = graph

    # monkeypatch restores the real AgentLoop.__init__ after the test.
    monkeypatch.setattr(AgentLoop, "__init__", fake_init)
    enable_tool_call_tracking()

    graph = _FakeGraph()
    loop = AgentLoop(graph)
    loop.graph.astream({}, config={"configurable": {"thread_id": "t"}})
    loop.graph.astream({}, config=None)

    assert graph.configs == [{"configurable": {"thread_id": "t", "track_tool_calls": True}}, None]


def test_extract_tool_calls_with_results():
    state = SimpleNamespace(
        tool_calls=[
            {"name": "crm_get_account", "arguments": {"id": 1}, "result": {"ok": True}, "app_name": "crm"},
            {"name": "crm_list_accounts", "arguments": None},
        ]
    )
    assert extract_tool_calls_with_results(state) == [
        {"tool": "crm_get_account", "arguments": {"id": 1}, "result": {"ok": True}, "app_name": "crm"},
        {"tool": "crm_list_accounts", "arguments": {}, "result": None, "app_name": ""},
    ]
    assert extract_tool_calls_with_results(SimpleNamespace(tool_calls=None)) == []


def test_param_rows_normalizes_openapi_and_mcp_shapes():
    openapi = {"properties": {"id": {"type": "integer", "description": "Account id"}}}
    mcp = [{"name": "id", "type": "integer", "required": True, "description": "Account id"}, "junk"]
    assert catalog._param_rows(openapi) == [("id", "integer", "Account id")]
    assert catalog._param_rows(mcp) == [("id", "integer", "Account id")]
    assert catalog._param_rows(None) == []


def _patch_registry(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def client_with_transport(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_with_transport)


def test_fetch_tool_catalog_formats_registry_tools(monkeypatch):
    apps = {
        "crm": {
            "crm_get_account": {
                "method": "GET",
                "path": "/accounts/{id}",
                "description": "Get one account",
                "parameters": {"properties": {"id": {"type": "integer", "description": "Account id"}}},
                "response_schemas": {"200": {"type": "object"}},
            },
            "crm_ping": {"description": "Health check", "parameters": []},
        }
    }

    def handler(request):
        assert request.url.path == "/apis"
        return httpx.Response(200, json=apps)

    _patch_registry(monkeypatch, handler)
    doc = asyncio.run(fetch_tool_catalog(8001))

    assert doc.startswith("# Available Tools")
    assert "### `crm_get_account`" in doc
    assert "`GET /accounts/{id}` — Get one account" in doc
    assert "| `id` | integer | Account id |" in doc
    assert "| _(none)_ | — | No parameters required |" in doc


@pytest.mark.parametrize("response", [httpx.Response(500), httpx.Response(200, json={})])
def test_fetch_tool_catalog_returns_empty_on_failure_or_no_tools(monkeypatch, response):
    _patch_registry(monkeypatch, lambda request: response)
    assert asyncio.run(fetch_tool_catalog(8001)) == ""
