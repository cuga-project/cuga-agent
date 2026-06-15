# Agent Spawning for CugaLite — Technical Implementation Plan

## Quick Reference

| Package root | `src/cuga/backend/agent_spawn/` |
|---|---|
| Jinja template | `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompts/mcp_prompt.jinja2` |
| Config file | `src/cuga/settings.toml` |
| Validators | `src/cuga/config.py` |
| Skills loader | `src/cuga/backend/skills/loader.py` |
| Prepare node | `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/prepare_node.py` |
| Prompt utils | `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py` |
| Graph adapter | `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/graph_adapter.py` |
| CugaLite graph | `src/cuga/backend/cuga_graph/nodes/cuga_lite/cuga_lite_graph.py` |
| Agent loop | `src/cuga/backend/cuga_graph/utils/agent_loop.py` |
| **Unit tests (all phases)** | **`tests/unit/test_agent_spawn.py`** |
| Integration tests | `tests/integration/test_agent_spawn_integration.py` |
| Test fixtures | `tests/fixtures/agents/data_analyst/` (AGENT.md + tools.py) |

> **Source of truth:** All requirements, FRs, NFRs, and design decisions in this plan are grounded in `docs/agent-spawning-proposal.md`. Section references (e.g. "FR-1", "NFR-3", "Q2") map directly to that document.

---

## Phase 1 — Configuration & Feature Toggle

### Context

Covers FR-1, NFR-1, NFR-2. Adds the `[agent_spawn]` TOML section and its Dynaconf validators. This is the foundation all later phases depend on.

### Implementation Steps

- [x] **1.1 Add `[agent_spawn]` section to `src/cuga/settings.toml`**
  → `src/cuga/settings.toml`, `[agent_spawn]` section

- [x] **1.2 Register 5 Dynaconf `Validator` entries in `src/cuga/config.py`**
  → `src/cuga/config.py`, validators list

- [x] **1.3 Create the package skeleton at `src/cuga/backend/agent_spawn/`**
  → `src/cuga/backend/agent_spawn/__init__.py`

### Completion Checklist

- [ ] `from cuga.config import settings; settings.agent_spawn.enabled` returns `False` without error.
- [ ] `settings.agent_spawn.agents_dir` returns `".agents/agents"`.
- [ ] `settings.agent_spawn.max_spawn_depth` returns `2`.
- [ ] `settings.agent_spawn.forward_sync_subagent_events` returns `True`.
- [ ] `settings.agent_spawn.inherit_parent_tools` returns `False`.
- [ ] `from cuga.backend.agent_spawn import __all__` does not raise `ImportError`.

### Tests to Write

File: `tests/unit/test_agent_spawn.py` — **Phase 1 section**

```python
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
```

---

## Phase 2 — AGENT.md Loader & Registry

### Context

Covers FR-5, FR-8, NFR-6. Parallels the existing `skills/loader.py` + `skills/registry.py` pair exactly. The loader walks `.agents/agents/`, parses YAML frontmatter, and produces `AgentDescriptorEntry` objects stored in `AgentDescriptorRegistry`. Also references Q6 (fail-fast on `tool_definitions` errors) and Q1 (tool resolution strategy).

### Implementation Steps

- [x] **2.1 Create `src/cuga/backend/agent_spawn/registry.py`**
  → `src/cuga/backend/agent_spawn/registry.py`: `ToolDefinition`, `AgentDescriptorEntry`, `AgentDescriptorRegistry`

- [x] **2.2 Create `src/cuga/backend/agent_spawn/loader.py`**
  → `src/cuga/backend/agent_spawn/loader.py`: `discover_agents()`, `_parse_agent_file()`, `_parse_tool_definitions()`

- [x] **2.3 Update `src/cuga/backend/agent_spawn/__init__.py`**
  → `src/cuga/backend/agent_spawn/__init__.py`, exports `AgentDescriptorEntry`, `AgentDescriptorRegistry`, `discover_agents`

### Completion Checklist

- [ ] `discover_agents("/nonexistent")` returns `[]` without raising.
- [ ] A minimal AGENT.md with `name: foo` and `description: bar` produces an `AgentDescriptorEntry(name="foo", description="bar")`.
- [ ] An AGENT.md with `name: ../etc/passwd` is silently skipped.
- [ ] An AGENT.md with `description: {{ inject }}` has the Jinja delimiters stripped in the resulting entry.
- [ ] A `tool_definitions` entry missing the `function` key raises a descriptive `ValueError` at parse time (not at spawn time — Q6).
- [ ] Two AGENT.md files with the same name: the one discovered last wins.

