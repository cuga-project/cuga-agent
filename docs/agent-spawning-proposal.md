# Agent Spawning for CugaLite — Architecture Proposal

**Author:** Iftach Shoham
**Date:** June 2026
**Status:** Proposal — Awaiting Review
**Scope Note:** Multimodal / image support is explicitly out of scope for this release. It is documented separately in Section 10 as a future enhancement.
**Target Release:** TBD

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Current Architecture — CugaLite Overview](#3-current-architecture--cugalite-overview)
4. [Proposed Feature — Agent Spawning](#4-proposed-feature--agent-spawning)
5. [Functional Requirements](#5-functional-requirements)
6. [Non-Functional Requirements](#6-non-functional-requirements)
7. [Architecture Design](#7-architecture-design)
8. [Subagent Descriptor Format (AGENT.md)](#8-subagent-descriptor-format-agentmd)
9. [Subagent Tool Creation](#9-subagent-tool-creation)
10. [Multimodal / Image Support — Future Work](#10-multimodal--image-support--future-work)
11. [UI Visibility](#11-ui-visibility)
12. [Tracing Integration](#12-tracing-integration)
13. [Implementation Plan](#13-implementation-plan)
14. [Open Questions](#14-open-questions)
15. [Risks and Mitigations](#15-risks-and-mitigations)
16. [Success Criteria](#16-success-criteria)

---

## 1. Executive Summary

This document proposes adding **agent spawning** to CUGA's CugaLite execution engine. The feature enables a running CugaLite agent to dynamically create and delegate work to child `CugaAgent` instances at runtime, using a declarative descriptor file (`AGENT.md`) to define each subagent's role, tools, and model.

Agent spawning is implemented as a first-class **tool** — fully consistent with CUGA's existing tool architecture — so no changes to the graph topology are required. The parent agent calls `spawn_agent(...)` exactly as it calls any other tool; the runtime handles `CugaAgent` construction, streaming, and result collection transparently.

This unlocks a class of multi-step enterprise workflows — parallel data gathering, role-separated reasoning, skill-scoped tooling — that cannot be expressed cleanly within a single flat agent loop today.

> **Out of scope for this release:** multimodal / image support (`enable_vision`). See Section 10 for the deferred design.

**Key design choices at a glance:**

| Decision | Choice | Rationale |
|---|---|---|
| Integration point | Tool injection in `prepare_node` | Zero graph changes; consistent with `load_skill` precedent |
| Subagent type | `CugaAgent` from SDK only | No custom loops; full SDK observability |
| Execution | `agent.stream()` | Consistent with supervisor and server patterns |
| Configuration | `AGENT.md` descriptor files | Mirrors `SKILL.md` convention; no code required |
| Default tool scope | Empty (explicit opt-in) | Principle of least privilege |
| Subagent tool creation | `StructuredTool.from_function` declared in `AGENT.md` or `SKILL.md` | Same SDK pattern used everywhere in CUGA; no new primitives |
| Feature gate | `agent_spawn.enabled` in `settings.toml` | Zero impact when disabled |
| Image / vision support | **Deferred** | Out of scope for this release; see Section 10 |

---

## 2. Problem Statement

### 2.1 Current Limitations

CugaLite is a high-performing single-agent loop that handles complex tasks well. Several real-world use cases, however, push against the ceiling of a single agent:

| Use case | Why a single agent struggles |
|---|---|
| Parallel data gathering across multiple APIs | The agent executes sequentially; concurrent sub-tasks compound latency |
| Role separation (analyst → summariser → formatter) | All reasoning must fit one prompt context, increasing noise and hallucination risk |
| Reusable reasoning patterns across projects | No mechanism to package and share agent "personas" the way `SKILL.md` packages workflows |
| Skill-specific tooling | A skill may require a bespoke tool (e.g. a domain-specific API wrapper) that should not be exposed to the parent agent's full context |
| Processing images / multimodal content | *(Deferred — see Section 10)* |

### 2.2 The Missing Primitive

CUGA already supports multi-agent patterns at the supervisor level (`CugaSupervisor`). However, supervisor-level delegation requires:

- Pre-configured agents defined at startup
- A separate graph topology
- Access that is not available from inside a running CugaLite execution

There is no lightweight way for an *executing* agent to spawn a *purpose-built child agent on demand* — with a custom tool set and model, instantiated from a descriptor file, without restarting or reconfiguring the parent.

**Agent spawning fills this gap.** It is explicitly *not* a replacement for `CugaSupervisor`, which remains the right choice for statically-defined multi-agent pipelines. Spawning is the right choice when the need for a subagent is determined dynamically, at runtime, by the executing LLM.

---

## 3. Current Architecture — CugaLite Overview

CugaLite is a LangGraph-based single-agent loop that follows a **code-act** pattern: the LLM writes Python that calls tools as coroutines, the code is executed in a sandboxed environment (E2B, OpenSandbox, Docker, or native), and results flow back to the model.

```
┌───────────────────────────────────────────────────────┐
│                  CugaLite Graph                       │
│                                                       │
│  prepare_node                                         │
│    └─ load tools, skills, apps into tools_context     │
│    └─ build tools_for_prompt (available tool names)   │
│                                                       │
│  call_model                                           │
│    └─ LLM receives prompt + tool catalog              │
│    └─ emits Python code block                         │
│                                                       │
│  execute_node (sandbox)                               │
│    └─ runs Python; tools are coroutines in locals     │
│    └─ stdout/stderr captured as observations          │
│                                                       │
│  [loop until done or step cap]                        │
└───────────────────────────────────────────────────────┘
```

**Key extension point:** `prepare_node` is where tools are injected. Any new tool follows the same pattern: define a `StructuredTool`, register it in `tools_context`, add it to `tools_for_prompt`. The graph itself does not change.

**Existing precedent:** the Skills system (`SKILL.md` → `load_skill` tool) already uses exactly this pattern. Agent spawning follows the identical extension path, making it a natural and low-risk addition.

---

## 4. Proposed Feature — Agent Spawning

### 4.1 Core Idea

Introduce a `spawn_agent` tool (and a companion `get_agent_result` tool) that allows the executing LLM to dynamically create a `CugaAgent` subagent from a named descriptor, run it with a custom tool set and model, and collect its result — either synchronously (blocking) or asynchronously (fire-and-forget).

### 4.2 From the LLM's Perspective

The LLM sees `spawn_agent` as just another tool call. All complexity — agent construction, streaming, result collection — is hidden inside the tool's implementation.

```python
# Synchronous — blocks until the subagent completes and returns its answer
summary = await spawn_agent(
    name="data_analyst",
    task="Summarise total orders by region for Q1 2026. Return JSON.",
    mode="sync",
)
print(summary)

# Asynchronous — fires a background task and returns a handle
future_id = await spawn_agent(
    name="data_analyst",
    task="Compute year-over-year growth from the attached CSV.",
    mode="async",
)
# ... continue doing other work in the parent ...
result = await get_agent_result(future_id, timeout=60)
```

### 4.3 Design Principles

- **Consistent with CUGA tool architecture.** Uses the same `StructuredTool.from_function` pattern as `load_skill`, `find_tools`, and every other CugaLite tool.
- **`CugaAgent`-only.** Spawned agents are always `CugaAgent` instances from the Python SDK. No custom ReAct wiring, no new graph nodes, no new execution surfaces.
- **Streaming internally.** The runtime uses `agent.stream()` to consume the subagent, consistent with how the CUGA server and supervisor consume the SDK today.
- **Declarative descriptor.** Subagent configuration lives in `AGENT.md` files under `.agents/agents/`, mirroring the `SKILL.md` convention.
- **Least-privilege by default.** Spawned agents receive an empty tool set unless explicitly configured. The wildcard `tools: ["*"]` requires an explicit opt-in per descriptor.
- **Feature-flagged.** The entire feature is gated behind `agent_spawn.enabled = false` in `settings.toml`. When disabled, there is zero impact on prompt size, tool count, or execution time.

---

## 5. Functional Requirements

### FR-1 — Toggle

The feature MUST be enable/disable-able via a single configuration key (`agent_spawn.enabled`). When disabled, no spawn tools are injected, no descriptor files are loaded, and the prompt is unchanged.

### FR-2 — Tool Consistency

The `spawn_agent` and `get_agent_result` tools MUST follow the same implementation pattern as all existing CUGA tools:
- Pydantic `BaseModel` for input schema
- `StructuredTool.from_function(coroutine=..., name=..., description=..., args_schema=...)`
- Registered in `tools_context` and `tools_for_prompt` during `prepare_node`

### FR-3 — `CugaAgent` Subagents

Spawned agents MUST be instances of `CugaAgent` (from `cuga.sdk`). The runtime MUST NOT create custom LangGraph graphs, custom ReAct loops, or any alternative agent type.

### FR-4 — Streaming Execution

The spawn runtime MUST use `CugaAgent.stream()` internally. For synchronous spawns, all stream events are consumed and the final answer is extracted and returned as a string. For asynchronous spawns, stream consumption runs as an `asyncio.Task`.

### FR-5 — Descriptor-Driven Configuration

Each subagent MUST be configurable via an `AGENT.md` file containing YAML frontmatter and a markdown body. The descriptor MUST support at minimum:

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Unique identifier used in `spawn_agent(name=...)` |
| `description` | Yes | Shown in the `<available_agents>` prompt block |
| `tools` | No | Tool names from the parent context; `["*"]` = inherit all |
| `tool_definitions` | No | Inline tool declarations built for this subagent (see Section 9) |
| `skill_tools` | No | Names of skills whose declared tools should be loaded for this subagent |
| `model` | No | LLM override for this subagent |
| `thread_id_prefix` | No | Prefix for the auto-generated thread ID |
| `max_steps` | No | Override `cuga_lite_max_steps` for this subagent |

### FR-6 — Synchronous and Asynchronous Execution

`spawn_agent` MUST support:
- `mode="sync"` — awaits completion and returns the answer as a string
- `mode="async"` — creates a background task and returns a `future_id` string

`get_agent_result` MUST accept a `future_id` and a `timeout`, and return the answer string or an informative in-progress message.

### FR-7 — Thread ID Isolation

Every spawned subagent MUST run with a unique `thread_id` formatted as `{prefix}_{uuid4().hex[:8]}`. Spawned agents MUST NOT share or reuse the parent's `thread_id`.

### FR-8 — Descriptor Discovery

On each `prepare_node` invocation, the runtime MUST walk `agent_spawn.agents_dir` and an optional global agents directory for `AGENT.md` files. Discovered agents MUST be listed in an `<available_agents>` block injected into the system prompt, mirroring `<available_skills>`.

### FR-9 — Error Handling in Async Spawns

When an async-spawned agent raises an exception, `get_agent_result` MUST return the error as a descriptive string rather than re-raising it. The parent LLM MUST be able to handle the failure gracefully in its code loop.

### FR-10 — Subagent Tool Creation from AGENT.md

The descriptor format MUST support a `tool_definitions` block that declares one or more new `StructuredTool` instances to be constructed and passed exclusively to the spawned `CugaAgent`. Each definition MUST specify, at minimum: a tool `name`, a `description` (used in the subagent's prompt), and a `module`/`function` reference pointing to an importable Python coroutine. The runtime MUST build each tool using `StructuredTool.from_function` — the same pattern used for all CUGA tools — and MUST NOT use any alternative tool-construction mechanism.

### FR-11 — Subagent Tool Creation from SKILL.md

A `SKILL.md` file MUST be able to declare a `tools:` block in its frontmatter listing one or more tool definitions using the same schema as FR-10. When a descriptor's `skill_tools` field references a skill by name, the spawn runtime MUST load that skill's tool definitions and merge them into the subagent's tool set alongside any tools listed in `tool_definitions`. A skill's declared tools MUST NOT be injected into the parent agent's tool context — they are scoped exclusively to subagents that reference that skill.

---

## 6. Non-Functional Requirements

### NFR-1 — Zero Impact When Disabled

When `agent_spawn.enabled = false`, the feature MUST add zero overhead: no file I/O, no imports of spawn modules, no prompt changes, and no additional LLM tokens.

### NFR-2 — No Graph Topology Changes

The implementation MUST NOT add, remove, or rewire LangGraph nodes in `create_cuga_lite_graph`. The feature is purely additive at the tool-injection level.

### NFR-3 — Model and Tool Isolation

Spawned subagents MUST NOT automatically inherit the parent's full tool set unless explicitly configured via `tools: ["*"]`. The default is an empty tool set, requiring explicit declaration of every tool needed.

### NFR-4 — Async Safety

Async-spawned agents share the parent's event loop. The implementation MUST handle sandbox contention gracefully (see Open Questions). It MUST NOT deadlock or corrupt shared state.

### NFR-5 — Observability

Spawned agent invocations MUST be visible to the user in the UI (new `SpawnAgent` / `SpawnAgentResult` stream events) and MUST appear as traceable child spans under the parent's trace in both Langfuse and OpenLit/OTEL. Full design in Sections 11 and 12.

### NFR-6 — Testability

Each component (loader, registry, runtime, tools) MUST be independently unit-testable without requiring a live LLM or sandbox. The runtime MUST accept a mock `CugaAgent` factory to enable full test coverage of the spawn path.

### NFR-7 — Subagent Tool Isolation

Tools created for a spawned subagent (via `tool_definitions` or `skill_tools`) MUST be scoped exclusively to that subagent's `CugaAgent` instance. They MUST NOT be registered in the parent's `tools_context`, listed in the parent's `tools_for_prompt`, or visible to the parent LLM in any way. The runtime MUST validate that each declared `module.function` reference is importable before constructing the agent, and MUST raise a descriptive `ToolDefinitionError` at spawn time (not at descriptor load time) if the reference cannot be resolved.

---

## 7. Architecture Design

### 7.1 Component Map

```
src/cuga/backend/agent_spawn/
├── __init__.py            — package exports
├── loader.py              — walks .agents/agents/, parses AGENT.md frontmatter
├── registry.py            — AgentDescriptorEntry + AgentDescriptorRegistry
├── runtime.py             — SpawnAgentRuntime: CugaAgent construction + stream execution
├── tool_builder.py        — builds StructuredTool instances from tool_definitions + skill_tools
├── tools.py               — StructuredTool definitions + create_spawn_tools() factory
└── prompt_utils.py        — format_available_agents_block() for prompt injection
```

The package is self-contained. No existing module imports from it; it only *receives* context (tools, model config, futures store) passed in at construction time. This keeps the dependency graph clean and the package independently testable.

### 7.2 Runtime Flow

```
prepare_node (on each graph invocation)
│
├── settings.agent_spawn.enabled?
│   └── YES:
│       ├── discover_agents(agents_dir) → AgentDescriptorRegistry
│       ├── create_spawn_tools(registry, tools_for_spawn, model_config, futures_store)
│       │     → [spawn_agent StructuredTool, get_agent_result StructuredTool]
│       ├── inject both into tools_context (as coroutines)
│       ├── inject both into tools_for_prompt
│       └── format_available_agents_block(registry) → injected into system prompt
│
│   └── NO: no-op (zero overhead)
│
call_model
│   LLM writes Python using spawn_agent / get_agent_result
│
execute_node (sandbox)
│   spawn_agent("data_analyst", task="...", mode="sync")
│   │
│   └── SpawnAgentRuntime.execute()
│       ├── resolve AgentDescriptorEntry by name
│       ├── resolve tools: filter _tools_for_spawn to names in descriptor.tools
│       ├── CugaAgent(tools=resolved_tools, model=..., special_instructions=body)
│       ├── generate thread_id = f"{prefix}_{uuid4().hex[:8]}"
│       │
│       ├── mode="sync":
│       │   async for state in agent.stream(task, thread_id=...):
│       │       if state.get("answer"): final_answer = state["answer"]
│       │   return final_answer
│       │
│       └── mode="async":
│           task = asyncio.create_task(collect_stream(agent, task, thread_id))
│           futures_store[future_id] = task
│           return future_id
```

**Tool construction detail** — the `tool_builder.py` step, expanded:

```
SpawnAgentRuntime.execute()
│
├── resolve tools from parent context (names in descriptor.tools)
│
├── tool_builder.build_tools(entry)
│   ├── for each entry in descriptor.tool_definitions:
│   │   ├── import module.function via importlib
│   │   ├── validate it is a coroutine function
│   │   └── StructuredTool.from_function(coroutine=fn, name=..., description=..., args_schema=...)
│   │
│   └── for each skill name in descriptor.skill_tools:
│       ├── load skill frontmatter from SKILL.md (reuse parse_markdown_with_frontmatter)
│       ├── for each tool in skill.tools block:
│       │   ├── import module.function via importlib
│       │   └── StructuredTool.from_function(...)
│       └── merge into built_tools list
│
├── final_tools = parent_resolved_tools + built_tools  (built_tools take precedence on name collision)
└── CugaAgent(tools=final_tools, ...)
```

### 7.3 Changes to Existing Modules

The table below enumerates every file modified and the nature of each change. All are additive; no existing logic is altered.

| File | Change | Size |
|---|---|---|
| `prepare_node.py` | Add `agent_spawn` block mirroring the `skills_cfg_on` block | ~14 lines |
| `prompt_utils.py` | Add `agents_enabled: bool` + `agents_prompt_section: str` parameters | ~8 lines |
| `mcp_prompt.jinja2` | Add `{% if agents_enabled %}` section after skills block | ~5 lines |
| `cuga_lite_graph.py` | Add `_spawn_futures: dict` closure | 1 line |
| `graph_adapter.py` | Expose `spawn_futures_store` field | ~3 lines |
| `settings.toml` | Add `[agent_spawn]` section | 7 keys (includes `forward_sync_subagent_events`) |
| `config.py` | Add 7 `Validator` entries | ~14 lines |
| `skill_loader.py` | Add optional `tools:` block parsing to `parse_skill_frontmatter` | ~20 lines |
| `agent_loop.py` | Add `SpawnAgent` and `SpawnAgentResult` cases to `get_event_message()` | ~12 lines |

No other files are modified.

---

## 8. Subagent Descriptor Format (AGENT.md)

Descriptors use the same frontmatter format already parsed by the Skills system (`parse_markdown_with_frontmatter`). No new parser is needed.

### 8.1 Example Descriptor

```markdown
---
name: data_analyst
description: "Analyses structured data from CSVs and databases. Spawn when the task
              requires SQL queries or statistical summaries over tabular data."
tools:
  - knowledge_search_knowledge
  - find_tools
skill_tools:
  - data                        # loads query_postgres from data/SKILL.md
tool_definitions:
  - name: summarise_dataframe
    description: "Return descriptive statistics for a pandas DataFrame passed as JSON."
    module: cuga.skills.data.df_tools
    function: summarise_dataframe_async
    args_schema: SummariseDataframeInput
model: claude-sonnet-4-6
thread_id_prefix: data_analyst
max_steps: 8
---

# Data Analyst Agent

## Role
You receive a data analysis question and access to database and file tools.
Execute the necessary queries or computations and return a structured JSON result.

## Constraints
- Run read-only queries only.
- Do not ask clarifying questions.
- Always return valid JSON.
```

### 8.2 Field Reference

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string | Yes | — | Unique identifier; used in `spawn_agent(name=...)` |
| `description` | string | Yes | — | Shown in `<available_agents>` prompt block |
| `tools` | list[string] | No | `[]` | Tool names from the parent context; `["*"]` = inherit all |
| `tool_definitions` | list[ToolDefinition] | No | `[]` | New tools constructed for this subagent at spawn time (see Section 9) |
| `skill_tools` | list[string] | No | `[]` | Skill names whose `tools:` block should be loaded for this subagent (see Section 9) |
| `model` | string | No | Parent model | LLM override for this subagent |
| `thread_id_prefix` | string | No | `"spawn"` | Prefix for the auto-generated thread ID |
| `inherit_parent_tools` | bool | No | Global setting | Overrides `agent_spawn.inherit_parent_tools` per descriptor |
| `max_steps` | integer | No | Parent setting | Override `cuga_lite_max_steps` for this subagent |
| `enable_vision` | bool | N/A | — | **Deferred — not supported in this release** (see Section 10) |

The descriptor **body** (the markdown below the frontmatter) becomes the subagent's `special_instructions`, prepended to the standard system prompt.

### 8.3 Discovery Paths

Descriptors are discovered in priority order:

1. `.agents/agents/<agent_name>/AGENT.md` — project-level, version-controlled alongside the codebase
2. `~/.config/agents/agents/<agent_name>/AGENT.md` — user-level, shared across projects

Project-level descriptors take precedence over user-level ones with the same name.

---

## 9. Subagent Tool Creation

### 9.1 Motivation

The tool set a spawned agent needs is often not identical to what the parent agent has available. A data analyst subagent may need a domain-specific SQL wrapper or DataFrame helper. These tools are scoped to one subagent's purpose and should neither pollute the parent's tool context nor be hardcoded into the CUGA SDK.

The solution is to let tool definitions travel with the agent or skill descriptor that needs them — declared declaratively, constructed at spawn time using the same `StructuredTool.from_function` pattern the rest of CUGA uses.

### 9.2 Two Declaration Sources

**Source 1: `AGENT.md` — tools defined directly for a specific subagent**

The descriptor's frontmatter gains a `tool_definitions` list. Each entry names a Python coroutine by importable reference. The spawn runtime imports and wraps it into a `StructuredTool` at the moment the subagent is constructed.

```yaml
---
name: data_analyst
description: "Analyses structured data from CSVs and databases."
tool_definitions:
  - name: query_postgres
    description: "Run a read-only SQL query against the project Postgres instance."
    module: cuga.skills.data.pg_tools
    function: query_postgres_async
    args_schema: QueryPostgresInput   # Pydantic model in the same module
  - name: summarise_dataframe
    description: "Return descriptive statistics for a pandas DataFrame passed as JSON."
    module: cuga.skills.data.df_tools
    function: summarise_dataframe_async
---
```

**Source 2: `SKILL.md` — tools declared by the skill that requires them**

A skill can declare a `tools:` block in its own frontmatter. These tools are not injected into the parent agent; they become available only when a subagent explicitly lists that skill in its `skill_tools` field. This keeps skill-specific tooling encapsulated inside the skill.

```yaml
# .agents/skills/data/SKILL.md frontmatter
---
name: data
description: "Queries and analyses structured data from databases and files."
tools:
  - name: query_postgres
    description: "Run a read-only SQL query against the project Postgres instance."
    module: cuga.skills.data.pg_tools
    function: query_postgres_async
    args_schema: QueryPostgresInput
---
```

A descriptor that wants those tools then simply declares:

```yaml
# .agents/agents/data_analyst/AGENT.md frontmatter
---
name: data_analyst
description: "Analyses structured data. Spawn for SQL queries or statistical summaries."
skill_tools:
  - data
model: claude-sonnet-4-6
---
```

The spawn runtime loads the `data` skill, extracts its `tools:` block, and merges the resulting `StructuredTool` instances into the subagent's tool set — without touching the parent's context.

### 9.3 Tool Definition Schema

Each entry in `tool_definitions` (and in a skill's `tools:` block) supports the following fields:

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | Tool name as it appears in the subagent's prompt |
| `description` | string | Yes | Natural-language description used by the LLM to decide when to call the tool |
| `module` | string | Yes | Fully-qualified Python module path (e.g. `cuga.skills.data.pg_tools`) |
| `function` | string | Yes | Name of the async coroutine function within that module |
| `args_schema` | string | No | Name of the Pydantic `BaseModel` class in the same module; omit for zero-argument tools |

### 9.4 Construction Flow

At spawn time, `tool_builder.py` resolves and constructs tools in this order:

1. **Parent-context tools** — tools inherited from the parent via the `tools` field (names resolved from `_tools_for_spawn`).
2. **Skill tools** — for each name in `skill_tools`, load the skill's `SKILL.md`, extract its `tools:` block, import and wrap each coroutine.
3. **Inline tool definitions** — for each entry in `tool_definitions`, import and wrap the coroutine.

On name collision, later entries (inline definitions) take precedence over earlier ones (inherited parent tools). This allows a descriptor to intentionally override a parent tool with a subagent-specific version.

```python
# tool_builder.py — simplified
import importlib
from langchain.tools import StructuredTool

def build_tools(entry: AgentDescriptorEntry, skill_registry) -> list[StructuredTool]:
    built = []

    for skill_name in entry.skill_tools:
        skill = skill_registry.get(skill_name)
        for defn in skill.tool_definitions:
            built.append(_build_one(defn))

    for defn in entry.tool_definitions:
        built.append(_build_one(defn))

    return built

def _build_one(defn: ToolDefinition) -> StructuredTool:
    mod = importlib.import_module(defn.module)
    fn = getattr(mod, defn.function)
    schema = getattr(mod, defn.args_schema) if defn.args_schema else None
    return StructuredTool.from_function(
        coroutine=fn,
        name=defn.name,
        description=defn.description,
        args_schema=schema,
    )
```

### 9.5 From the LLM's Perspective

The LLM authoring the subagent task sees these tools in its prompt catalog exactly as it would any other tool — no distinction is made between inherited tools and freshly-constructed ones. The `<available_agents>` block shown to the *parent* LLM does not enumerate subagent tools; it shows only the agent's `description`, keeping the parent prompt lean.

```python
# Parent agent spawns a data_analyst with its private query_postgres tool
result = await spawn_agent(
    name="data_analyst",
    task="How many orders were placed in Q1 2026? Return a single integer.",
    mode="sync",
)
# The data_analyst subagent calls query_postgres internally;
# the parent agent never sees or uses that tool.
```

---

## 10. Multimodal / Image Support — Future Work

> **This section is deferred and out of scope for the current release.** It is preserved here to capture the design intent for a follow-up iteration.

The original motivation for agent spawning included a use case where a parent agent (using a text-only model) delegates slide/image reading to a vision-capable subagent. That use case is still valid but has been deprioritised. The core spawning infrastructure (Sections 1–9) is designed to accommodate it without structural changes.

When multimodal support is taken up, the key design decisions are:

- **`enable_vision` descriptor field** — gates multimodal content passing; validated at load time against `model_supports_vision(model_config)`.
- **`SpawnAgentInput.task`** — accept `str | list` to support pre-built multimodal content blocks alongside plain text.
- **Fallback behaviour** — if `enable_vision: true` but the configured model does not support vision, fall back to plain-text with a logged warning (no exception).
- **Helper reuse** — use the existing `model_supports_vision()` from `cuga.backend.llm.utils.helpers`, consistent with the browser planner (issue #214).

The image content format (whether `task` accepts `str | list` or a separate `image_urls` parameter) is an open question to be resolved when this work is resumed.

---

## 11. UI Visibility

### 11.1 New Stream Event Types

CugaLite's streaming layer is built on `StreamEvent(name: str, data: str)` objects produced by `get_event_message()` in `agent_loop.py` and consumed by the `/stream` SSE endpoint. Two new event names follow the same convention:

| Event name | When emitted | Key data fields |
|---|---|---|
| `SpawnAgent` | At the moment `spawn_agent(...)` is called, before execution starts | `agent_name`, `task` (truncated to 200 chars), `mode` (`sync`/`async`), `thread_id` |
| `SpawnAgentResult` | When a spawned agent finishes — sync: after stream exhausted; async: when `get_agent_result` resolves | `agent_name`, `thread_id`, `status` (`completed`/`failed`), `answer` or `error` |

Example SSE output:

```
event: SpawnAgent
data: {"agent_name": "data_analyst", "task": "How many orders in Q1 2026?", "mode": "sync", "thread_id": "data_analyst_1a2b3c4d"}

event: SpawnAgentResult
data: {"agent_name": "data_analyst", "thread_id": "data_analyst_1a2b3c4d", "status": "completed", "answer": "1 482"}
```

### 11.2 Subagent Step Forwarding

While a sync-spawned subagent runs, it emits its own `CodeAgent` events. These are optionally forwarded to the parent stream so the user can follow the subagent's reasoning in real time. Forwarded events are decorated with a `subagent` field to distinguish them from parent-agent steps:

```
event: CodeAgent
data: {"subagent": "data_analyst", "thread_id": "data_analyst_1a2b3c4d", "code": "...", "execution_output": "..."}
```

**Async spawns suppress forwarding** — interleaving a background subagent's steps with unrelated parent steps would be confusing. Only the `SpawnAgent` / `SpawnAgentResult` bookend events appear in the parent stream for async mode.

A single config key controls this: `agent_spawn.forward_sync_subagent_events = true` (default). Setting it to `false` suppresses forwarding for sync spawns as well, showing only bookend events.

### 11.3 Emission Points

`SpawnAgentRuntime` receives a reference to the parent's event queue (the same `stream_events_buffer` already plumbed through `execute_node`). Emission requires no new infrastructure:

```
SpawnAgentRuntime.execute()
├── emit SpawnAgent event → stream_events_buffer
├── [if sync and forward_sync_subagent_events]:
│     async for state in agent.stream(...):
│         re-wrap CodeAgent/Answer states → emit to stream_events_buffer
├── emit SpawnAgentResult event → stream_events_buffer
└── return answer
```

### 11.4 Persistence

Both `SpawnAgent` and `SpawnAgentResult` events (and any forwarded subagent steps) are persisted to the existing `stream_events` table alongside parent-agent events. They carry the **parent's** `thread_id`, ensuring they appear in the same conversation history record and are replayed correctly by `get_stream_events()`.

---

## 12. Tracing Integration

### 12.1 Langfuse

CUGA propagates Langfuse tracing via `CallbackHandler` objects carried in LangGraph config. The same mechanism is reused for spawned agents — no new primitives are needed.

`SpawnAgentRuntime.execute()` calls the existing `get_langfuse_invoke_config()` helper from `langfuse_tracing.py` before constructing the `CugaAgent`. This produces a config that:

1. Carries the **parent's Langfuse callbacks** (via `get_langfuse_callbacks(parent_config)`), so the subagent's LLM calls are automatically attributed to the parent trace.
2. Sets **`langfuse_trace_id`** in `config["configurable"]` to the parent's trace ID, so all subagent spans nest correctly under the same top-level trace.

This is identical to the pattern already used for nested policy system calls (`TraceScopedLangfuseCallbackHandler`). The resulting Langfuse trace structure:

```
Trace: parent-thread-abc123
├── LLM: call_model (parent step 1)
├── Tool: spawn_agent("data_analyst")          ← parent sandbox step
│   ├── LLM: data_analyst / call_model (step 1)
│   ├── Sandbox: data_analyst / execute (step 1)
│   └── LLM: data_analyst / call_model (step 2)
└── LLM: call_model (parent step 2)
```

For **async spawns**, the Langfuse callback objects are captured at the time `spawn_agent` is called (inside the parent's sandbox step) and stored alongside the `asyncio.Task` in `_spawn_futures`. When the background task runs, it uses the captured callbacks — not a fresh handler — so spans remain linked to the parent trace even if the parent's graph step has already advanced.

### 12.2 OpenLit / OTEL

The `SessionSpanProcessor` in `openlit_init.py` tags every OTEL span with `session.id` by reading the `_current_session_id` context variable (set via `set_session_attribute()`).

**Sync spawns** — the subagent runs in the same coroutine chain as the parent, so `_current_session_id` is already set; no extra action needed.

**Async spawns** — Python's `asyncio.create_task()` copies the current `contextvars.Context` to the new task, so `_current_session_id` is automatically inherited. `SpawnAgentRuntime` MUST call `set_session_attribute(parent_thread_id)` before creating the task (not inside it) to guarantee the value is captured in the snapshot.

The result: every span emitted by a spawned agent — regardless of sync or async mode — appears under the same session in OTEL-compatible backends (Grafana, Datadog, etc.) as the parent agent.

### 12.3 Summary of Changes Required

| File | Change |
|---|---|
| `runtime.py` | Call `get_langfuse_invoke_config(parent_config)` before constructing `CugaAgent`; call `set_session_attribute(parent_thread_id)` before streaming |
| `tools.py` | Pass parent `stream_events_buffer` and parent `config` into `SpawnAgentRuntime` |
| `agent_loop.py` | Add `SpawnAgent` and `SpawnAgentResult` handling in `get_event_message()` and in the event-forwarding loop |
| `settings.toml` | Add `forward_sync_subagent_events = true` under `[agent_spawn]` |

---

## 13. Implementation Plan

Work is sequenced so that each phase is independently shippable and testable before the next begins. Phases 1 and 2 can be completed without touching any runtime code; integration only happens in Phase 4.

| Phase | Description | Files | Effort |
|---|---|---|---|
| **1** | Config scaffolding — add `[agent_spawn]` section and validators. No logic, zero risk. | `settings.toml`, `config.py` | 0.5 days |
| **2** | Descriptor loader and registry — reuse `parse_markdown_with_frontmatter`; define `AgentDescriptorEntry` dataclass including `tool_definitions` and `skill_tools` fields. Unit-testable with fixture files. | `loader.py`, `registry.py` | 1 day |
| **3** | Tool builder — implement `tool_builder.py`: importlib resolution, `StructuredTool.from_function` construction, skill frontmatter extraction, merge ordering. Unit-testable with fixture modules and mock skill registry. | `tool_builder.py`, `skill_loader.py` | 1 day |
| **4** | Spawn runtime, sync path only — implement `SpawnAgentRuntime.execute()` for `mode="sync"`, integrate `tool_builder`, wire `CugaAgent.stream()`. Unit-testable with mock `CugaAgent`. | `runtime.py`, `tools.py` | 2 days |
| **5** | Integration — inject spawn tools in `prepare_node`, add `<available_agents>` block to system prompt. First full end-to-end test including subagent with a skill-declared tool. | `prepare_node.py`, `prompt_utils.py`, `mcp_prompt.jinja2` | 1 day |
| **6** | Async execution path — add `_spawn_futures` store, implement async task creation and `get_agent_result`, handle timeouts and error propagation. | `runtime.py`, `tools.py`, `cuga_lite_graph.py`, `graph_adapter.py` | 1.5 days |
| **7** | UI events — emit `SpawnAgent` / `SpawnAgentResult` events from `SpawnAgentRuntime`; wire `stream_events_buffer` into runtime; add forwarding loop for sync subagent steps; persist events to `stream_events` table. | `runtime.py`, `tools.py`, `agent_loop.py` | 1 day |
| **8** | Tracing integration — call `get_langfuse_invoke_config()` in runtime; propagate `session_id` for async tasks; verify Langfuse child-span nesting and OTEL session grouping end-to-end. | `runtime.py`, `tools.py` | 0.5 days |
| **9** | Sample descriptors and integration tests — ship a `data_analyst` descriptor (skill tool + inline tool definition). Integration test covers sync and async paths, UI event sequence, Langfuse span nesting, and verifies subagent-specific tools absent from parent context. | `.agents/agents/data_analyst/AGENT.md`, `.agents/skills/data/SKILL.md`, `tests/` | 1 day |

**Total estimated effort: ~9.5 developer days**

> **Note on sequencing:** Phase 3 (tool builder) can be developed in parallel with Phase 2 once the `ToolDefinition` dataclass interface is agreed. Phase 4 depends on both 2 and 3. Phases 4 and 6 can be split across two engineers once the `SpawnAgentRuntime` interface is stable. Phases 7 and 8 can also run in parallel once Phase 5 is complete.

---

## 14. Open Questions

The following decisions should be resolved before implementation begins. Each is a genuine trade-off, not a stylistic preference. Recommended resolutions are provided as a starting point for discussion.

| # | Question | Options | Recommendation |
|---|---|---|---|
| **Q1** | **Tool resolution** — `_tools_context` holds raw coroutines; `CugaAgent` needs `BaseTool` objects. | (a) Keep a parallel `_tools_for_spawn: Dict[str, StructuredTool]` in `prepare_node`. (b) Re-wrap coroutines into `StructuredTool` at spawn time. | **(a)** — avoids redundant re-wrapping; one canonical reference per tool |
| **Q2** | **Async sandbox isolation** — async-spawned agents share the parent's E2B/OpenSandbox singleton. | (a) Force `local` executor for spawned agents in v1. (b) Document the limitation and leave to operators. (c) Allocate a new sandbox per spawn. | **(a) for v1** — simplest path; documented; revisit in v2 |
| **Q3** | **Nesting depth** — a spawned agent could itself call `spawn_agent`, creating unbounded recursion. | (a) Add `max_spawn_depth` config key + `contextvars` depth tracker. (b) Document and trust operators. | **(a)** — small implementation cost, prevents runaway cost and recursion |
| **Q4** | **Variable bridging** — after a sync spawn, should the subagent's `variables` be merged into the parent's scope? | (a) Yes, behind a `bridge_variables: bool = True` flag. (b) No — keep scopes strictly separate. | **(b) for v1** — scope isolation is safer; bridging can be added in a follow-up if there is demand |
| **Q5** | **Name collision between built tools and inherited parent tools** — if a `tool_definition` or `skill_tools` entry shares a name with an inherited parent tool, which wins? | (a) Built tools always win (last-write semantics). (b) Raise a `ToolDefinitionError` and force the descriptor author to resolve the conflict explicitly. (c) Inherited tools win; built tools are additive only. | **(a)** — predictable, allows intentional override; document the precedence order clearly |
| **Q6** | **When should `ToolDefinitionError` be raised?** — at descriptor load time (startup) or at spawn time (runtime). | (a) At spawn time — lazy, only pay cost when the agent is actually used. (b) At load time — fail fast, surface broken descriptors immediately on startup. | **(b)** — prefer fail-fast; a broken descriptor that only surfaces at runtime is hard to debug |

---

## 15. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Async-spawned agents cause sandbox contention (E2B) | Medium | Medium | Force `local` executor for spawned agents in v1 (Q2); revisit in v2 |
| Recursive agent spawning causing runaway cost | Low | High | `max_spawn_depth` guard via `contextvars` (Q3); default to depth 2 |
| `tools: ["*"]` grants spawned agent access to destructive tools | Medium | High | `inherit_parent_tools = false` default; `["*"]` requires explicit opt-in per descriptor |
| Thread ID collision corrupting parent's `MemorySaver` checkpoint | Low | High | Runtime always generates a fresh thread ID via UUID; parent's ID is never reused or exposed to subagent |
| Streaming API change in a future `CugaAgent` SDK version | Low | Medium | Thin `runtime.py` wrapper isolates spawn logic from SDK surface; updating one file is sufficient |
| Descriptor proliferation making prompt too long | Low | Low | Descriptions are short; if this becomes a problem, add a `max_agents_in_prompt` config key to filter by relevance |
| A `tool_definitions` module reference is importable at load time but broken at spawn time (e.g. env-specific dependency) | Low | Medium | Validate imports eagerly at descriptor load time (Q6 recommendation); include in CI fixture test suite |
| A skill's `tools:` block is used directly by the parent agent (misuse) | Low | Low | Skill tool definitions are only parsed by `tool_builder.py`, which is only called from `SpawnAgentRuntime`; the parent's `prepare_node` path never touches them |

---

## 16. Success Criteria

The feature is considered complete and production-ready when all of the following are met:

1. `spawn_agent` and `get_agent_result` pass all unit tests with mocked dependencies, including error and timeout paths.
2. A CugaLite agent can spawn a subagent, receive its result, and incorporate it into the parent answer — verified by an integration test.
3. Async spawning with `get_agent_result` works correctly under a 120-second timeout, including graceful handling of subagent failure.
4. `agent_spawn.enabled = false` produces zero difference in prompt size, tool count, or execution time compared to the baseline (verified by automated benchmark).
5. A `SpawnAgent` event is emitted at spawn time and a `SpawnAgentResult` event is emitted on completion; both appear in the SSE stream and in the `stream_events` persistence table under the parent's `thread_id`.
6. For a sync spawn with `forward_sync_subagent_events = true`, the subagent's `CodeAgent` steps appear in the parent SSE stream decorated with the `subagent` field; for an async spawn, only the bookend events appear.
7. Spawned agent LLM calls appear as child spans nested under the parent's Langfuse trace (verified by inspecting the Langfuse trace structure in an integration test using a mock `CallbackHandler`).
8. All spans from spawned agents carry the same `session.id` as the parent in OTEL traces, for both sync and async spawn modes.
9. At least one integration test covers the full path: parent → `spawn_agent` → subagent execution → `get_agent_result` → parent answer.
10. The example descriptor in `.agents/agents/data_analyst/AGENT.md` runs end-to-end in CI without a live LLM.
11. A subagent constructed with a `tool_definitions` entry calls its declared tool successfully and returns a result to the parent; the tool is verified absent from the parent's `tools_context`.
12. A subagent constructed with a `skill_tools` entry loads and calls a tool declared in the referenced `SKILL.md`; the skill tool is verified absent from the parent's `tools_context`.
13. A descriptor with an invalid `module.function` reference raises `ToolDefinitionError` at load time (not silently at spawn time).

---

