"""Unit tests for the agent_spawn package."""

from pathlib import Path

import pytest


def _make_adapter(spawn_futures=None):
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter

    return AgentGraphAdapter(
        tracker=None,
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref=None,
        base_tool_provider=None,
        spawn_futures_ref=spawn_futures if spawn_futures is not None else {},
    )


def _get_prompt_template():
    from cuga.backend.llm.utils.helpers import load_one_prompt

    prompts_dir = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "cuga"
        / "backend"
        / "cuga_graph"
        / "nodes"
        / "cuga_lite"
        / "prompts"
    )
    return load_one_prompt(str(prompts_dir / "mcp_prompt.jinja2"), relative_to_caller=False)


# ── Phase 1: Configuration ─────────────────────────────────────────────────


def test_agent_spawn_defaults():
    from cuga.config import settings

    assert settings.agent_spawn.max_spawn_depth == 2
    assert settings.agent_spawn.forward_sync_subagent_events is True


def test_agent_spawn_package_importable():
    import importlib

    mod = importlib.import_module("cuga.backend.agent_spawn")
    assert mod is not None


# ── Phase 4: SpawnAgentRuntime ─────────────────────────────────────────────


def test_make_thread_id_format():
    import re

    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    rt = SpawnAgentRuntime([])
    tid = rt._make_thread_id()
    assert re.match(r"^sub_cuga_[0-9a-f]{8}$", tid), f"Unexpected thread_id: {tid}"


@pytest.mark.asyncio
async def test_execute_respects_max_spawn_depth():
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime, _spawn_depth

    token = _spawn_depth.set(99)
    try:
        rt = SpawnAgentRuntime([])
        result = await rt.execute("some task")
        assert result.startswith("[SpawnError]")
    finally:
        _spawn_depth.reset(token)


@pytest.mark.asyncio
async def test_execute_async_returns_future_id(monkeypatch):
    import re

    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    rt = SpawnAgentRuntime([])

    async def _fake_execute(task: str) -> str:
        return "result"

    monkeypatch.setattr(rt, "execute", _fake_execute)

    future_id = await rt.execute_async("do something")
    assert future_id.startswith("future_")
    assert re.match(r"^future_[0-9a-f]{8}$", future_id)


# ── Phase 5: spawn_agent / get_agent_result tools + prompt_utils ───────────


def test_create_spawn_tools_returns_two_tools():
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    tools = create_spawn_tools({})
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert "spawn_agent" in names and "get_agent_result" in names


@pytest.mark.asyncio
async def test_get_agent_result_unknown_future_id():
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    futures: dict = {}
    tools = create_spawn_tools(futures)
    get_result = next(t for t in tools if t.name == "get_agent_result")
    result = await get_result.coroutine(future_id="nonexistent")
    assert "Unknown future_id" in result


@pytest.mark.asyncio
async def test_get_agent_result_done_returns_immediately():
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    futures = {"fid_done": {"status": "done", "result": "hello"}}
    tools = create_spawn_tools(futures)
    get_result = next(t for t in tools if t.name == "get_agent_result")
    result = await get_result.coroutine(future_id="fid_done", timeout=5.0)
    assert result == "hello"


@pytest.mark.asyncio
async def test_get_agent_result_timeout():
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    futures = {"fid_run": {"status": "running", "result": None}}
    tools = create_spawn_tools(futures)
    get_result = next(t for t in tools if t.name == "get_agent_result")
    result = await get_result.coroutine(future_id="fid_run", timeout=0.1)
    assert "[SpawnTimeout]" in result


def test_format_available_agents_block_adhoc_description():
    from cuga.backend.agent_spawn.prompt_utils import format_available_agents_block

    block = format_available_agents_block()
    assert "spawn_agent" in block
    assert "Ad-hoc" in block


# ── Phase 6: Graph Closure (spawn_futures ref) ─────────────────────────────


def test_create_cuga_lite_graph_no_regression():
    from unittest.mock import MagicMock

    from langchain_core.language_models import BaseChatModel

    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import create_cuga_lite_graph

    model = MagicMock(spec=BaseChatModel)
    graph = create_cuga_lite_graph(model=model)
    assert graph is not None


def test_spawn_futures_closure_is_shared():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter

    spawn_futures: dict = {}
    adapter = AgentGraphAdapter(
        tracker=None,
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref=None,
        base_tool_provider=None,
        spawn_futures_ref=spawn_futures,
    )
    assert adapter._spawn_futures is spawn_futures
    spawn_futures["key"] = "value"
    assert adapter._spawn_futures["key"] == "value"


def test_agent_graph_adapter_spawn_futures_default_is_empty_dict():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter

    adapter = AgentGraphAdapter(
        tracker=None,
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref=None,
        base_tool_provider=None,
    )
    assert isinstance(adapter._spawn_futures, dict)
    assert len(adapter._spawn_futures) == 0


# ── Phase 8: MCP Prompt Template ──────────────────────────────────────────


def test_agents_block_absent_when_disabled():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import create_mcp_prompt

    prompt = create_mcp_prompt([], agents_enabled=False, prompt_template=_get_prompt_template())
    assert "available_agents" not in prompt
    assert "spawn_agent" not in prompt


def test_agents_block_present_when_enabled():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import create_mcp_prompt

    prompt = create_mcp_prompt(
        [],
        agents_enabled=True,
        agents_prompt_section="<available_agents>\n- **a**: desc\n</available_agents>",
        prompt_template=_get_prompt_template(),
    )
    assert "<available_agents>" in prompt
    assert "spawn_agent" in prompt