### Tests to Write

File: `tests/unit/test_agent_spawn.py` — **Phase 2 section**

```python
def test_discover_agents_empty_dir(tmp_path):
    from cuga.backend.agent_spawn.loader import discover_agents
    assert discover_agents(tmp_path / "nonexistent") == []

def test_discover_agents_minimal_descriptor(tmp_path):
    # writes AGENT.md, asserts name/description parsed

def test_discover_agents_rejects_path_traversal(tmp_path):
    # name: ../../etc/passwd → skipped

def test_discover_agents_sanitizes_jinja_in_description(tmp_path):
    # description: "foo {{ x }}" → "foo "

def test_tool_definition_missing_function_raises(tmp_path):
    # tool_definitions with no function → ValueError at parse time

def test_discover_agents_last_wins_on_name_collision(tmp_path):
    # two AGENT.md with same name → second one returned

def test_discover_agents_full_frontmatter(tmp_path):
    # all fields: tools, skill_tools, model, max_steps, inherit_parent_tools
    # asserts AgentDescriptorEntry fields are correctly populated
```

---

## Phase 3 — Tool Builder (`tool_builder.py` + SKILL.md `tools:` block)

### Context

Covers FR-10, FR-11, FR-2, NFR-7, Q5, Q6. Two sub-tasks: (a) build `StructuredTool` from `tool_definitions`; (b) parse `tools:` block from an existing SKILL.md and produce `StructuredTool` instances.

### Implementation Steps

- [x] **3.1 Extend `src/cuga/backend/skills/loader.py` — add `tools:` block parsing**
  → `src/cuga/backend/skills/loader.py`: `_parse_skill_file()` (extracts `tools:` frontmatter key)
  → `src/cuga/backend/skills/registry.py`: `SkillEntry.tool_definitions` field

- [x] **3.2 Create `src/cuga/backend/agent_spawn/tool_builder.py`**
  → `src/cuga/backend/agent_spawn/tool_builder.py`: `ToolDefinitionError`, `build_tool_from_definition()`, `build_tools_from_skill_tool_definitions()`

- [x] **3.3 Update `src/cuga/backend/agent_spawn/__init__.py`**
  → `src/cuga/backend/agent_spawn/__init__.py`, exports `ToolDefinitionError`, `build_tool_from_definition`, `build_tools_from_skill_tool_definitions`

### Completion Checklist

- [ ] `build_tool_from_definition` with a valid async function produces a `StructuredTool` with `coroutine` set.
- [ ] `build_tool_from_definition` with a valid sync function produces a `StructuredTool` with `func` set.
- [ ] `build_tool_from_definition` with an invalid module path raises `ToolDefinitionError` immediately.
- [ ] `build_tool_from_definition` with a missing function attribute raises `ToolDefinitionError`.
- [ ] `build_tool_from_definition` with an invalid `args_schema` name raises `ToolDefinitionError`.
- [ ] `build_tools_from_skill_tool_definitions` on a `SkillEntry` with `tool_definitions=()` returns `[]`.
- [ ] A SKILL.md with a `tools:` block produces a `SkillEntry` with non-empty `tool_definitions`.

### Tests to Write

File: `tests/unit/test_agent_spawn.py` — **Phase 3b section** (tool builder)

> Note: `_async_fn` and `_sync_fn` are module-level helpers in the merged file;
> `ToolDefinition(module="tests.unit.test_agent_spawn", ...)` is used as the import target.

```python
def test_build_tool_from_definition_async_function():
    # Creates ToolDefinition pointing at a test async function
    # Asserts returned StructuredTool.coroutine is not None

def test_build_tool_from_definition_sync_function():
    # Asserts returned StructuredTool.func is not None

def test_build_tool_invalid_module_raises_tool_definition_error():
    from cuga.backend.agent_spawn.tool_builder import ToolDefinitionError
    # import of nonexistent.module.path → ToolDefinitionError

def test_build_tool_missing_function_raises():
    # module exists but function missing → ToolDefinitionError

def test_build_tool_missing_args_schema_raises():
    # args_schema="NoSuchClass" in valid module → ToolDefinitionError

def test_skill_entry_tools_block_parsed(tmp_path):
    # Writes SKILL.md with tools: block
    # Asserts SkillEntry.tool_definitions has length > 0

def test_build_tools_from_skill_empty_returns_empty():
    # SkillEntry(tool_definitions=()) → []
```

