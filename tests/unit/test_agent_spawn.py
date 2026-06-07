"""Unit tests for the agent_spawn package (Phases 1–10).

Organized by implementation phase. Shared helpers are at the top.
Module-level functions _async_fn / _sync_fn are import targets for
tool_definitions tests (module path = tests.unit.test_agent_spawn).
"""

from pathlib import Path

import pytest


# ── Shared helpers ──────────────────────────────────────────────────────────


def _make_entry(**kwargs):
    from cuga.backend.agent_spawn.registry import AgentDescriptorEntry

    defaults = dict(name="agent", description="d", source="/tmp")
    defaults.update(kwargs)
    return AgentDescriptorEntry(**defaults)


def _make_registry(*names):
    from cuga.backend.agent_spawn.registry import AgentDescriptorEntry, AgentDescriptorRegistry

    entries = [AgentDescriptorEntry(name=n, description=f"desc-{n}", source="/") for n in names]
    return AgentDescriptorRegistry(entries)


def _write_agent(root: Path, name: str, description: str, extra: str = "") -> None:
    agent_dir = root / name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "AGENT.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\nAgent body.\n",
        encoding="utf-8",
    )


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


def _run_agent_spawn_block(adapter, agents_path: str, enabled: bool, config=None):
    """Run the agent_spawn block from prepare_node in isolation.

    Mirrors the exact code in prepare_node.py between the
    '── agent_spawn ──' and '── end agent_spawn ──' markers,
    plus the follow-up loop that registers tools into adapter._tools_context.
    """
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import (
        make_tool_awaitable,
    )

    agent_spawn_tools = []
    agents_prompt_section = ""
    agents_enabled = False

    if enabled:
        from cuga.backend.agent_spawn import (
            AgentDescriptorRegistry,
            create_spawn_tools,
            discover_agents,
            format_available_agents_block,
        )

        _agent_entries = discover_agents(agents_path)
        if _agent_entries:
            _agent_registry = AgentDescriptorRegistry(_agent_entries)
            agent_spawn_tools = create_spawn_tools(
                registry=_agent_registry,
                parent_tools_context=adapter._tools_context,
                spawn_futures=adapter._spawn_futures,
                parent_config=config,
            )
            agents_prompt_section = format_available_agents_block(_agent_registry)
            agents_enabled = True

    for tool in agent_spawn_tools:
        tool_func = (
            tool.coroutine if (hasattr(tool, "coroutine") and tool.coroutine) else tool.func
        )
        if tool_func:
            adapter._tools_context[tool.name] = make_tool_awaitable(tool_func)

    return agent_spawn_tools, agents_enabled, agents_prompt_section


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


# ── Import targets for tool_definitions tests ──────────────────────────────

async def _async_fn(x: int) -> str:
    return str(x)


def _sync_fn(x: int) -> str:
    return str(x)


# ── Phase 1: Configuration & Feature Toggle ────────────────────────────────


def test_agent_spawn_disabled_by_default():
    from cuga.config import settings

    assert settings.agent_spawn.enabled is False


def test_agent_spawn_defaults():
    from cuga.config import settings

    assert settings.agent_spawn.agents_dir == ".agents/agents"
    assert settings.agent_spawn.max_spawn_depth == 2
    assert settings.agent_spawn.forward_sync_subagent_events is True
    assert settings.agent_spawn.inherit_parent_tools is False


def test_agent_spawn_package_importable():
    import cuga.backend.agent_spawn  # must not raise


# ── Phase 2: AGENT.md Loader & Registry ───────────────────────────────────


def test_discover_agents_empty_dir(tmp_path):
    from cuga.backend.agent_spawn.loader import discover_agents

    assert discover_agents(tmp_path / "nonexistent") == []


def test_discover_agents_minimal_descriptor(tmp_path):
    from cuga.backend.agent_spawn.loader import discover_agents

    _write_agent(tmp_path, "foo", "bar")
    entries = discover_agents(tmp_path)
    assert len(entries) == 1
    assert entries[0].name == "foo"
    assert entries[0].description == "bar"


def test_discover_agents_rejects_path_traversal(tmp_path):
    from cuga.backend.agent_spawn.loader import discover_agents

    agent_dir = tmp_path / "bad"
    agent_dir.mkdir()
    (agent_dir / "AGENT.md").write_text(
        "---\nname: ../../etc/passwd\ndescription: bad\n---\n",
        encoding="utf-8",
    )
    assert discover_agents(tmp_path) == []


