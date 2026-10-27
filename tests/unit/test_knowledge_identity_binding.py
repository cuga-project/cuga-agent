# tests/unit/test_knowledge_identity_binding.py
"""Identity-binding regression tests for the knowledge tools (GHSA-ffh4-qv86-xfxv).

``agent_id`` and ``thread_id`` decide *which collection* a knowledge call reads,
writes or deletes. They used to be ordinary tool arguments, which meant the model
filled them in — so a prompt injection could name another agent, session or tenant
and reach that principal's documents. Authentication was proven (localhost plus a
shared token); authorization was taken on the caller's word.

The fix does not add a new check. It removes the model's ability to make the
claim, at every layer that can carry one:

* ``mcp_server`` no longer has an ``agent_id`` parameter at all — the agent is
  resolved server-side on every call;
* the cuga_lite wrapper drops any identity the model passed before supplying the
  run's real thread, where it previously used ``setdefault`` and so let the
  model's value stand;
* the E2B stubs do the same for sandbox code, which is model-written Python and
  can pass whatever it likes;
* the SDK wrappers prefer the construction-time capture, which the model cannot
  reach, over the injected kwarg.

Every test below also asserts that the *unfixed* arrangement would have carried
the attacker's value through, so none of them can pass merely because the value
never arrived.

The HTTP listener guard is covered too: in HTTP mode the MCP server holds the
internal token and would otherwise perform authenticated writes for any local
caller, which makes reaching the port equivalent to holding the token.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

# Imported at module scope on purpose: this file uses
# ``from __future__ import annotations``, so FastAPI resolves the route's
# annotations against module globals. Importing these inside the helper leaves
# ``Request`` unresolvable and every request 422s on a phantom query parameter.
from cuga.backend.knowledge.auth import (
    KnowledgeIdentity,
    ensure_agent_scope_manage_if_needed,
    require_internal_or_auth,
    resolve_collection,
)
from cuga.backend.knowledge.run_context import (
    KnowledgeRunContext,
    RUN_TOKEN_HEADER,
    RUN_TOKEN_META,
    issue_run_token,
)

pytestmark = pytest.mark.unit

MCP_TOOLS = (
    "search_knowledge",
    "ingest_knowledge",
    "ingest_knowledge_url",
    "list_knowledge_documents",
    "delete_knowledge_document",
    "get_ingestion_status",
    "get_knowledge_status",
)

VICTIM_AGENT = "victim-agent-42"
VICTIM_THREAD = "victim-thread-0007"
REAL_THREAD = "real-thread-abc"


# --------------------------------------------------------------------------
# Layer 1 — the MCP tool surface
# --------------------------------------------------------------------------


def test_no_mcp_tool_accepts_an_agent_id():
    """The parameter the proof of concept set is gone from every tool."""
    from cuga.backend.knowledge import mcp_server

    offenders = [
        name for name in MCP_TOOLS if "agent_id" in inspect.signature(getattr(mcp_server, name)).parameters
    ]
    assert offenders == [], f"tools still exposing agent_id: {offenders}"


def test_identity_headers_resolve_the_agent_server_side():
    from cuga.backend.knowledge import mcp_server

    ctx = SimpleNamespace(request_context=SimpleNamespace(meta={RUN_TOKEN_META: "cap"}))
    headers = mcp_server._identity_headers(ctx)
    assert headers == {RUN_TOKEN_HEADER: "cap"}

    # Not vacuous: the helper has no channel for a caller-named agent at all,
    # so the old `_identity_headers(agent_id, thread_id)` call is now a
    # TypeError rather than a quietly honoured claim.
    with pytest.raises(TypeError):
        mcp_server._identity_headers(VICTIM_AGENT, VICTIM_THREAD)


# --------------------------------------------------------------------------
# Layer 2 — the cuga_lite wrapper, which every tool call passes through
# --------------------------------------------------------------------------


def _knowledge_engine(*, agent=True, session=True):
    return SimpleNamespace(
        _config=SimpleNamespace(enabled=True, agent_level_enabled=agent, session_level_enabled=session)
    )


def _fake_knowledge_tool(name, fn):
    """``prepare_tools_and_apps`` builds ``_tools_context`` from ``tool.func``.

    Seeding ``_tools_context`` directly does not work — it gets overwritten — so
    the recording function has to arrive as the tool's own callable.
    """
    fn._response_schemas = {"success": {"type": "object"}}
    return SimpleNamespace(name=name, func=fn, description="d", args_schema=None)


def _build_mock_adapter(tools, tools_context):
    adapter = MagicMock()
    adapter._task_todos_ref = []
    adapter._tools_context = tools_context
    adapter._instructions = ""
    adapter._special_instructions = None
    adapter._static_prompt = None
    adapter._thread_id = REAL_THREAD
    adapter._model = MagicMock()
    adapter._weak_schema_tool_names = frozenset()
    adapter.set_metadata = MagicMock()

    adapter._base_tool_provider = MagicMock()
    adapter._base_tool_provider.get_all_tools = AsyncMock(return_value=tools)
    adapter._base_tool_provider.get_apps = AsyncMock(return_value=[])
    adapter._base_tool_provider.get_tools = AsyncMock(return_value=[])

    rendered = MagicMock()
    rendered.to_string = MagicMock(return_value="")
    adapter._prompt_template = MagicMock()
    adapter._prompt_template.invoke = MagicMock(return_value=rendered)
    return adapter


def _make_state():
    return SimpleNamespace(
        chat_messages=[HumanMessage(content="task")],
        task_todos=None,
        sub_task=None,
        sub_task_app=None,
        api_intent_relevant_apps=None,
        cuga_lite_metadata=None,
        thread_id=REAL_THREAD,
    )


@pytest.mark.asyncio
async def test_wrapper_replaces_model_supplied_identity():
    """A tool call naming another principal is rewritten to this run's own."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )

    seen: dict = {}
    contexts = []

    async def _raw_tool(*args, **kwargs):
        from cuga.backend.knowledge.run_context import current_run_token, verify_run_token

        if current_run_token.get():
            contexts.append(verify_run_token(current_run_token.get()))
        seen.update(kwargs)
        return {"ok": True}

    tool_name = "knowledge_search_knowledge"
    tools_context: dict = {}
    adapter = _build_mock_adapter([_fake_knowledge_tool(tool_name, _raw_tool)], tools_context)

    configurable = {
        "enable_todos": False,
        "shortlisting_tool_threshold": 35,
        "thread_id": REAL_THREAD,
        "knowledge_agent_id": "selected-agent",
        "knowledge_engine": _knowledge_engine(),
    }
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled",
        new=False,
    ):
        node = create_prepare_tools_and_apps_node(adapter, lc_bind_tools_meta={})
        await node(_make_state(), config={"configurable": configurable})

    wrapped = tools_context.get(tool_name)
    assert wrapped is not None, "knowledge tool never reached _tools_context"
    assert wrapped is not _raw_tool, "knowledge tool was never wrapped"

    # The injection an attacker would attempt.
    await wrapped(query="q", scope="session", agent_id=VICTIM_AGENT, thread_id=VICTIM_THREAD)

    assert seen.get("thread_id") == REAL_THREAD
    assert "agent_id" not in seen
    assert contexts[0].agent_id == "selected-agent"
    assert contexts[0].thread_id == REAL_THREAD

    # Not vacuous: the same call against the unwrapped tool carries both values
    # straight through, which is exactly what the wrapper now prevents.
    seen.clear()
    await _raw_tool(query="q", scope="session", agent_id=VICTIM_AGENT, thread_id=VICTIM_THREAD)
    assert seen["thread_id"] == VICTIM_THREAD
    assert seen["agent_id"] == VICTIM_AGENT


