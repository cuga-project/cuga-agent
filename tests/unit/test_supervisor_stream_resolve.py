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


@pytest.mark.asyncio
@pytest.mark.parametrize("disable_history", [False, True])
@pytest.mark.parametrize("pause_exit", ["interrupt", "done", "close"])
async def test_approval_pause_preserves_prompt_without_duplicating_resume(
    monkeypatch, disable_history, pause_exit
):
    from cuga.backend.cuga_graph.state.agent_state import default_state
    from cuga.backend.cuga_graph.utils.agent_loop import AgentLoopAnswer, StreamEvent

    prompt = "Delegate this roster UI test to weatherbot"
    checkpoint = default_state(page=None, observation=None, goal=prompt)
    saved = []

    async def fake_save(**kwargs):
        saved.append(kwargs)

    class ApprovalLoop:
        def __init__(self, **kwargs):
            pass

        async def run_stream(self, **kwargs):
            if kwargs.get("resume"):
                yield AgentLoopAnswer(end=True, answer="Approved answer", tools=[])
            else:
                yield StreamEvent(name="SuggestHumanActions", data='{"action_id":"agent_approval"}').format()
                if pause_exit == "interrupt":
                    yield AgentLoopAnswer(end=False, interrupt=True, tools=[])

    graph = MagicMock()
    graph.get_state.return_value = SimpleNamespace(values=checkpoint.model_dump())
    agent = SimpleNamespace(graph=graph, policy_system=None)
    monkeypatch.setattr("cuga.backend.cuga_graph.utils.agent_loop.AgentLoop", ApprovalLoop)
    monkeypatch.setattr(main_mod, "_save_conversation_and_events_async", fake_save)
    monkeypatch.setattr(main_mod, "_knowledge_enabled_for_app_state", lambda _: False)
    monkeypatch.setattr(main_mod, "_rehydrate_citation_ledger", AsyncMock())
    monkeypatch.setattr(main_mod, "_dispatch_slash_for_stream", AsyncMock(return_value=None))
    monkeypatch.setattr(main_mod.app_state, "current_llm", None)
    monkeypatch.setattr(main_mod.app_state, "stop_events", {})
    monkeypatch.setattr(main_mod.app_state, "knowledge_provider", None)
    monkeypatch.setattr(main_mod.app_state, "output_format", None)
    monkeypatch.setattr(main_mod.settings.advanced_features, "langfuse_tracing", False)

    paused = main_mod.event_stream(
        prompt,
        api_mode=True,
        thread_id="approval-thread",
        agent=agent,
        agent_id="roster-supervisor",
        user_id="qa-user",
        disable_history=disable_history,
    )
    if pause_exit == "close":
        assert "SuggestHumanActions" in await anext(paused)
        await paused.aclose()
    else:
        _ = [chunk async for chunk in paused]
    assert len(saved) == 1
    assert saved[0]["events_only"] is True
    assert [event["event_name"] for event in saved[0]["events"]] == ["UserMessage", "SuggestHumanActions"]

    resumed = main_mod.event_stream(
        "",
        api_mode=True,
        thread_id="approval-thread",
        agent=agent,
        agent_id="roster-supervisor",
        user_id="qa-user",
        disable_history=disable_history,
        resume=SimpleNamespace(model_dump_json=lambda: "{}"),
    )
    _ = [chunk async for chunk in resumed]
    assert len(saved) == 2  # Answer is saved before delivery, never again in finally.
    assert saved[1].get("events_only", False) is disable_history
    events = [event for batch in saved for event in batch["events"]]
    assert [event["event_name"] for event in events] == ["UserMessage", "SuggestHumanActions", "Answer"]
    assert events[0]["event_data"] == prompt
    assert json.loads(events[-1]["event_data"])["data"] == "Approved answer"
    for batch in saved:
        assert (batch["agent_id"], batch["thread_id"], batch["user_id"]) == (
            "roster-supervisor",
            "approval-thread",
            "qa-user",
        )