def test_discover_agents_sanitizes_jinja_in_description(tmp_path):
    from cuga.backend.agent_spawn.loader import discover_agents

    _write_agent(tmp_path, "safe", "foo {{ x }}")
    entries = discover_agents(tmp_path)
    assert entries[0].description == "foo "


def test_tool_definition_missing_function_raises(tmp_path):
    from cuga.backend.agent_spawn.loader import discover_agents

    agent_dir = tmp_path / "broken"
    agent_dir.mkdir()
    (agent_dir / "AGENT.md").write_text(
        "---\nname: broken\ndescription: desc\ntool_definitions:\n"
        "  - name: t\n    module: some.module\n---\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="function"):
        discover_agents(tmp_path)


def test_discover_agents_last_wins_on_name_collision(tmp_path):
    from cuga.backend.agent_spawn.loader import discover_agents

    dir_a = tmp_path / "a_agent"
    dir_b = tmp_path / "b_agent"
    for d in (dir_a, dir_b):
        d.mkdir()
    (dir_a / "AGENT.md").write_text(
        "---\nname: dup\ndescription: first\n---\n", encoding="utf-8"
    )
    (dir_b / "AGENT.md").write_text(
        "---\nname: dup\ndescription: second\n---\n", encoding="utf-8"
    )
    entries = discover_agents(tmp_path)
    assert len(entries) == 1
    assert entries[0].description == "second"


def test_discover_agents_full_frontmatter(tmp_path):
    from cuga.backend.agent_spawn.loader import discover_agents

    agent_dir = tmp_path / "full"
    agent_dir.mkdir()
    (agent_dir / "AGENT.md").write_text(
        "---\n"
        "name: full\n"
        "description: full agent\n"
        "tools:\n  - my_tool\n"
        "skill_tools:\n  - my_skill\n"
        "model: gpt-4o\n"
        "thread_id_prefix: full\n"
        "max_steps: 5\n"
        "inherit_parent_tools: true\n"
        "---\nBody.\n",
        encoding="utf-8",
    )
    entries = discover_agents(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e.tools == ("my_tool",)
    assert e.skill_tools == ("my_skill",)
    assert e.model == "gpt-4o"
    assert e.thread_id_prefix == "full"
    assert e.max_steps == 5
    assert e.inherit_parent_tools is True


# ── Phase 3a: AGENT.md tool_definitions validation ────────────────────────


def test_invalid_module_in_tool_definitions_raises_at_parse_time(tmp_path):
    from cuga.backend.agent_spawn.loader import discover_agents

    agent_dir = tmp_path / "broken_module"
    agent_dir.mkdir()
    (agent_dir / "AGENT.md").write_text(
        "---\nname: broken_module\ndescription: desc\ntool_definitions:\n"
        "  - name: t\n    function: some_fn\n---\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="module"):
        discover_agents(tmp_path)


def test_invalid_name_in_tool_definitions_raises_at_parse_time(tmp_path):
    from cuga.backend.agent_spawn.loader import discover_agents

    agent_dir = tmp_path / "broken_name"
    agent_dir.mkdir()
    (agent_dir / "AGENT.md").write_text(
        "---\nname: broken_name\ndescription: desc\ntool_definitions:\n"
        "  - module: some.module\n    function: some_fn\n---\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="name"):
        discover_agents(tmp_path)


def test_valid_tool_definitions_do_not_raise(tmp_path):
    from cuga.backend.agent_spawn.loader import discover_agents
    from cuga.backend.agent_spawn.registry import ToolDefinition

    agent_dir = tmp_path / "valid_agent"
    agent_dir.mkdir()
    (agent_dir / "AGENT.md").write_text(
        "---\nname: valid_agent\ndescription: desc\ntool_definitions:\n"
        "  - name: my_tool\n    description: does stuff\n"
        "    module: some.module\n    function: fn\n---\n",
        encoding="utf-8",
    )
    entries = discover_agents(tmp_path)
    assert len(entries) == 1
    assert len(entries[0].tool_definitions) == 1
    assert isinstance(entries[0].tool_definitions[0], ToolDefinition)
    assert entries[0].tool_definitions[0].name == "my_tool"


# ── Phase 3b: Tool Builder ─────────────────────────────────────────────────


def test_build_tool_from_definition_async_function():
    from cuga.backend.agent_spawn.registry import ToolDefinition
    from cuga.backend.agent_spawn.tool_builder import build_tool_from_definition

    defn = ToolDefinition(
        name="async_tool",
        description="desc",
        module="tests.unit.test_agent_spawn",
        function="_async_fn",
    )
    tool = build_tool_from_definition(defn)
    assert tool.coroutine is not None
    assert tool.name == "async_tool"


def test_build_tool_from_definition_sync_function():
    from cuga.backend.agent_spawn.registry import ToolDefinition
    from cuga.backend.agent_spawn.tool_builder import build_tool_from_definition

    defn = ToolDefinition(
        name="sync_tool",
        description="desc",
        module="tests.unit.test_agent_spawn",
        function="_sync_fn",
    )
    tool = build_tool_from_definition(defn)
    assert tool.func is not None
    assert tool.name == "sync_tool"


def test_build_tool_invalid_module_raises_tool_definition_error():
    from cuga.backend.agent_spawn.registry import ToolDefinition
    from cuga.backend.agent_spawn.tool_builder import ToolDefinitionError, build_tool_from_definition

    defn = ToolDefinition(
        name="bad",
        description="d",
        module="nonexistent.module.path",
        function="fn",
    )
    with pytest.raises(ToolDefinitionError, match="Cannot import module"):
        build_tool_from_definition(defn)


def test_build_tool_missing_function_raises():
    from cuga.backend.agent_spawn.registry import ToolDefinition
    from cuga.backend.agent_spawn.tool_builder import ToolDefinitionError, build_tool_from_definition

    defn = ToolDefinition(
        name="bad",
        description="d",
        module="tests.unit.test_agent_spawn",
        function="_no_such_function",
    )
    with pytest.raises(ToolDefinitionError, match="no attribute"):
        build_tool_from_definition(defn)


def test_build_tool_missing_args_schema_raises():
    from cuga.backend.agent_spawn.registry import ToolDefinition
    from cuga.backend.agent_spawn.tool_builder import ToolDefinitionError, build_tool_from_definition

    defn = ToolDefinition(
        name="bad",
        description="d",
        module="tests.unit.test_agent_spawn",
        function="_sync_fn",
        args_schema="NoSuchClass",
    )
    with pytest.raises(ToolDefinitionError, match="args_schema"):
        build_tool_from_definition(defn)


def test_skill_entry_tools_block_parsed(tmp_path):
    from cuga.backend.skills.loader import _parse_skill_file

    skill_dir = tmp_path / "myskill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: myskill\n"
        "description: My skill\n"
        "tools:\n"
        "  - name: my_tool\n"
        "    description: does stuff\n"
        "    module: tests.unit.test_agent_spawn\n"
        "    function: _sync_fn\n"
        "---\nBody.\n",
        encoding="utf-8",
    )
    entry = _parse_skill_file(skill_dir / "SKILL.md")
    assert entry is not None
    assert len(entry.tool_definitions) == 1
    assert entry.tool_definitions[0]["name"] == "my_tool"


def test_build_tools_from_skill_empty_returns_empty():
    from cuga.backend.agent_spawn.tool_builder import build_tools_from_skill_tool_definitions
    from cuga.backend.skills.registry import SkillEntry

    entry = SkillEntry(name="s", description="d", body="b", source="/tmp/SKILL.md")
    assert build_tools_from_skill_tool_definitions(entry) == []


# ── Phase 4: SpawnAgentRuntime ─────────────────────────────────────────────


def test_make_thread_id_format():
    import re

    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    entry = _make_entry(name="myagent", thread_id_prefix="myagent")
    rt = SpawnAgentRuntime(entry, {})
    tid = rt._make_thread_id()
    assert re.match(r"^myagent_[0-9a-f]{8}$", tid), f"Unexpected thread_id: {tid}"


def test_assemble_tools_built_wins_over_parent():
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime
    from langchain_core.tools import StructuredTool

    async def parent_foo():
        return "parent"

    async def built_foo():
        return "built"

    entry = _make_entry(inherit_parent_tools=True, tools=("foo",))
    rt = SpawnAgentRuntime(entry, {"foo": parent_foo})

    built_tool = StructuredTool.from_function(coroutine=built_foo, name="foo", description="built")
    rt._build_definition_tools = lambda: [built_tool]

    tools = rt._assemble_tools()
    by_name = {t.name: t for t in tools}
    assert by_name["foo"].coroutine is built_foo


def test_assemble_tools_no_inherit_ignores_parent():
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    async def parent_tool():
        return "parent"

    entry = _make_entry(inherit_parent_tools=False, tools=("parent_tool",))
    rt = SpawnAgentRuntime(entry, {"parent_tool": parent_tool})
    rt._build_definition_tools = lambda: []
    rt._build_skill_tools = lambda: []

    tools = rt._assemble_tools()
    assert not any(t.name == "parent_tool" for t in tools)


@pytest.mark.asyncio
async def test_execute_respects_max_spawn_depth():
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime, _spawn_depth

    token = _spawn_depth.set(99)
    try:
        entry = _make_entry()
        rt = SpawnAgentRuntime(entry, {})
        result = await rt.execute("some task")
        assert result.startswith("[SpawnError]")
    finally:
        _spawn_depth.reset(token)


@pytest.mark.asyncio
async def test_execute_async_returns_future_id(monkeypatch):
    import re

    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    entry = _make_entry()
    rt = SpawnAgentRuntime(entry, {})

    async def _fake_execute(task: str) -> str:
        return "result"

    monkeypatch.setattr(rt, "execute", _fake_execute)

    future_id = await rt.execute_async("do something")
    assert future_id.startswith("future_")
    assert re.match(r"^future_[0-9a-f]{8}$", future_id)


# ── Phase 5: spawn_agent / get_agent_result tools + prompt_utils ───────────


def test_create_spawn_tools_returns_two_tools():
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    tools = create_spawn_tools(_make_registry(), {}, {})
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert "spawn_agent" in names and "get_agent_result" in names


@pytest.mark.asyncio
async def test_spawn_agent_unknown_name_returns_error_string():
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    futures: dict = {}
    tools = create_spawn_tools(_make_registry(), {}, futures)
    spawn = next(t for t in tools if t.name == "spawn_agent")
    result = await spawn.coroutine(name="nobody", task="hi")
    assert "Unknown agent" in result


@pytest.mark.asyncio
async def test_spawn_agent_async_returns_future_id(monkeypatch):
    import re

    from cuga.backend.agent_spawn import runtime
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    futures: dict = {}
    reg = _make_registry("mybot")
    tools = create_spawn_tools(reg, {}, futures)
    spawn = next(t for t in tools if t.name == "spawn_agent")

    async def _fake_execute_async(self, task):
        return "future_aabbccdd"

    monkeypatch.setattr(runtime.SpawnAgentRuntime, "execute_async", _fake_execute_async)

    result = await spawn.coroutine(name="mybot", task="do x", mode="async")
    assert re.match(r"^future_[0-9a-f]+$", result)


@pytest.mark.asyncio
async def test_get_agent_result_unknown_future_id():
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    futures: dict = {}
    tools = create_spawn_tools(_make_registry(), {}, futures)
    get_result = next(t for t in tools if t.name == "get_agent_result")
    result = await get_result.coroutine(future_id="nonexistent")
    assert "Unknown future_id" in result


@pytest.mark.asyncio
async def test_get_agent_result_done_returns_immediately():
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    futures = {"fid_done": {"status": "done", "result": "hello"}}
    tools = create_spawn_tools(_make_registry(), {}, futures)
    get_result = next(t for t in tools if t.name == "get_agent_result")
    result = await get_result.coroutine(future_id="fid_done", timeout=5.0)
    assert result == "hello"


@pytest.mark.asyncio
async def test_get_agent_result_timeout():
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    futures = {"fid_run": {"status": "running", "result": None}}
    tools = create_spawn_tools(_make_registry(), {}, futures)
    get_result = next(t for t in tools if t.name == "get_agent_result")
    result = await get_result.coroutine(future_id="fid_run", timeout=0.1)
    assert "[SpawnTimeout]" in result


def test_format_available_agents_block_structure():
    from cuga.backend.agent_spawn.prompt_utils import format_available_agents_block
    from cuga.backend.agent_spawn.registry import AgentDescriptorEntry, AgentDescriptorRegistry

    reg = AgentDescriptorRegistry([AgentDescriptorEntry(name="a", description="d", source="/")])
    block = format_available_agents_block(reg)
    assert block.startswith("<available_agents>")
    assert "**a**: d" in block
    assert "spawn_agent" in block


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


# ── Phase 7: Prepare Node Integration ─────────────────────────────────────


def test_prepare_node_disabled_no_spawn_tools(tmp_path):
    adapter = _make_adapter()
    tools, agents_enabled, section = _run_agent_spawn_block(adapter, str(tmp_path), enabled=False)

    assert tools == []
    assert agents_enabled is False
    assert section == ""
    assert "spawn_agent" not in adapter._tools_context
    assert "get_agent_result" not in adapter._tools_context


def test_prepare_node_enabled_with_agents_injects_tools(tmp_path):
    _write_agent(tmp_path, "my_agent", "My test agent")

    adapter = _make_adapter()
    tools, agents_enabled, section = _run_agent_spawn_block(adapter, str(tmp_path), enabled=True)

    assert len(tools) == 2
    assert agents_enabled is True
    assert "<available_agents>" in section
    assert "**my_agent**: My test agent" in section
    assert "spawn_agent" in adapter._tools_context
    assert "get_agent_result" in adapter._tools_context


def test_prepare_node_enabled_no_agents_dir_no_tools(tmp_path):
    nonexistent = str(tmp_path / "does_not_exist")

    adapter = _make_adapter()
    tools, agents_enabled, section = _run_agent_spawn_block(adapter, nonexistent, enabled=True)

    assert tools == []
    assert agents_enabled is False
    assert section == ""
    assert "spawn_agent" not in adapter._tools_context
    assert "get_agent_result" not in adapter._tools_context


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

    entry = _make_entry(name="tester", thread_id_prefix="tester")
    rt = SpawnAgentRuntime(entry, {})

    events: list = []
    runtime.set_event_callback(lambda name, data: events.append((name, data)))

    rt._assemble_tools = lambda: []

    async def _fake_run_stream(agent, task, thread_id, cfg):
        return "mocked-answer"

    monkeypatch.setattr(rt, "_build_agent", lambda tools: object())
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

    entry = _make_entry(name="forwarder")
    rt = SpawnAgentRuntime(entry, {})

    events: list = []
    runtime.set_event_callback(lambda name, data: events.append((name, data)))

    class FakeAgent:
        async def stream(self, task, thread_id, config):
            # CugaAgent.stream() with subgraphs=True yields (namespace, {node: state}) tuples
            yield ((), {"FinalAnswerAgent": {"script": "print('hi')", "final_answer": "done"}})

    try:
        result = await rt._run_stream(FakeAgent(), "task", "t_id", {})
        assert result == "done"
        code_agent_events = [e for e in events if e[0] == "CodeAgent"]
        assert len(code_agent_events) == 1
        assert code_agent_events[0][1]["subagent"] == "forwarder"
    finally:
        runtime.set_event_callback(None)


# ── Phase 10: Observability (Langfuse + OTEL) ──────────────────────────────


def test_build_invoke_config_syncs_langfuse_callbacks(monkeypatch):
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    sync_calls = []
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.utils.langfuse_tracing.sync_langfuse_callbacks_from_config",
        lambda cfg: sync_calls.append(cfg),
    )
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.utils.langfuse_tracing.get_langfuse_invoke_config",
        lambda: {"callbacks": []},
    )

    parent_cfg = {"configurable": {"thread_id": "parent-thread"}}
    entry = _make_entry(name="obs")
    rt = SpawnAgentRuntime(entry, {}, parent_config=parent_cfg)
    rt._build_invoke_config()

    assert len(sync_calls) == 1
    assert sync_calls[0] is parent_cfg


@pytest.mark.asyncio
async def test_execute_calls_set_session_attribute(monkeypatch):
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

    session_calls = []
    monkeypatch.setattr(
        "cuga.backend.observability.openlit_init.set_session_attribute",
        lambda sid: session_calls.append(sid),
    )

    parent_cfg = {"configurable": {"thread_id": "parent-thread"}}
    entry = _make_entry(name="obs")
    rt = SpawnAgentRuntime(entry, {}, parent_config=parent_cfg)

    rt._assemble_tools = lambda: []
    rt._build_invoke_config = lambda: {}
    rt._build_agent = lambda tools: object()

    async def _fake_run_stream(agent, task, thread_id, cfg):
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
        "cuga.backend.observability.openlit_init.set_session_attribute",
        lambda sid: call_order.append(f"set_session:{sid}"),
    )

    original_create_task = asyncio.create_task

    def _tracked_create_task(coro, **kw):
        call_order.append("create_task")
        return original_create_task(coro, **kw)

    monkeypatch.setattr(asyncio, "create_task", _tracked_create_task)

    parent_cfg = {"configurable": {"thread_id": "async-thread"}}
    entry = _make_entry(name="obs")
    rt = SpawnAgentRuntime(entry, {}, parent_config=parent_cfg)
    rt._assemble_tools = lambda: []

    async def _fake_execute(task):
        return "done"

    monkeypatch.setattr(rt, "execute", _fake_execute)

    await rt.execute_async("async task")

    set_idx = next(i for i, s in enumerate(call_order) if s.startswith("set_session"))
    task_idx = call_order.index("create_task")
    assert set_idx < task_idx, f"set_session_attribute not before create_task: {call_order}"