File: `tests/unit/test_agent_spawn.py` — **Phase 3a section** (tool_definitions validation) ✅ implemented
```python
def test_invalid_module_in_tool_definitions_raises_at_parse_time(tmp_path):
    # AGENT.md with tool_definitions missing `module` key → ValueError at parse time (Q6)

def test_invalid_name_in_tool_definitions_raises_at_parse_time(tmp_path):
    # AGENT.md with tool_definitions missing `name` key → ValueError at parse time (Q6)

def test_valid_tool_definitions_do_not_raise(tmp_path):
    # Well-formed tool_definitions entry parsed without error
```

---

## Phase 4 — `SpawnAgentRuntime` (`runtime.py`)

### Context

Covers FR-3, FR-4, FR-6, FR-7, NFR-3, NFR-4, Q1, Q3. This is the core spawning engine. It takes an `AgentDescriptorEntry`, resolves tools, builds a `CugaAgent` (from `cuga.sdk.CugaAgent`), and either executes it synchronously (awaits `stream()`) or fires it as an `asyncio.Task`. It is called from `tools.py` (Phase 5), never directly from the graph.

### Implementation Steps

- [x] **4.1 Create `src/cuga/backend/agent_spawn/runtime.py`**
  → `src/cuga/backend/agent_spawn/runtime.py`: `SpawnAgentRuntime` class with `execute()`, `execute_async()`, `_assemble_tools()`, `_make_thread_id()`, `_resolve_parent_tools()`, `_build_skill_tools()`, `_build_definition_tools()`, `_build_agent()`, `_build_invoke_config()`, `_run_stream()`, `_execute_and_store()`; module-level `_spawn_depth` ContextVar, `_event_callback`, `set_event_callback()`, `_emit()`

- [x] **4.2 Update `src/cuga/backend/agent_spawn/__init__.py`**
  → `src/cuga/backend/agent_spawn/__init__.py`, exports `SpawnAgentRuntime`

### Completion Checklist

- [ ] `_make_thread_id()` returns a string matching `r"^[a-z_]+_[0-9a-f]{8}$"`.
- [ ] `_assemble_tools()` with `inherit_parent_tools=False` returns only built tools (not parent tools).
- [ ] `_assemble_tools()` with `inherit_parent_tools=True` and a name collision: built tool takes precedence (Q5).
- [ ] `_spawn_depth` ContextVar increments by 1 around `execute()` and is reset correctly on error.
- [ ] When `depth >= max_spawn_depth`, `execute()` returns a string starting with `"[SpawnError]"` without raising.
- [ ] `execute_async()` returns a string starting with `"future_"`.

### Tests to Write

File: `tests/unit/test_agent_spawn.py` — **Phase 4 section**

```python
def test_make_thread_id_format():
    import re
    from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime
    from cuga.backend.agent_spawn.registry import AgentDescriptorEntry
    entry = AgentDescriptorEntry(name="x", description="d", source="/tmp")
    rt = SpawnAgentRuntime(entry, {})
    tid = rt._make_thread_id()
    assert re.match(r"^x_[0-9a-f]{8}$", tid)

def test_assemble_tools_built_wins_over_parent():
    # parent has tool "foo", defn also has "foo"
    # _assemble_tools with inherit_parent_tools=True → defn version present

def test_assemble_tools_no_inherit_ignores_parent():
    # inherit_parent_tools=False → parent tools not present

@pytest.mark.asyncio
async def test_execute_respects_max_spawn_depth():
    from cuga.backend.agent_spawn.runtime import _spawn_depth
    _spawn_depth.set(99)
    # execute() → returns "[SpawnError]..." string, not raise

@pytest.mark.asyncio
async def test_execute_async_returns_future_id():
    # mocked execute_async returns a "future_" string
```

---

## Phase 5 — `spawn_agent` and `get_agent_result` StructuredTools (`tools.py`)

### Context

Covers FR-1, FR-6, FR-9, NFR-1. The two tools the LLM calls. `spawn_agent` dispatches sync or async. `get_agent_result` polls/awaits the future dict. Both are created by `create_spawn_tools()` which is a factory taking the registry + parent context (following the `create_skill_tools` factory pattern from `skills/tools.py`).