def test_prompt_renders_without_jinja_errors_disabled():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import create_mcp_prompt

    create_mcp_prompt([], agents_enabled=False, prompt_template=_get_prompt_template())


def test_prompt_renders_without_jinja_errors_enabled():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import create_mcp_prompt

    create_mcp_prompt(
        [],
        agents_enabled=True,
        agents_prompt_section="x",
        prompt_template=_get_prompt_template(),
    )


# ── Phase 9: Stream Events ─────────────────────────────────────────────────


def test_emit_noop_without_callback():
    from cuga.backend.agent_spawn.runtime import _emit

    _emit("SpawnAgent", {})  # must not raise


def test_set_event_callback_and_emit():
    from cuga.backend.agent_spawn import runtime

    events: list = []
    runtime.set_event_callback(lambda name, data: events.append((name, data)))
    try:
        runtime._emit("SpawnAgent", {"agent_name": "x"})
        assert len(events) == 1
        assert events[0][0] == "SpawnAgent"
        assert events[0][1]["agent_name"] == "x"
    finally:
        runtime.set_event_callback(None)


@pytest.mark.asyncio
async def test_execute_emits_spawn_agent_and_result_events(monkeypatch):
    from cuga.backend.agent_spawn import runtime
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    rt = SpawnAgentRuntime([])

    events: list = []
    runtime.set_event_callback(lambda name, data: events.append((name, data)))

    async def _fake_run_stream(agent, task, thread_id, cfg, spawn_id=""):
        return "mocked-answer"

    monkeypatch.setattr(rt, "_build_agent", lambda tools, parent_thread_id="": object())
    monkeypatch.setattr(rt, "_build_invoke_config", lambda: {})
    monkeypatch.setattr(rt, "_run_stream", _fake_run_stream)

    try:
        result = await rt.execute("do something")
        assert result == "mocked-answer"
        names = [e[0] for e in events]
        assert "SpawnAgent" in names
        assert "SpawnAgentResult" in names
    finally:
        runtime.set_event_callback(None)


@pytest.mark.asyncio
async def test_forward_sync_subagent_events_includes_subagent_key(monkeypatch):
    from cuga.backend.agent_spawn import runtime
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    rt = SpawnAgentRuntime([])

    events: list = []
    runtime.set_event_callback(lambda name, data: events.append((name, data)))

    class FakeAgent:
        async def stream(self, task, thread_id, config):
            yield ((), {"FinalAnswerAgent": {"script": "print('hi')", "final_answer": "done"}})

    try:
        result = await rt._run_stream(FakeAgent(), "task", "t_id", {})
        assert result == "done"
        code_agent_events = [e for e in events if e[0] == "CodeAgent"]
        assert len(code_agent_events) == 1
        assert code_agent_events[0][1]["subagent"] == "SubCuga"
    finally:
        runtime.set_event_callback(None)


# ── Phase 10: Observability (Langfuse + OTEL) ──────────────────────────────


def test_build_invoke_config_syncs_langfuse_callbacks(monkeypatch):
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    sync_calls = []
    monkeypatch.setattr(
        "cuga.backend.agent_spawn.runtime.sync_langfuse_callbacks_from_config",
        lambda cfg: sync_calls.append(cfg),
    )
    monkeypatch.setattr(
        "cuga.backend.agent_spawn.runtime.get_langfuse_invoke_config",
        lambda: {"callbacks": []},
    )

    parent_cfg = {"configurable": {"thread_id": "parent-thread"}}
    rt = SpawnAgentRuntime([], parent_config=parent_cfg)
    rt._build_invoke_config()

    assert len(sync_calls) == 1
    assert sync_calls[0] is parent_cfg


@pytest.mark.asyncio
async def test_execute_calls_set_session_attribute(monkeypatch):
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    session_calls = []
    monkeypatch.setattr(
        "cuga.backend.agent_spawn.runtime.set_session_attribute",
        lambda sid: session_calls.append(sid),
    )

    parent_cfg = {"configurable": {"thread_id": "parent-thread"}}
    rt = SpawnAgentRuntime([], parent_config=parent_cfg)

    rt._build_invoke_config = lambda: {}
    rt._build_agent = lambda tools, parent_thread_id="": object()

    async def _fake_run_stream(agent, task, thread_id, cfg, spawn_id=""):
        return "done"

    monkeypatch.setattr(rt, "_run_stream", _fake_run_stream)

    await rt.execute("task")
    assert "parent-thread" in session_calls


@pytest.mark.asyncio
async def test_execute_async_calls_set_session_before_create_task(monkeypatch):
    import asyncio

    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    call_order: list[str] = []

    monkeypatch.setattr(
        "cuga.backend.agent_spawn.runtime.set_session_attribute",
        lambda sid: call_order.append(f"set_session:{sid}"),
    )

    original_create_task = asyncio.create_task

    def _tracked_create_task(coro, **kw):
        call_order.append("create_task")
        return original_create_task(coro, **kw)

    monkeypatch.setattr(asyncio, "create_task", _tracked_create_task)

    parent_cfg = {"configurable": {"thread_id": "async-thread"}}
    rt = SpawnAgentRuntime([], parent_config=parent_cfg)

    async def _fake_execute(task, spawn_id=""):
        return "done"

    monkeypatch.setattr(rt, "execute", _fake_execute)

    await rt.execute_async("async task")

    set_idx = next(i for i, s in enumerate(call_order) if s.startswith("set_session"))
    task_idx = call_order.index("create_task")
    assert set_idx < task_idx, f"set_session_attribute not before create_task: {call_order}"
