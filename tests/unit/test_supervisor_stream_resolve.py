"""Unknown / unpublished agents must not silently chat as cuga-default."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from cuga.backend.server import main as main_mod

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _registry_on(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: True,
    )


def _patch_agent_loop(monkeypatch, fake_loop) -> None:
    """Make ``event_stream`` build ``fake_loop`` instead of ``AgentLoop``, for this test only.

    ``event_stream`` imports ``AgentLoop`` and then ``controller`` inside its body. If
    ``controller`` is first imported while ``agent_loop.AgentLoop`` is patched, its
    module-level ``from ...agent_loop import AgentLoop`` binds the fake for the rest of
    the process; monkeypatch only restores ``agent_loop.AgentLoop``. Import
    ``controller`` first so it always binds the real class.
    """
    import cuga.backend.cuga_graph.utils.controller  # noqa: F401

    monkeypatch.setattr("cuga.backend.cuga_graph.utils.agent_loop.AgentLoop", fake_loop)


def _request():
    request = MagicMock()
    request.app.state.draft_app_state = None
    return request


def test_unpublished_agent_does_not_fall_back_to_default():
    request = _request()
    with patch.object(main_mod, "app_state") as mock_state:
        mock_state.agent = object()
        mock_state.agent_graphs_cache = {}
        with patch(
            "cuga.backend.server.config_store.load_config",
            new_callable=AsyncMock,
            return_value=(None, None),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(main_mod._resolve_stream_agent(request, "trip-supervisor", False))
    assert exc.value.status_code == 404
    assert "trip-supervisor" in exc.value.detail


def test_graph_build_error_does_not_fall_back_to_default():
    request = _request()
    with patch.object(main_mod, "app_state") as mock_state:
        mock_state.agent = object()
        mock_state.agent_graphs_cache = {}
        with patch(
            "cuga.backend.server.config_store.load_config",
            new_callable=AsyncMock,
            return_value=({"agent": {"kind": "single"}}, None),
        ):
            with patch(
                "cuga.backend.cuga_graph.graph.DynamicAgentGraph",
                side_effect=RuntimeError("boom"),
            ):
                with pytest.raises(HTTPException) as exc:
                    asyncio.run(main_mod._resolve_stream_agent(request, "trip-supervisor", False))
    assert exc.value.status_code == 500
    assert "trip-supervisor" in exc.value.detail


def test_registry_disabled_ignores_non_default_agent_id(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: False,
    )
    request = _request()
    default_agent = object()
    with patch.object(main_mod, "app_state") as mock_state:
        mock_state.agent = default_agent
        mock_state.agent_graphs_cache = {}
        resolved = asyncio.run(main_mod._resolve_stream_agent(request, "trip-supervisor", False))
    assert resolved is default_agent


@pytest.mark.asyncio
async def test_stream_forwards_x_agent_id_as_execution_identity(monkeypatch):
    captured: dict = {}

    async def fake_event_stream(*_args, **kwargs):
        captured.update(kwargs)
        if False:
            yield "data: {}\n\n"

    monkeypatch.setattr(main_mod, "event_stream", fake_event_stream)
    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: True,
    )

    isolated_graph = object()
    request = MagicMock()
    request.headers.get.side_effect = lambda key, default=None: {
        "X-Agent-ID": "sales-east",
        "X-Thread-ID": "thread-1",
        "X-Use-Draft": "",
        "X-Disable-History": "",
    }.get(key, default)
    request.app.state.draft_app_state = None

    with patch.object(main_mod, "get_query", new_callable=AsyncMock, return_value="hello"):
        with patch.object(main_mod, "get_attachment_snapshot", new_callable=AsyncMock, return_value=None):
            with patch.object(
                main_mod,
                "_resolve_stream_agent",
                new_callable=AsyncMock,
                return_value=isolated_graph,
            ):
                response = await main_mod.stream(request, current_user=None)
                async for _ in response.body_iterator:
                    pass

    assert captured["agent"] is isolated_graph
    assert captured["agent_id"] == "sales-east"
    assert captured["current_llm"] is None


@pytest.mark.asyncio
async def test_concurrent_first_stream_shares_one_graph():
    builds: list[object] = []

    class CountingGraph:
        async def build_graph(self, **_kwargs):
            builds.append(self)
            await asyncio.sleep(0.05)

    request = _request()
    state = SimpleNamespace(
        agent=object(),
        agent_graphs_cache={},
        agent_graph_build_locks={},
        agent_graph_generations={},
        policy_system=None,
        checkpointer=None,
    )
    with patch.object(main_mod, "app_state", state):
        with patch(
            "cuga.backend.server.config_store.load_config",
            new_callable=AsyncMock,
            return_value=({"agent": {"kind": "single"}}, 4),
        ):
            with patch(
                "cuga.backend.cuga_graph.graph.DynamicAgentGraph",
                side_effect=lambda *_args, **_kwargs: CountingGraph(),
            ):
                first, second = await asyncio.gather(
                    main_mod._resolve_stream_agent(request, "sales-east", False),
                    main_mod._resolve_stream_agent(request, "sales-east", False),
                )

    assert first is second
    assert len(builds) == 1
    assert state.agent_graphs_cache[("sales-east", False)] is first
    assert first._checkpoint_config_version == "4"


@pytest.mark.asyncio
async def test_invalidate_during_build_does_not_cache_stale_graph():
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowGraph:
        async def build_graph(self, **_kwargs):
            started.set()
            await release.wait()

    request = _request()
    state = SimpleNamespace(
        agent=object(),
        agent_graphs_cache={},
        agent_graph_build_locks={},
        agent_graph_generations={},
        policy_system=None,
        checkpointer=None,
    )
    request.app.state.app_state = state

    with patch.object(main_mod, "app_state", state):
        with patch(
            "cuga.backend.server.config_store.load_config",
            new_callable=AsyncMock,
            return_value=({"agent": {"kind": "single"}}, None),
        ):
            with patch(
                "cuga.backend.cuga_graph.graph.DynamicAgentGraph",
                side_effect=lambda *_args, **_kwargs: SlowGraph(),
            ):
                with patch(
                    "cuga.backend.server.manage_routes.helpers._referrer_ids_for_agent",
                    new_callable=AsyncMock,
                    return_value=[],
                ):
                    task = asyncio.create_task(main_mod._resolve_stream_agent(request, "sales-east", False))
                    await started.wait()
                    from cuga.backend.server.manage_routes.helpers import invalidate_agent_graph_cache

                    await invalidate_agent_graph_cache(request, "sales-east", draft=False, published=True)
                    release.set()
                    graph = await task

    assert graph is not None
    assert ("sales-east", False) not in state.agent_graphs_cache


@pytest.mark.asyncio
async def test_event_stream_persists_under_requested_agent_id(monkeypatch):
    saved: dict = {}

    async def fake_save(**kwargs):
        saved.update(kwargs)

    class _ImmediateLoop:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def get_langfuse_trace_id(self):
            return None

        async def run_stream(self, **_kwargs):
            from cuga.backend.cuga_graph.utils.agent_loop import AgentLoopAnswer

            assert _kwargs["state"].service_scope["published_config_version"] == "4"
            yield AgentLoopAnswer(end=True, answer="done", tools=[])

    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(values=None))
    graph.get_state.side_effect = AssertionError("event_stream must read state via aget_state")
    run_agent = SimpleNamespace(
        graph=graph,
        _checkpoint_config_version="4",
        policy_system=None,
        enable_todos=None,
        reflection_enabled=None,
        shortlisting_tool_threshold=None,
        cuga_lite_max_steps=None,
        enable_filesystem_tools=None,
        special_instructions=None,
        chat=None,
    )

    _patch_agent_loop(monkeypatch, _ImmediateLoop)
    monkeypatch.setattr(main_mod, "_save_conversation_and_events_async", fake_save)
    monkeypatch.setattr(main_mod, "_knowledge_enabled_for_app_state", lambda _state: False)
    monkeypatch.setattr(main_mod, "_rehydrate_citation_ledger", AsyncMock())
    monkeypatch.setattr(main_mod, "_dispatch_slash_for_stream", AsyncMock(return_value=None))
    monkeypatch.setattr(main_mod.app_state, "agent_id", "cuga-default")
    monkeypatch.setattr(main_mod.app_state, "current_llm", "default-llm")
    monkeypatch.setattr(main_mod.app_state, "stop_events", {})
    monkeypatch.setattr(main_mod.app_state, "output_format", None)
    monkeypatch.setattr(main_mod.app_state, "knowledge_provider", None)

    chunks = []
    async for chunk in main_mod.event_stream(
        "hello",
        api_mode=True,
        thread_id="thread-1",
        agent=run_agent,
        agent_id="sales-east",
        current_llm=None,
    ):
        chunks.append(chunk)

    assert saved["agent_id"] == "sales-east"
    assert any(chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize("is_resume", [False, True])
@pytest.mark.parametrize("graph_version", ["3", "4"])
async def test_stream_rejects_pending_approval_after_published_config_changes(
    monkeypatch, is_resume, graph_version
):
    from cuga.backend.cuga_graph.state.agent_state import default_state

    checkpoint = default_state(page=None, observation=None, goal="")
    checkpoint.service_scope["published_config_version"] = "3"
    graph = MagicMock()
    graph.aget_state = AsyncMock(
        return_value=SimpleNamespace(values=checkpoint.model_dump(), next=("WaitForResponse",))
    )
    run_agent = SimpleNamespace(graph=graph, _checkpoint_config_version=graph_version)

    def unexpected_loop(**_kwargs):
        raise AssertionError("A stale approval must not invoke the graph")

    _patch_agent_loop(monkeypatch, unexpected_loop)
    load_config = AsyncMock(return_value=({}, "4"))
    monkeypatch.setattr("cuga.backend.server.config_store.load_config", load_config)
    monkeypatch.setattr(main_mod, "_assert_run_thread_access", AsyncMock())
    monkeypatch.setattr(main_mod.app_state, "stop_events", {})
    monkeypatch.setattr(main_mod.app_state, "output_format", None)

    chunks = [
        chunk
        async for chunk in main_mod.event_stream(
            "next turn",
            api_mode=True,
            resume=SimpleNamespace(model_dump_json=lambda: "{}") if is_resume else None,
            thread_id="thread-1",
            agent=run_agent,
            agent_id="sales-east",
        )
    ]

    assert len(chunks) == 1
    assert "older agent configuration" in chunks[0]
    graph.aget_state.assert_awaited_once()
    if graph_version == "3":
        load_config.assert_awaited_once_with(None, "sales-east")
    else:
        load_config.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("wxo", [False, True])
@pytest.mark.parametrize("turn_id", ["interrupted-turn", ""])
async def test_resumed_stream_uses_checkpoint_memory_disclosure(monkeypatch, wxo, turn_id):
    from cuga.backend.cuga_graph.state.agent_state import default_state
    from cuga.backend.evolve import memory_store

    checkpoint = default_state(page=None, observation=None, goal="")
    checkpoint.service_scope["memory_turn_id"] = turn_id
    checkpoint.service_scope["published_config_version"] = "4"
    checkpoint.thread_id = "thread-1"
    saved: dict = {}
    usage = AsyncMock(return_value={"count": 1, "entity_ids": ["fact-a"]})
    monkeypatch.setattr(memory_store, "get_turn_memory_usage", usage)
    monkeypatch.setattr("cuga.backend.server.config_store.load_config", AsyncMock(return_value=({}, "4")))
    monkeypatch.setattr(main_mod.settings.evolve, "enabled", True)
    monkeypatch.setattr(main_mod.settings.advanced_features, "wxo_integration", wxo)

    async def fake_save(**kwargs):
        saved.update(kwargs)

    class _ImmediateLoop:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def get_langfuse_trace_id(self):
            return None

        async def run_stream(self, **_kwargs):
            assert _kwargs["state"] is None
            assert _kwargs["resume"] is not None
            from cuga.backend.cuga_graph.utils.agent_loop import AgentLoopAnswer

            yield AgentLoopAnswer(end=True, answer="done", tools=[])

    graph = MagicMock()
    graph.aget_state = AsyncMock(
        return_value=SimpleNamespace(values=checkpoint.model_dump(), next=("WaitForResponse",))
    )
    graph.get_state.side_effect = AssertionError("event_stream must read state via aget_state")
    run_agent = SimpleNamespace(
        graph=graph,
        _checkpoint_config_version="4",
        policy_system=None,
        enable_todos=None,
        reflection_enabled=None,
        shortlisting_tool_threshold=None,
        cuga_lite_max_steps=None,
        enable_filesystem_tools=None,
        special_instructions=None,
        chat=None,
    )

    _patch_agent_loop(monkeypatch, _ImmediateLoop)
    monkeypatch.setattr(main_mod, "_save_conversation_and_events_async", fake_save)
    monkeypatch.setattr(main_mod, "_knowledge_enabled_for_app_state", lambda _state: False)
    monkeypatch.setattr(main_mod, "_rehydrate_citation_ledger", AsyncMock())
    monkeypatch.setattr(main_mod, "_dispatch_slash_for_stream", AsyncMock(return_value=None))
    monkeypatch.setattr(main_mod.app_state, "agent_id", "cuga-default")
    monkeypatch.setattr(main_mod.app_state, "current_llm", "default-llm")
    monkeypatch.setattr(main_mod.app_state, "stop_events", {})
    monkeypatch.setattr(main_mod.app_state, "output_format", None)
    monkeypatch.setattr(main_mod.app_state, "knowledge_provider", None)

    chunks = []
    async for chunk in main_mod.event_stream(
        "hello",
        api_mode=True,
        resume=SimpleNamespace(model_dump_json=lambda: "{}"),
        user_id="alice",
        thread_id="thread-1",
        agent=run_agent,
        agent_id="sales-east",
        current_llm=None,
    ):
        chunks.append(chunk)

    assert saved["agent_id"] == "sales-east"
    persisted = next(event["event_data"] for event in saved["events"] if event["event_name"] == "Answer")
    if wxo and not turn_id:
        assert persisted == "done"
    else:
        payload = json.loads(persisted)
        assert payload["data"] == "done"
        assert payload.get("memory_turn_id", "") == turn_id
    if wxo:
        assert any("done" in chunk for chunk in chunks)
        assert all("memory_turn_id" not in chunk for chunk in chunks)
    if not wxo and turn_id:
        usage.assert_awaited_once_with(turn_id=turn_id, agent_id="sales-east", user_id="alice")
        assert any("memory_usage" in chunk and "fact-a" in chunk for chunk in chunks)
    else:
        usage.assert_not_awaited()


def _cheap_entry_graph(*_args, **_kwargs):
    """A real, unbuilt CugaEntryGraph with no tools (same recipe as test_entry_graph)."""
    from cuga.backend.cuga_graph.entry_graph import CugaEntryGraph
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface

    tool_provider = MagicMock(spec=ToolProviderInterface)
    tool_provider.initialize = AsyncMock()
    tool_provider.get_apps = AsyncMock(return_value=[])
    return CugaEntryGraph(None, tool_provider=tool_provider, supervisor_enabled=False)


async def _resolve_real_graph(request, state, agent_id: str, use_draft: bool):
    from cuga.backend.llm.models import LLMManager

    config = {"agent": {"kind": "single"}}
    with (
        patch.object(main_mod, "app_state", state),
        patch(
            "cuga.backend.server.config_store.load_config",
            new_callable=AsyncMock,
            return_value=(config, None),
        ),
        patch("cuga.backend.server.config_store.load_draft", new_callable=AsyncMock, return_value=config),
        patch(
            "cuga.backend.cuga_graph.policy.configurable.create_agent_policy_system",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("cuga.backend.cuga_graph.graph.DynamicAgentGraph", side_effect=_cheap_entry_graph),
        patch.object(LLMManager, "get_model", return_value=MagicMock()),
    ):
        return await main_mod._resolve_stream_agent(request, agent_id, use_draft)


def _shared_saver_state(saver):
    return SimpleNamespace(
        agent=object(),
        agent_graphs_cache={},
        agent_graph_build_locks={},
        agent_graph_generations={},
        policy_system=None,
        checkpointer=saver,
    )


@pytest.mark.asyncio
async def test_published_rebuild_after_invalidation_keeps_thread_state():
    """A publish invalidation rebuilds the graph; the shared saver keeps the thread (#774 W04.2)."""
    from langgraph.checkpoint.memory import MemorySaver

    from cuga.backend.server.manage_routes.helpers import invalidate_agent_graph_cache

    saver = MemorySaver()
    state = _shared_saver_state(saver)
    request = _request()
    request.app.state.app_state = state
    cfg = {"configurable": {"thread_id": "published-thread"}}

    first = await _resolve_real_graph(request, state, "sales-east", False)
    await first.graph.aupdate_state(
        cfg, {"input": "before publish", "url": "seeded"}, as_node=first.chat.chat_agent.name
    )

    with patch(
        "cuga.backend.server.manage_routes.helpers._referrer_ids_for_agent",
        new_callable=AsyncMock,
        return_value=[],
    ):
        await invalidate_agent_graph_cache(request, "sales-east", draft=False, published=True)
    assert ("sales-east", False) not in state.agent_graphs_cache

    second = await _resolve_real_graph(request, state, "sales-east", False)

    assert second is not first
    assert second.graph is not first.graph
    snapshot = await second.graph.aget_state(cfg)
    assert snapshot.values["input"] == "before publish"
    assert snapshot.values["url"] == "seeded"


@pytest.mark.asyncio
async def test_draft_graph_does_not_use_the_shared_saver():
    """Draft builds keep their own MemorySaver.

    Judged by storage, not identity: a strict-msgpack compile wraps every saver
    in a ``with_allowlist`` clone, so ``graph.checkpointer is saver`` never holds.
    """
    from langgraph.checkpoint.memory import MemorySaver

    saver = MemorySaver()
    state = _shared_saver_state(saver)
    request = _request()
    cfg = {"configurable": {"thread_id": "shared-thread-id"}}

    published = await _resolve_real_graph(request, state, "sales-east", False)
    await published.graph.aupdate_state(
        cfg, {"input": "published turn"}, as_node=published.chat.chat_agent.name
    )
    assert await saver.aget_tuple(cfg) is not None

    draft = await _resolve_real_graph(request, state, "sales-east", True)
    assert draft is not published

    # The draft graph cannot see the published thread...
    assert not (await draft.graph.aget_state(cfg)).values
    # ...and its own writes never reach the shared saver.
    draft_cfg = {"configurable": {"thread_id": "draft-only-thread"}}
    await draft.graph.aupdate_state(draft_cfg, {"input": "draft turn"}, as_node=draft.chat.chat_agent.name)
    assert (await draft.graph.aget_state(draft_cfg)).values["input"] == "draft turn"
    assert await saver.aget_tuple(draft_cfg) is None
