"""execd requests carry what sandbox-api needs for sticky routing across the pool."""

import json

from unittest.mock import Mock

import httpx
import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import ExecdExecutor, connection
from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager
from cuga.config import settings


@pytest.fixture
def fake_sandbox(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/code/context":
            return httpx.Response(200, json={"id": "ctx-1"})
        body = "\n".join(
            json.dumps(e) for e in ({"type": "stdout", "text": "ok"}, {"type": "execution_complete"})
        )
        return httpx.Response(200, text=body)

    def client(cls, timeout, thread=None):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=timeout, headers=cls._auth_headers(thread)
        )

    monkeypatch.setattr(ExecdExecutor, "_client", classmethod(client))
    connection.set_runtime_config({"enabled": True, "url": "https://sandbox.example", "api_key_ref": "K"})
    ExecdExecutor._contexts.clear()
    ExecdExecutor._thread_of_context.clear()
    ExecdExecutor._contexts_url = None
    yield requests
    connection.set_runtime_config(None)
    ExecdExecutor._contexts.clear()
    ExecdExecutor._thread_of_context.clear()


@pytest.mark.asyncio
async def test_thread_id_and_identity_headers_on_every_request(fake_sandbox):
    out = await ExecdExecutor._run_remote("print('x')", "thread-a")
    await ExecdExecutor._run_shell_command("ls", "thread-a")
    await ExecdExecutor.release_thread("thread-a")

    assert out == "ok"
    paths = [r.url.path for r in fake_sandbox]
    assert paths[0] == "/code/context"
    assert "/command" in paths and paths[-1] == "/threads/thread-a"
    assert fake_sandbox[-1].method == "DELETE"

    for r in fake_sandbox:
        assert r.headers["X-API-Key"] == "K"
        assert r.headers["X-Tenant-Id"] and r.headers["X-Agent-Id"]
        if r.method == "POST":
            assert json.loads(r.content)["thread_id"] == "thread-a"
    code_req = next(r for r in fake_sandbox if r.url.path == "/code")
    assert json.loads(code_req.content)["context"] == {"id": "ctx-1", "language": "python"}


@pytest.mark.asyncio
async def test_throwaway_context_still_routes_consistently(fake_sandbox):
    await ExecdExecutor._run_remote("print('x')", None)
    ids = {json.loads(r.content)["thread_id"] for r in fake_sandbox if r.method == "POST"}
    assert len(ids) == 1 and next(iter(ids)).startswith("throwaway-")


@pytest.mark.parametrize(
    "value, expected",
    [
        (False, False),
        (True, True),
        ("false", False),
        ("true", True),
        ("", False),
        ("/etc/ca.crt", "/etc/ca.crt"),
    ],
)
def test_verify_ssl_setting(monkeypatch, value, expected):
    monkeypatch.setattr(settings.advanced_features, "execd_verify_ssl", value, raising=False)
    assert ExecdExecutor._verify_ssl() == expected


@pytest.mark.asyncio
async def test_identity_headers_come_from_conversation_scope(fake_sandbox):
    state = Mock(spec=AgentState)
    state.variables_manager = VariablesManager()
    state.service_scope = {"tenant_id": "tenant-12345", "agent_id": "my-agent"}

    ExecdExecutor._remember_scope("thread-s", state)
    await ExecdExecutor._run_remote("print(1)", "thread-s")
    await ExecdExecutor.release_thread("thread-s")

    for r in fake_sandbox:
        assert r.headers["X-Tenant-Id"] == "tenant-12345"
        assert r.headers["X-Agent-Id"] == "my-agent"
    assert fake_sandbox[-1].method == "DELETE"
    assert "thread-s" not in ExecdExecutor._scope_of_thread


@pytest.mark.asyncio
async def test_release_reaches_sandbox_without_a_cached_context(fake_sandbox):
    # A restart or another replica leaves nothing cached, yet the kernel still lives in the sandbox.
    await ExecdExecutor.release_thread("thread-gone", agent_id="my-agent")

    assert len(fake_sandbox) == 1
    assert fake_sandbox[0].method == "DELETE"
    assert fake_sandbox[0].url.path == "/threads/thread-gone"
    assert fake_sandbox[0].headers["X-Agent-Id"] == "my-agent"
    assert "thread-gone" not in ExecdExecutor._scope_of_thread


@pytest.mark.asyncio
async def test_release_without_thread_id_sends_nothing(fake_sandbox):
    await ExecdExecutor.release_thread(None)

    assert fake_sandbox == []