### Implementation Steps

- [x] **5.1 Create `src/cuga/backend/agent_spawn/tools.py`**
  → `src/cuga/backend/agent_spawn/tools.py`: `create_spawn_tools()`, `SpawnAgentInput`, `GetAgentResultInput`

- [x] **5.2 Create `src/cuga/backend/agent_spawn/prompt_utils.py`**
  → `src/cuga/backend/agent_spawn/prompt_utils.py`: `format_available_agents_block()`

- [x] **5.3 Update `src/cuga/backend/agent_spawn/__init__.py`**
  → `src/cuga/backend/agent_spawn/__init__.py`, exports `create_spawn_tools`, `format_available_agents_block`

### Completion Checklist

- [ ] `spawn_agent("unknown_name", "task")` returns a string containing `"Unknown agent"`.
- [ ] `spawn_agent(valid_name, "task", mode="async")` returns a string starting with `"future_"` and writes a `"running"` entry to `spawn_futures`.
- [ ] `get_agent_result("nonexistent_id")` returns a string containing `"Unknown future_id"`.
- [ ] `get_agent_result` of a `"done"` future returns the result immediately (no sleep).
- [ ] `get_agent_result` of a `"running"` future that never finishes returns a timeout string after `timeout` seconds (FR-9).
- [ ] `format_available_agents_block` output starts with `<available_agents>` and ends with the usage instruction line.

### Tests to Write

File: `tests/unit/test_agent_spawn.py` — **Phase 5 section**

```python
def test_create_spawn_tools_returns_two_tools():
    from cuga.backend.agent_spawn.tools import create_spawn_tools
    from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry
    tools = create_spawn_tools(AgentDescriptorRegistry([]), {}, {})
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert "spawn_agent" in names and "get_agent_result" in names

@pytest.mark.asyncio
async def test_spawn_agent_unknown_name_returns_error_string():
    # spawn_agent("x", "task") → contains "Unknown agent"

@pytest.mark.asyncio
async def test_spawn_agent_async_returns_future_id():
    # mode="async", mocked runtime.execute_async → future_id format

@pytest.mark.asyncio
async def test_get_agent_result_unknown_future_id():
    # spawn_futures={}, get_agent_result("x") → "Unknown future_id"

@pytest.mark.asyncio
async def test_get_agent_result_done_returns_immediately():
    # spawn_futures={"fid": {"status": "done", "result": "hello"}}
    # get_agent_result("fid") → "hello"

@pytest.mark.asyncio
async def test_get_agent_result_timeout():
    # spawn_futures={"fid": {"status": "running"}}
    # get_agent_result("fid", timeout=0.1) → "[SpawnTimeout]..."

def test_format_available_agents_block_structure():
    from cuga.backend.agent_spawn.prompt_utils import format_available_agents_block
    from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry, AgentDescriptorEntry
    reg = AgentDescriptorRegistry([AgentDescriptorEntry(name="a", description="d", source="/")])
    block = format_available_agents_block(reg)
    assert block.startswith("<available_agents>")
    assert "**a**: d" in block
    assert "spawn_agent" in block
```

---

## Phase 6 — Graph Integration (Closure State + `cuga_lite_graph.py`)

### Context

Covers FR-1, NFR-1, NFR-2, Q2. Adds a `_spawn_futures` dict to the `create_cuga_lite_graph` closure. No changes to graph topology. The dict is passed to `create_spawn_tools()` in Phase 7 (prepare_node). This phase is a prerequisite for Phase 7.

### Implementation Steps

- [x] **6.1 Modify `src/cuga/backend/cuga_graph/nodes/cuga_lite/cuga_lite_graph.py`**
  → `create_cuga_lite_graph()`: `spawn_futures: Dict[str, Any] = {}` closure dict, passed as `spawn_futures_ref=spawn_futures` to `AgentGraphAdapter`

- [x] **6.2 Modify `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/graph_adapter.py`**
  → `AgentGraphAdapter.__init__()`: `spawn_futures_ref` parameter, stored as `self._spawn_futures`

### Completion Checklist