@pytest.mark.asyncio
@pytest.mark.parametrize("positional", [False, True])
async def test_prepared_guarded_tool_discards_thread_id_without_runtime_thread(positional):
    """A no-thread run cannot select another citation ledger through ToolGuard."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import DirectLangChainToolsProvider
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider
    from cuga.backend.knowledge.client import KnowledgeClient
    from cuga.backend.knowledge.run_context import current_run_token, verify_run_token

    engine = _knowledge_engine()
    engine._config.default_limit = 5
    engine._config.default_score_threshold = 0.5
    client = KnowledgeClient(engine)
    contexts = []

    async def search(*args, **kwargs):
        contexts.append(verify_run_token(current_run_token.get()))
        return {"results": []}

    client.search_envelope = AsyncMock(side_effect=search)
    tools = client.get_langchain_tools()
    provider = ToolGuardingToolProvider(DirectLangChainToolsProvider(tools), policy_storage=None)
    runtime = SimpleNamespace(guard_tool_call=AsyncMock(return_value=None))
    provider._get_or_create_toolguard_runtime = AsyncMock(return_value=runtime)
    raw = next(tool for tool in tools if tool.name == "knowledge_search_knowledge")
    guarded = provider._wrap_tool(raw, "runtime_tools")
    tools_context = {}
    adapter = _build_mock_adapter([guarded], tools_context)
    adapter._thread_id = None
    state = _make_state()
    state.thread_id = None

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled",
        new=False,
    ):
        node = create_prepare_tools_and_apps_node(adapter, {})
        await node(
            state,
            config={
                "configurable": {
                    "enable_todos": False,
                    "shortlisting_tool_threshold": 35,
                    "knowledge_agent_id": "selected-agent",
                    "knowledge_engine": engine,
                }
            },
        )

    wrapped = tools_context[raw.name]
    if positional:
        result = await wrapped("q", "agent", VICTIM_THREAD)
    else:
        result = await wrapped(query="q", thread_id=VICTIM_THREAD)

    assert result == {"results": []}
    assert client.search_envelope.await_args.kwargs["thread_id"] == ""
    assert runtime.guard_tool_call.await_args.kwargs["arguments"]["thread_id"] == ""
    assert contexts[0].agent_id == "selected-agent"
    assert contexts[0].thread_id is None
    assert current_run_token.get() is None


# --------------------------------------------------------------------------
# Layer 3 — the E2B sandbox stubs, which run model-written Python
# --------------------------------------------------------------------------


def _generate_stub(tool_name="knowledge_search_knowledge"):
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.e2b.e2b_executor import E2BExecutor

    tool_func = SimpleNamespace(
        _knowledge_allowed_scopes=("all", "agent", "session"),
        _knowledge_default_scope="session",
        _knowledge_thread_id=REAL_THREAD,
    )
    return E2BExecutor()._serialize_knowledge_tool_stub(tool_name, tool_func)


@pytest.mark.asyncio
async def test_sandbox_stub_scrubs_identity_from_keyword_arguments():
    source = _generate_stub()
    forwarded: dict = {}

    async def call_api(_app, _tool, kwargs):
        forwarded.update(kwargs)
        return {"ok": True}

    namespace: dict = {"call_api": call_api}
    exec(source, namespace)  # noqa: S102 - exercising generated sandbox code is the point

    await namespace["knowledge_search_knowledge"](
        query="q", scope="session", agent_id=VICTIM_AGENT, thread_id=VICTIM_THREAD
    )

    assert forwarded["thread_id"] == REAL_THREAD
    assert "agent_id" not in forwarded


@pytest.mark.asyncio
async def test_sandbox_stub_ignores_identity_passed_positionally():
    """The old positional map ended in agent_id/thread_id, so this call set them."""
    source = _generate_stub()
    forwarded: dict = {}

    async def call_api(_app, _tool, kwargs):
        forwarded.update(kwargs)
        return {"ok": True}

    namespace: dict = {"call_api": call_api}
    exec(source, namespace)  # noqa: S102

    # query, scope, limit, score_threshold, then what used to be agent_id/thread_id.
    await namespace["knowledge_search_knowledge"]("q", "session", 5, 0.5, VICTIM_AGENT, VICTIM_THREAD)

    assert forwarded["thread_id"] == REAL_THREAD
    assert "agent_id" not in forwarded
    assert VICTIM_AGENT not in forwarded.values()


def test_status_stubs_never_receive_a_thread_id():
    """The two status tools take no thread_id; injecting one would not forward."""
    for tool_name in ("knowledge_get_ingestion_status", "knowledge_get_knowledge_status"):
        source = _generate_stub(tool_name)
        assert 'kwargs["thread_id"] =' not in source


# --------------------------------------------------------------------------
# Layer 4 — the in-process SDK wrappers
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sdk_wrapper_prefers_the_capture_over_a_model_supplied_thread():
    """Sandbox code calls these directly, so ``**_`` is attacker-reachable."""
    from cuga.backend.knowledge.client import KnowledgeClient

    engine = _knowledge_engine()
    engine.default_limit = 5
    engine._config.default_limit = 5
    engine._config.default_score_threshold = 0.5

    client = KnowledgeClient(engine, default_agent_id="real-agent")
    client.search_envelope = AsyncMock(return_value={"results": []})

    tools = client.get_langchain_tools(thread_id=REAL_THREAD)
    search = next(t for t in tools if t.name == "knowledge_search_knowledge")

    await search.coroutine(query="q", scope="session", thread_id=VICTIM_THREAD)

    assert client.search_envelope.await_args.kwargs["thread_id"] == REAL_THREAD


# --------------------------------------------------------------------------
# The HTTP listener guard
# --------------------------------------------------------------------------


async def _drive_middleware(token_header: bytes | None):
    from cuga.backend.knowledge.mcp_server import _RequireInternalToken

    reached = {"inner": False}

    async def inner_app(scope, receive, send):
        reached["inner"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    headers = [(b"host", b"127.0.0.1")]
    if token_header is not None:
        headers.append((b"x-internal-token", token_header))

    sent: list = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await _RequireInternalToken(inner_app)({"type": "http", "headers": headers}, receive, send)
    return reached["inner"], sent[0]["status"]


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, b"", b"not-the-token"])
async def test_http_listener_rejects_callers_without_the_internal_token(header):
    from cuga.backend.knowledge import mcp_server

    with patch.object(mcp_server, "_get_token", return_value="the-real-token"):
        with patch.object(mcp_server, "_reload_token", return_value="the-real-token"):
            reached_inner, status = await _drive_middleware(header)

    assert status == 401
    assert not reached_inner, "request reached the MCP server without a token"


@pytest.mark.asyncio
async def test_http_listener_admits_the_holder_of_the_internal_token():
    from cuga.backend.knowledge import mcp_server

    with patch.object(mcp_server, "_get_token", return_value="the-real-token"):
        reached_inner, status = await _drive_middleware(b"the-real-token")

    assert status == 200
    assert reached_inner


@pytest.mark.asyncio
async def test_http_listener_fails_closed_when_the_token_is_unreadable():
    """A missing token file means "cannot verify", never "no check needed"."""
    from cuga.backend.knowledge import mcp_server

    with patch.object(mcp_server, "_get_token", side_effect=RuntimeError("not ready")):
        reached_inner, status = await _drive_middleware(b"anything")

    assert status == 401
    assert not reached_inner


# --------------------------------------------------------------------------
# The reporter's scenario, at the layer he ran it
# --------------------------------------------------------------------------


def _knowledge_app(*, agent_id="beta"):
    """Real routes-level wiring: real ``require_internal_or_auth``, real
    ``resolve_collection``, real manage gate, on a 127.0.0.1 client.

    This is the layer Moshe Levi (LevinityCyber) reported against — "real
    routes, real auth.py", with the identity supplied as a header rather than
    as a tool argument. A fix that only removes the tool parameter leaves this
    path untouched, so the reporter re-running his proof of concept would see
    identical output and reasonably conclude nothing had been fixed.
    """
    token = "the-shared-mcp-subprocess-token"
    app = FastAPI()
    app.state.app_state = SimpleNamespace(
        internal_token=token,
        agent_id=agent_id,
        knowledge_engine=SimpleNamespace(
            _config=SimpleNamespace(enabled=True, agent_level_enabled=True, session_level_enabled=True)
        ),
        knowledge_provider=None,
        knowledge_config_hash=None,
        stop_events={},
    )

    @app.post("/probe")
    async def probe(
        request: Request,
        scope: str = "agent",
        identity: KnowledgeIdentity = Depends(require_internal_or_auth),
    ):
        ensure_agent_scope_manage_if_needed(identity, scope)
        return {"agent_id": identity.agent_id, "collection": resolve_collection(identity, scope, request)}

    run_token = issue_run_token(KnowledgeRunContext(agent_id, REAL_THREAD, ("agent", "session")))
    client = TestClient(app, raise_server_exceptions=False, client=("127.0.0.1", 50000))
    client.headers[RUN_TOKEN_HEADER] = run_token
    return client, token


def test_reporter_scenario_naming_another_agent_no_longer_reaches_its_collection():
    """beta names alpha and must still land on its own collection.

    Mirrors the reported run: agent 'alpha' owns a canary document in
    kb_agent_alpha; agent 'beta' searches while naming agent_id='alpha'. The
    reported result was that alpha's secret came back to beta, and that beta
    could not reach it under its own identity — the two together are what made
    it a confirmed cross-agent read rather than a misconfiguration.
    """
    client, token = _knowledge_app(agent_id="beta")
    headers = {"X-Internal-Token": token}

    impersonating = client.post(
        "/probe", params={"scope": "agent"}, headers={**headers, "X-Agent-ID": "alpha"}
    )
    assert impersonating.status_code == 200
    body = impersonating.json()

    # The whole of the report in one assertion: naming alpha does not reach alpha.
    assert body["collection"] == "kb_agent_beta"
    assert body["agent_id"] == "beta"
    assert "alpha" not in body["collection"]

    # The control leg from the report: beta's own identity resolves the same
    # collection, so the block above is real isolation and not a blanket denial
    # that would equally have "passed" by breaking knowledge for everyone.
    honest = client.post("/probe", params={"scope": "agent"}, headers=headers)
    assert honest.status_code == 200
    assert honest.json()["collection"] == "kb_agent_beta"


def test_reporter_scenario_holds_for_every_agent_name_tried():
    """The report also showed a flat namespace via tenant-ish agent ids."""
    client, token = _knowledge_app(agent_id="acme_prod")
    headers = {"X-Internal-Token": token}

    for claimed in ("globex_prod", "alpha", "victim-agent-42", ""):
        resp = client.post("/probe", params={"scope": "agent"}, headers={**headers, "X-Agent-ID": claimed})
        assert resp.status_code == 200
        assert resp.json()["collection"] == "kb_agent_acme_prod", f"claimed={claimed!r} escaped"