@pytest.fixture(autouse=True)
def _registry_on(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: True,
    )


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
@pytest.mark.parametrize("use_draft", [False, True])
async def test_registered_single_agent_does_not_inherit_global_supervisor(monkeypatch, use_draft):
    monkeypatch.setattr(main_mod.settings.supervisor, "enabled", True)
    config = {"agent": {"name": "Digital Sales Assistant", "kind": "single"}}
    graph = SimpleNamespace(build_graph=AsyncMock())
    state = SimpleNamespace(agent=object(), agent_graphs_cache={})
    with (
        patch.object(main_mod, "app_state", state),
        patch("cuga.backend.server.config_store.load_config", AsyncMock(return_value=(config, 1))),
        patch("cuga.backend.server.config_store.load_draft", AsyncMock(return_value=config)),
        patch("cuga.backend.cuga_graph.graph.DynamicAgentGraph", return_value=graph) as factory,
    ):
        resolved = await main_mod._resolve_stream_agent(_request(), "digital-sales-assistant", use_draft)

    assert resolved is graph
    assert factory.call_args.kwargs["supervisor_enabled"] is False


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
        async def build_graph(self):
            builds.append(self)
            await asyncio.sleep(0.05)

    request = _request()
    state = SimpleNamespace(
        agent=object(),
        agent_graphs_cache={},
        agent_graph_build_locks={},
        agent_graph_generations={},
        policy_system=None,
    )
    with patch.object(main_mod, "app_state", state):
        with patch(
            "cuga.backend.server.config_store.load_config",
            new_callable=AsyncMock,
            return_value=({"agent": {"kind": "single"}}, None),
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


@pytest.mark.asyncio
async def test_invalidate_during_build_does_not_cache_stale_graph():
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowGraph:
        async def build_graph(self):
            started.set()
            await release.wait()

    request = _request()
    state = SimpleNamespace(
        agent=object(),
        agent_graphs_cache={},
        agent_graph_build_locks={},
        agent_graph_generations={},
        policy_system=None,
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
@pytest.mark.parametrize("supervisor_enabled", [False, True])
@pytest.mark.parametrize("checkpoint_supervisor_mode", [None, True])
async def test_event_stream_persists_under_requested_agent_id(
    monkeypatch, supervisor_enabled, checkpoint_supervisor_mode
):
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

            saved["observed_supervisor_mode"] = _kwargs["state"].supervisor_mode
            yield AgentLoopAnswer(end=True, answer="done", tools=[])

    graph = MagicMock()
    if checkpoint_supervisor_mode is not None:
        from cuga.backend.cuga_graph.state.agent_state import default_state

        checkpoint = default_state(page=None, observation=None, goal="previous question")
        checkpoint.supervisor_mode = checkpoint_supervisor_mode
        graph.get_state.return_value = SimpleNamespace(values=checkpoint.model_dump())
    else:
        graph.get_state.return_value = SimpleNamespace(values=None)
    run_agent = SimpleNamespace(
        graph=graph,
        policy_system=None,
        enable_todos=None,
        reflection_enabled=None,
        shortlisting_tool_threshold=None,
        cuga_lite_max_steps=None,
        enable_filesystem_tools=None,
        special_instructions=None,
        chat=None,
        supervisor_enabled=supervisor_enabled,
    )

    monkeypatch.setattr(main_mod.settings.supervisor, "enabled", True)

    monkeypatch.setattr(
        "cuga.backend.cuga_graph.utils.agent_loop.AgentLoop",
        _ImmediateLoop,
    )
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
    assert saved["observed_supervisor_mode"] is supervisor_enabled
    assert any("Answer" in chunk for chunk in chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize("wxo", [False, True])
@pytest.mark.parametrize("turn_id", ["interrupted-turn", ""])
async def test_resumed_stream_uses_checkpoint_memory_disclosure(monkeypatch, wxo, turn_id):
    from cuga.backend.cuga_graph.state.agent_state import default_state
    from cuga.backend.evolve import memory_store

    checkpoint = default_state(page=None, observation=None, goal="")
    checkpoint.service_scope["memory_turn_id"] = turn_id
    checkpoint.thread_id = "thread-1"
    saved: dict = {}
    usage = AsyncMock(return_value={"count": 1, "entity_ids": ["fact-a"]})
    monkeypatch.setattr(memory_store, "get_turn_memory_usage", usage)
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
    graph.get_state.return_value = SimpleNamespace(values=checkpoint.model_dump())
    run_agent = SimpleNamespace(
        graph=graph,
        policy_system=None,
        enable_todos=None,
        reflection_enabled=None,
        shortlisting_tool_threshold=None,
        cuga_lite_max_steps=None,
        enable_filesystem_tools=None,
        special_instructions=None,
        chat=None,
    )

    monkeypatch.setattr(
        "cuga.backend.cuga_graph.utils.agent_loop.AgentLoop",
        _ImmediateLoop,
    )
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