- [ ] `create_cuga_lite_graph()` does not raise when called.
- [ ] `adapter._spawn_futures` is an empty dict after construction.
- [ ] `adapter._spawn_futures` is the **same object** as `spawn_futures` in the closure (identity check, not just equality).
- [ ] Adding a key to `adapter._spawn_futures` is visible from within closure tools.

### Tests to Write

File: `tests/unit/test_agent_spawn.py` — **Phase 6 section** ✅ implemented

```python
def test_create_cuga_lite_graph_no_regression():
    # With agent_spawn.enabled=False, create_cuga_lite_graph(model=mock) doesn't raise
    # (NFR-1: zero overhead when disabled)

def test_spawn_futures_closure_is_shared():
    # Creates AgentGraphAdapter with spawn_futures_ref, checks adapter._spawn_futures is same object
    # Adds key to adapter._spawn_futures, confirms mutation visible through the ref

def test_agent_graph_adapter_spawn_futures_default_is_empty_dict():
    # AgentGraphAdapter without spawn_futures_ref → _spawn_futures is an empty dict
```

---

## Phase 7 — Prepare Node Integration

### Context

Covers FR-1, FR-8, NFR-1, NFR-2. This is the ~14-line addition described in the proposal's "Files to modify" table. When `settings.agent_spawn.enabled` is `True`, the prepare node (a) discovers agents, (b) creates spawn tools, (c) adds them to `tools_for_prompt`, (d) registers them in `adapter._tools_context`, and (e) adds the `agents_prompt_section` to `create_mcp_prompt`. When disabled, the code path is entirely bypassed (NFR-1).

### Implementation Steps

- [x] **7.1 Modify `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/prepare_node.py`**
  → `prepare_tools_and_apps()`: agent_spawn block (between `── agent_spawn ──` markers); tool injection loop below the block

- [x] **7.2 Modify `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py`**
  → `create_mcp_prompt()`: `agents_enabled` and `agents_prompt_section` keyword parameters

- [x] **7.3 Update the `create_mcp_prompt` call in `prepare_node.py`**
  → `prepare_tools_and_apps()`: passes `agents_enabled=agents_enabled, agents_prompt_section=agents_prompt_section` to `create_mcp_prompt()`

### Completion Checklist

- [ ] With `agent_spawn.enabled=False`, no spawn tools are added to `tools_for_prompt`.
- [ ] With `agent_spawn.enabled=True` and a non-empty agents directory, `adapter._tools_context` contains `"spawn_agent"` and `"get_agent_result"`.
- [ ] With `agent_spawn.enabled=True` and no AGENT.md files found, no spawn tools are added (graceful empty directory handling).
- [ ] `create_mcp_prompt` accepts `agents_enabled` and `agents_prompt_section` without error.

### Tests to Write

File: `tests/unit/test_agent_spawn.py` — **Phase 7 section** ✅ implemented

Tests exercise the agent_spawn block from prepare_node in isolation via a
`_run_agent_spawn_block` helper that mirrors the exact code path (same imports,
same function calls, same injection into `adapter._tools_context`).

```python
def test_prepare_node_disabled_no_spawn_tools(tmp_path):
    # enabled=False → tools == [], "spawn_agent" not in adapter._tools_context

def test_prepare_node_enabled_with_agents_injects_tools(tmp_path):
    # Write minimal AGENT.md, enabled=True → 2 tools injected into adapter._tools_context
    # assert "spawn_agent" in adapter._tools_context
    # assert "get_agent_result" in adapter._tools_context

def test_prepare_node_enabled_no_agents_dir_no_tools(tmp_path):
    # enabled=True but agents_dir doesn't exist → no tools added, no error raised
```

---

## Phase 8 — Prompt Template Update (`mcp_prompt.jinja2`)

### Context

Covers FR-8, NFR-1. Adds the `{% if agents_enabled %}` block after the skills section in `mcp_prompt.jinja2`. When `agents_enabled` is `False` (the default), this block renders nothing — zero overhead in the prompt (NFR-1).

### Implementation Steps

- [x] **8.1 Modify `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompts/mcp_prompt.jinja2`**
  → `mcp_prompt.jinja2`: `{% if agents_enabled %}` block after the skills section; tool entries in the "What You Have Access To" section

- [x] **8.2 Verify template renders correctly**
  → covered by `tests/unit/test_agent_spawn.py` (Phase 8 section)

### Completion Checklist

