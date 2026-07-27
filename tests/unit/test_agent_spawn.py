"""Unit tests for the agent_spawn package."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


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
    assert "fid_run" not in futures


@pytest.mark.asyncio
async def test_get_agent_result_timeout_cancels_task(monkeypatch):
    import asyncio

    from cuga.backend.agent_spawn import runtime as rt
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    rt.clear_runtime_caches()
    try:
        parent_cfg = {"configurable": {"thread_id": "thr-timeout"}}
        futures = rt.thread_spawn_futures("thr-timeout")

        async def _fake_execute(self, task, spawn_id="", share_workspace=False):
            await asyncio.sleep(60)
            return "never"

        monkeypatch.setattr(rt.SpawnAgentRuntime, "execute", _fake_execute)
        tools = create_spawn_tools(futures, parent_config=parent_cfg)
        spawn = next(t for t in tools if t.name == "spawn_agent")
        get_result = next(t for t in tools if t.name == "get_agent_result")

        future_id = await spawn.coroutine(task="slow", mode="async")
        result = await get_result.coroutine(future_id=future_id, timeout=0.1)
        assert "[SpawnTimeout]" in result
        assert future_id not in futures
        # Let CancelledError propagate and done-callback clear the bucket.
        for _ in range(20):
            if not rt.pending_spawn_tasks("thr-timeout"):
                break
            await asyncio.sleep(0.01)
        assert rt.pending_spawn_tasks("thr-timeout") == []
    finally:
        rt.clear_runtime_caches()


@pytest.mark.asyncio
async def test_execute_and_store_records_cancelled(monkeypatch):
    import asyncio

    from cuga.backend.agent_spawn import runtime as rt

    rt.clear_runtime_caches()
    try:
        runtime = rt.SpawnAgentRuntime.from_parent(
            parent_config={"configurable": {"thread_id": "thr-c"}},
            spawn_futures_ref={},
        )

        async def _boom(*_a, **_k):
            raise asyncio.CancelledError()

        monkeypatch.setattr(runtime, "execute", _boom)
        with pytest.raises(asyncio.CancelledError):
            await runtime._execute_and_store("future_cancel1", "t")
        assert runtime._spawn_futures["future_cancel1"]["status"] == "cancelled"
    finally:
        rt.clear_runtime_caches()


@pytest.mark.asyncio
async def test_cancel_spawn_future_cancels_running_task():
    import asyncio

    from cuga.backend.agent_spawn import runtime as rt

    rt.clear_runtime_caches()
    try:
        parent = "thr-cancel"
        future_id = "future_deadbeef"

        async def _hang():
            await asyncio.sleep(60)

        task = asyncio.create_task(_hang())
        rt.thread_spawn_futures(parent)[future_id] = {
            "status": "running",
            "result": None,
            "error": None,
        }
        rt._track_task(parent, future_id, task)

        assert rt.cancel_spawn_future(parent, future_id) is True
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()
        entry = rt.thread_spawn_futures(parent)[future_id]
        assert entry["status"] == "timeout"
    finally:
        rt.clear_runtime_caches()


@pytest.mark.asyncio
async def test_wait_pending_spawns_cancels_on_timeout():
    import asyncio

    from cuga.backend.agent_spawn import runtime as rt

    rt.clear_runtime_caches()
    try:
        parent = "thr-wait"

        async def _hang():
            await asyncio.sleep(60)

        task = asyncio.create_task(_hang())
        rt._track_task(parent, "future_wait01", task)
        await rt.wait_pending_spawns(parent, timeout=0.05, cancel_on_timeout=True)
        assert task.cancelled() or task.done()
        assert rt.pending_spawn_tasks(parent) == []
    finally:
        rt.clear_runtime_caches()


def test_wait_pending_spawns_default_timeout_is_five_seconds():
    import inspect

    from cuga.backend.agent_spawn.runtime import wait_pending_spawns

    params = inspect.signature(wait_pending_spawns).parameters
    assert params["timeout"].default == 5.0
    assert params["cancel_on_timeout"].default is True


def test_agent_loop_waits_pending_spawns_with_short_timeout():
    src = Path("src/cuga/backend/cuga_graph/utils/agent_loop.py").read_text()
    assert "wait_pending_spawns(self.thread_id, timeout=5.0)" in src


def test_stop_and_reset_clear_spawn_caches():
    src = Path("src/cuga/backend/server/main.py").read_text()
    assert "clear_runtime_caches(thread_id)" in src
    assert "Failed to clear spawn caches on stop" in src
    assert "Failed to clear spawn caches on reset" in src


def test_format_available_agents_block_adhoc_description():
    from cuga.backend.agent_spawn.prompt_utils import format_available_agents_block

    block = format_available_agents_block()
    assert "spawn_agent" in block
    assert "Ad-hoc" in block
    assert "json.dumps" in block
    assert "print" in block
    assert "opaque" in block
    assert "share_workspace" in block
    assert "both ways" in block


def test_resolve_thread_ids_isolated_by_default():
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    rt = SpawnAgentRuntime([], parent_config={"configurable": {"thread_id": "parent-1"}})
    conversation_id, workspace_id = rt._resolve_thread_ids(share_workspace=False)
    assert conversation_id.startswith("sub_cuga_")
    assert workspace_id == conversation_id


def test_resolve_thread_ids_share_workspace_reuses_parent():
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    rt = SpawnAgentRuntime([], parent_config={"configurable": {"thread_id": "parent-1"}})
    conversation_id, workspace_id = rt._resolve_thread_ids(share_workspace=True)
    assert conversation_id.startswith("sub_cuga_")
    assert workspace_id == "parent-1"
    assert conversation_id != workspace_id


@pytest.mark.asyncio
async def test_execute_share_workspace_sets_workspace_thread_id_in_config(monkeypatch):
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    parent_cfg = {"configurable": {"thread_id": "parent-ws"}}
    rt = SpawnAgentRuntime([], parent_config=parent_cfg)
    captured = {}

    monkeypatch.setattr(rt, "_build_agent", lambda tools: object())

    async def _fake_run_stream(agent, task, thread_id, cfg, spawn_id=""):
        captured["thread_id"] = thread_id
        captured["cfg"] = cfg
        return "ok"

    monkeypatch.setattr(rt, "_run_stream", _fake_run_stream)
    monkeypatch.setattr("cuga.backend.agent_spawn.runtime.set_session_attribute", lambda sid: None)
    monkeypatch.setattr(
        "cuga.backend.agent_spawn.runtime.sync_langfuse_callbacks_from_config",
        lambda cfg: None,
    )
    monkeypatch.setattr(
        "cuga.backend.agent_spawn.runtime.get_langfuse_invoke_config",
        lambda: {"configurable": {}},
    )

    await rt.execute("task", share_workspace=True)
    assert captured["thread_id"].startswith("sub_cuga_")
    assert captured["cfg"]["configurable"]["workspace_thread_id"] == "parent-ws"


def test_agents_prompt_warns_against_nested_fstrings_for_spawn_task():
    from cuga.backend.agent_spawn.prompt_utils import format_available_agents_block
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import create_mcp_prompt

    prompt = create_mcp_prompt(
        [],
        agents_enabled=True,
        agents_prompt_section=format_available_agents_block(),
        prompt_template=_get_prompt_template(),
    )
    assert "json.dumps" in prompt
    assert "triple quotes" in prompt
    assert "Inspect results before follow-up" in prompt
    assert "share_workspace" in prompt
    assert "Named agent" not in prompt
    assert 'name="<agent_name>"' not in prompt


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
    token = runtime.set_event_callback(lambda name, data: events.append((name, data)))
    try:
        runtime._emit("SpawnAgent", {"agent_name": "x"})
        assert len(events) == 1
        assert events[0][0] == "SpawnAgent"
        assert events[0][1]["agent_name"] == "x"
    finally:
        runtime.reset_event_callback(token)


@pytest.mark.asyncio
async def test_event_callbacks_are_isolated_across_concurrent_contexts():
    """Regression: concurrent sessions must not share one process-global callback."""
    import asyncio

    from cuga.backend.agent_spawn import runtime

    q_a: asyncio.Queue = asyncio.Queue()
    q_b: asyncio.Queue = asyncio.Queue()

    async def session(label: str, queue: asyncio.Queue):
        token = runtime.set_event_callback(lambda name, data: queue.put_nowait((label, name, data)))
        try:
            await asyncio.sleep(0.01)  # overlap with the other session
            runtime._emit("SpawnAgent", {"agent_name": label})
            await asyncio.sleep(0.01)
        finally:
            runtime.reset_event_callback(token)

    await asyncio.gather(session("A", q_a), session("B", q_b))

    assert q_a.qsize() == 1
    assert q_b.qsize() == 1
    assert q_a.get_nowait()[0] == "A"
    assert q_b.get_nowait()[0] == "B"


@pytest.mark.asyncio
async def test_execute_emits_spawn_agent_and_result_events(monkeypatch):
    from cuga.backend.agent_spawn import runtime
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    rt = SpawnAgentRuntime([])

    events: list = []
    token = runtime.set_event_callback(lambda name, data: events.append((name, data)))

    async def _fake_run_stream(agent, task, thread_id, cfg, spawn_id=""):
        return "mocked-answer"

    monkeypatch.setattr(rt, "_build_agent", lambda tools: object())
    monkeypatch.setattr(rt, "_build_invoke_config", lambda workspace_thread_id="": {})
    monkeypatch.setattr(rt, "_run_stream", _fake_run_stream)

    try:
        result = await rt.execute("do something")
        assert result == "mocked-answer"
        names = [e[0] for e in events]
        assert "SpawnAgent" in names
        assert "SpawnAgentResult" in names
        spawn = next(e for e in events if e[0] == "SpawnAgent")
        assert spawn[1]["spawn_id"]
    finally:
        runtime.reset_event_callback(token)


def test_build_agent_returns_fresh_instance_each_call(monkeypatch):
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime, _SUBAGENT_SPECIAL_INSTRUCTIONS

    created = []

    class FakeAgent:
        def __init__(self, tools=None, special_instructions=None):
            created.append(self)
            self.tools = tools
            self.special_instructions = special_instructions

    monkeypatch.setattr("cuga.sdk.CugaAgent", FakeAgent)

    rt = SpawnAgentRuntime([])
    a1 = rt._build_agent([])
    a2 = rt._build_agent([])
    assert a1 is not a2
    assert len(created) == 2
    assert a1.special_instructions == _SUBAGENT_SPECIAL_INSTRUCTIONS


@pytest.mark.asyncio
async def test_forward_sync_subagent_events_includes_subagent_key(monkeypatch):
    from cuga.backend.agent_spawn import runtime
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    rt = SpawnAgentRuntime([])

    events: list = []
    token = runtime.set_event_callback(lambda name, data: events.append((name, data)))

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
        runtime.reset_event_callback(token)


def test_thread_spawn_futures_are_isolated_per_thread():
    from cuga.backend.agent_spawn.runtime import clear_runtime_caches, thread_spawn_futures

    clear_runtime_caches()
    try:
        a = thread_spawn_futures("thread-a")
        b = thread_spawn_futures("thread-b")
        a["f1"] = {"status": "running"}
        assert "f1" not in b
        assert thread_spawn_futures("thread-a") is a
    finally:
        clear_runtime_caches()


@pytest.mark.asyncio
async def test_execute_emits_error_event_on_failure(monkeypatch):
    from cuga.backend.agent_spawn import runtime
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    rt = SpawnAgentRuntime([])
    events: list = []
    token = runtime.set_event_callback(lambda name, data: events.append((name, data)))

    monkeypatch.setattr(rt, "_build_agent", lambda tools: object())
    monkeypatch.setattr(rt, "_build_invoke_config", lambda workspace_thread_id="": {})

    async def _boom(*_a, **_k):
        raise RuntimeError("subagent crashed")

    monkeypatch.setattr(rt, "_run_stream", _boom)
    monkeypatch.setattr("cuga.backend.agent_spawn.runtime.set_session_attribute", lambda sid: None)

    try:
        result = await rt.execute("task")
        assert "[SpawnError]" in result
        results = [e for e in events if e[0] == "SpawnAgentResult"]
        assert len(results) == 1
        assert results[0][1]["status"] == "error"
    finally:
        runtime.reset_event_callback(token)


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

    rt._build_invoke_config = lambda workspace_thread_id="": {}
    rt._build_agent = lambda tools: object()

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

    async def _fake_execute(task, spawn_id="", share_workspace=False):
        return "done"

    monkeypatch.setattr(rt, "execute", _fake_execute)

    await rt.execute_async("async task")

    set_idx = next(i for i, s in enumerate(call_order) if s.startswith("set_session"))
    task_idx = call_order.index("create_task")
    assert set_idx < task_idx, f"set_session_attribute not before create_task: {call_order}"