- [ ] `create_mcp_prompt(..., agents_enabled=False)` output does not contain `<available_agents>`.
- [ ] `create_mcp_prompt(..., agents_enabled=True, agents_prompt_section="<available_agents>...</available_agents>")` output contains `<available_agents>` and `spawn_agent`.
- [ ] The template renders without Jinja errors in both cases.

### Tests to Write

File: `tests/unit/test_agent_spawn.py` — **Phase 8 section**

```python
def test_agents_block_absent_when_disabled():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import create_mcp_prompt
    prompt = create_mcp_prompt([], agents_enabled=False, prompt_template=_get_prompt_template())
    assert "available_agents" not in prompt
    assert "spawn_agent" not in prompt

def test_agents_block_present_when_enabled():
    prompt = create_mcp_prompt(
        [],
        agents_enabled=True,
        agents_prompt_section="<available_agents>\n- **a**: desc\n</available_agents>",
        prompt_template=_get_prompt_template(),
    )
    assert "<available_agents>" in prompt
    assert "spawn_agent" in prompt

def test_prompt_renders_without_jinja_errors_disabled():
    create_mcp_prompt([], agents_enabled=False, prompt_template=_get_prompt_template())

def test_prompt_renders_without_jinja_errors_enabled():
    create_mcp_prompt([], agents_enabled=True, agents_prompt_section="x", prompt_template=_get_prompt_template())
```

---

## Phase 9 — Stream Events (`agent_loop.py` + NFR-5)

### Context

Covers NFR-5, success criteria 5, 6. Adds `SpawnAgent` and `SpawnAgentResult` stream event names. These events are emitted by `SpawnAgentRuntime.execute()` as tool side-effects via a module-level callback hook.

### Implementation Steps

- [x] **9.1 Emit `SpawnAgent` and `SpawnAgentResult` events from `SpawnAgentRuntime`**
  → `src/cuga/backend/agent_spawn/runtime.py`: `_event_callback`, `set_event_callback()`, `_emit()`, calls in `SpawnAgentRuntime.execute()`

- [x] **9.2 Forward subagent `CodeAgent` events (sync mode)**
  → `src/cuga/backend/agent_spawn/runtime.py`: `SpawnAgentRuntime._run_stream()`, `forward_sync_subagent_events` gate

- [ ] **9.3 Modify `src/cuga/backend/cuga_graph/utils/agent_loop.py`** *(deferred)*
  → SSE wiring not yet done; see comment near `AgentLoop.get_stream()` in `agent_loop.py` for the integration point

### Completion Checklist

- [x] `SpawnAgentRuntime.execute()` calls `_emit("SpawnAgent", {...})` before running the agent.
- [x] `SpawnAgentRuntime.execute()` calls `_emit("SpawnAgentResult", {...})` after completion.
- [x] `_emit` is a no-op when no callback is set.
- [x] With `forward_sync_subagent_events=True`, `CodeAgent` events from the subagent include a `"subagent"` key.
- [ ] `StreamEvent` name `"SpawnAgent"` passes through the server event buffer without filtering. *(deferred — SSE wiring not yet done)*

### Tests to Write

File: `tests/unit/test_agent_spawn.py` — **Phase 9 section**

```python
@pytest.mark.asyncio
async def test_execute_emits_spawn_agent_event(monkeypatch):
    from cuga.backend.agent_spawn import runtime
    events = []
    runtime.set_event_callback(lambda name, data: events.append((name, data)))
    # ... run mocked execute() ...
    assert any(name == "SpawnAgent" for name, _ in events)
    assert any(name == "SpawnAgentResult" for name, _ in events)
    runtime.set_event_callback(None)

def test_emit_noop_without_callback():
    from cuga.backend.agent_spawn.runtime import _emit
    _emit("SpawnAgent", {})  # must not raise

@pytest.mark.asyncio
async def test_forward_sync_subagent_events_includes_subagent_key(monkeypatch):
    # forward_sync_subagent_events=True
    # subagent stream includes {"script": "..."} chunk
    # forwarded event has "subagent" == agent name
```

---

## Phase 10 — Observability (Langfuse + OTEL)

### Context

Covers NFR-5, success criteria 7 and 8. Ensures spawned LLM calls nest under the parent Langfuse trace and that OTEL `session.id` is the same for sync and async spawns.

### Implementation Steps

- [x] **10.1 Fix `_build_invoke_config` in `runtime.py`**
  → `src/cuga/backend/agent_spawn/runtime.py`: `SpawnAgentRuntime._build_invoke_config()`, calls `sync_langfuse_callbacks_from_config` then `get_langfuse_invoke_config`

- [x] **10.2 OTEL session.id in `execute_async()`**
  → `src/cuga/backend/agent_spawn/runtime.py`: `SpawnAgentRuntime.execute_async()`, `set_session_attribute()` called before `asyncio.create_task()`

### Completion Checklist

- [ ] After `sync_langfuse_callbacks_from_config(parent_config)`, `get_langfuse_invoke_config()` returns a non-empty dict when Langfuse is configured.
- [ ] `set_session_attribute` is called in both `execute()` and `execute_async()` before any LLM call.
- [ ] In `execute_async()`, `set_session_attribute` is called **before** `asyncio.create_task()`.

### Tests to Write

File: `tests/unit/test_agent_spawn.py` — **Phase 10 section**

```python
def test_build_invoke_config_syncs_langfuse_callbacks(monkeypatch):
    # monkeypatch sync_langfuse_callbacks_from_config and get_langfuse_invoke_config
    # assert sync is called with parent_config before invoke_config is built

@pytest.mark.asyncio
async def test_execute_calls_set_session_attribute(monkeypatch):
    # monkeypatch set_session_attribute
    # run mocked execute()
    # assert set_session_attribute called with parent thread_id

@pytest.mark.asyncio
async def test_execute_async_calls_set_session_before_create_task(monkeypatch):
    # Verify set_session_attribute call precedes asyncio.create_task
    # (check call order via recorded call log)
```

---

## Phase 11 — Integration Tests & CI Validation

### Context

Covers success criteria 1–13 as an integration sweep. Validates the full path `parent → spawn → subagent → result → answer` without a live LLM.

### Implementation Steps

- [x] **11.1 Create a `data_analyst` fixture descriptor**
  → `tests/fixtures/agents/data_analyst/AGENT.md` — YAML frontmatter descriptor (name, tool_definitions, model, max_steps)
  → `tests/fixtures/agents/data_analyst/tools.py` — `summarise_list_async()` referenced by `tool_definitions`
  → `tests/fixtures/__init__.py`, `tests/fixtures/agents/__init__.py`, `tests/fixtures/agents/data_analyst/__init__.py` — package markers so the module path `tests.fixtures.agents.data_analyst.tools` is importable during tests

- [x] **11.2 Create `tests/integration/test_agent_spawn_integration.py`**
  → `tests/integration/test_agent_spawn_integration.py`

### Completion Checklist

- [ ] All tests in `test_agent_spawn_integration.py` pass with mocked `CugaAgent.stream`.
- [ ] `tools.summarise_list_async` is accessible from the subagent's tool set and absent from the parent's `_tools_context`.
- [ ] Async spawn + get_result completes within 5 seconds in CI (no live LLM).
- [ ] `ToolDefinitionError` is raised at descriptor load time, not at spawn time.

### Tests to Write

File: `tests/integration/test_agent_spawn_integration.py`

```python
@pytest.mark.asyncio
async def test_sync_spawn_returns_answer(tmp_path, monkeypatch):
    # Full path: discover data_analyst descriptor
    # Create registry, create spawn tools
    # Call spawn_agent("data_analyst", "Analyse [1,2,3]", mode="sync")
    # CugaAgent is mocked to return "count=3 sum=6"
    # Assert returned string == "count=3 sum=6"
    # (success criterion 2, 9)

@pytest.mark.asyncio
async def test_async_spawn_and_get_result(tmp_path, monkeypatch):
    # spawn_agent(..., mode="async") → future_id
    # Poll get_agent_result(future_id, timeout=5) → answer
    # (success criterion 3)

@pytest.mark.asyncio
async def test_async_spawn_graceful_failure(tmp_path, monkeypatch):
    # Mocked CugaAgent raises RuntimeError
    # get_agent_result → "[SpawnError] ..." string, not raise
    # (FR-9, success criterion 3)

def test_disabled_produces_no_diff_in_tools(monkeypatch):
    # agent_spawn.enabled=False
    # No spawn_agent in tools_for_prompt
    # No agents_prompt_section in generated prompt
    # (success criterion 4)

def test_tool_definitions_tool_absent_from_parent_context(tmp_path):
    # summarise_list is in subagent tools only
    # Parent tools_context does not contain "summarise_list"
    # (success criterion 11)

def test_skill_tools_absent_from_parent_context(tmp_path):
    # skill_tools tool is in subagent only
    # Parent context does not include it
    # (success criterion 12)

def test_invalid_module_raises_at_load_time(tmp_path):
    from cuga.backend.agent_spawn.tool_builder import ToolDefinitionError
    # tool_definition with module="nonexistent.module"
    # → ToolDefinitionError when build_tool_from_definition is called
    # (success criterion 13)

def test_data_analyst_descriptor_runs_in_ci_without_live_llm(tmp_path, monkeypatch):
    # discover_agents pointing at fixture dir
    # registry.get("data_analyst") is not None
    # build_tool_from_definition succeeds for summarise_list
    # (success criterion 10)
```

---

## Cross-Phase Dependency Order

```
Phase 1 (config)
    → Phase 2 (registry/loader)
        → Phase 3 (tool_builder + SKILL.md tools:)
            → Phase 4 (runtime.py)
                → Phase 5 (tools.py + prompt_utils)
                    → Phase 6 (cuga_lite_graph closure)
                        → Phase 7 (prepare_node)
                            → Phase 8 (Jinja template)
                                → Phase 9 (stream events)
                                    → Phase 10 (observability)
                                        → Phase 11 (integration tests)
```

> **Phases 3 and 2 can be parallelised** once the `ToolDefinition` dataclass interface is agreed (Phase 2's `registry.py`).
> **Phases 7 and 8 can be parallelised** once Phase 5 is complete.
> **Phases 9 and 10 can be parallelised** once Phase 7 is complete.

---

## Open Questions Resolution (from Proposal Section 14)

| ID | Question | Resolved Answer | Implemented in Phase |
|---|---|---|---|
| Q1 | Parallel `_tools_for_spawn` dict vs re-wrap at spawn time | Re-wrap at spawn time in `_resolve_parent_tools` | Phase 4 |
| Q2 | Async sandbox isolation | Force local executor for spawns in v1; document as limitation | Phase 4 `execute_async` |
| Q3 | Nesting depth guard | `ContextVar[int]` `_spawn_depth`, checked in `execute()` | Phase 4 |
| Q4 | Variable bridging | No bridging in v1 | N/A — no action needed |
| Q5 | Name collision built vs inherited | Built tools win (last-write in `_assemble_tools`) | Phase 4 |
| Q6 | ToolDefinitionError timing | At load time — `build_tool_from_definition` raises immediately | Phase 3 |

---

## Success Criteria Traceability

| # | Criterion (from proposal §16) | Verified by phase / test |
|---|---|---|
| 1 | Unit tests pass with mocked deps (error + timeout paths) | Phases 4, 5 unit tests |
| 2 | Integration test: parent spawns, receives result | `test_sync_spawn_returns_answer` (Phase 11) |
| 3 | Async spawn: 120s timeout + graceful failure | `test_async_spawn_*` (Phase 11) |
| 4 | `enabled=false` → zero diff in prompt/tools/timing | `test_disabled_produces_no_diff_in_tools` (Phase 11) |
| 5 | SpawnAgent + SpawnAgentResult in SSE stream + stream_events table | Phase 9 tests |
| 6 | Sync forwarding: CodeAgent decorated with `subagent` field | `test_forward_sync_subagent_events_includes_subagent_key` (Phase 9) |
| 7 | Spawned LLM calls nested under parent Langfuse trace | `test_build_invoke_config_syncs_langfuse_callbacks` (Phase 10) |
| 8 | Same `session.id` in OTEL for sync + async | `test_execute_async_calls_set_session_before_create_task` (Phase 10) |
| 9 | Full path integration test | `test_sync_spawn_returns_answer` (Phase 11) |
| 10 | data_analyst descriptor runs in CI without live LLM | `test_data_analyst_descriptor_runs_in_ci_without_live_llm` (Phase 11) |
| 11 | tool_definitions tool called + absent from parent | `test_tool_definitions_tool_absent_from_parent_context` (Phase 11) |
| 12 | skill_tools tool called + absent from parent | `test_skill_tools_absent_from_parent_context` (Phase 11) |
| 13 | Invalid module.function → ToolDefinitionError at load time | `test_invalid_module_raises_at_load_time` (Phase 11) + Phase 3 unit tests |
