---
title: "Weak tool output schema probing (issue #272, Stage 1)"
status: draft
issue: 272
date: 2026-06-23
---

# Weak tool output schema probing

## Problem

Issue #272: CugaLite's CodeAct loop assumes tools declare a clean output
schema so the model can plan over the result. When a tool has no real
output schema (raw text, loose `dict`/`Any`, no `responseSchema`), the
model mis-parses or over-shapes results — wrong key paths, unsafe
indexing, hallucinated structure. The Vakra(M3) eval traced this to
catastrophic thrash: a task expecting 2 tool calls burned 245–732 calls
before failing, because the model kept re-guessing the shape of the same
poorly-described tool response.

A prior fix (2026-06-03 bundle: tool-naming fix + a generic system-prompt
instruction telling the model to "probe tools with no declared output
schema") moved Vakra(M3) pass rate from 20.0% to 33.5% — a real
improvement, but still leaves most tasks failing. This spec covers a
more targeted Stage 1 fix. A second mechanism (structural enforcement,
"Stage 2") is named but deliberately deferred — see [Deferred work](#deferred-work-stage-2).

## Current-state audit (issue acceptance criterion #2)

This section captures what we found tracing the actual render path —
fulfilling the "validate schema presentation" audit called out in the
issue and the design deck (`docs/issues/272-tool-output-schema-design.md`,
solution #3).

**The tool-docs block is rendered once per session, not per turn.**
`PromptUtils.get_tool_docs` (`src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py:150-212`)
is called from `create_mcp_prompt`, which is called from
`prepare_tools_and_apps` (`.../adapter/prepare_node.py:54-660`), the
LangGraph node with the *only* inbound edge from `START`
(`shared_graph.py:56`). There is no edge back into `prepare`. The
resulting prompt is cached as `state.prepared_prompt` and read verbatim
by `call_model` on every turn (`shared_nodes.py:72`). Mutating a tool's
schema attribute mid-session does **not** change what the model sees —
the tool-docs text was already baked into the cached prompt.

The one mechanism that *does* run before every `call_model` invocation
is `adapter.prepare_system_content(state, configurable, base_prompt)`
(`shared_nodes.py:73`). Today `AgentGraphAdapter.prepare_system_content`
(`graph_adapter.py:91-97`) uses this to append a "current plan" block
built from a mutable, closure-scoped ref (`self._task_todos_ref`) that
gets written to elsewhere in the session. This is the existing seam this
spec reuses for "updating" schema knowledge mid-session.

**"No output schema" is actually two distinct runtime shapes**, not one:

- OpenAPI-derived tools with nothing in `responses.200/201.content.application/json.schema`
  get a **literally empty** `response_schemas == {}`
  (`tools_env/registry/mcp_manager/response_schema.py:177-249`).
- MCP tools with no `outputSchema` on the tool descriptor get a
  **generic synthetic fallback already injected today**:
  `{"success": {"type": "string"}, "failure": {"type": "string"}}`
  (`tools_env/registry/mcp_manager/mcp_manager.py:643-647`). This is
  *truthy* and passes `get_tool_docs`'s existing
  `if response_schemas and 'success' in response_schemas` check
  (`prompt_utils.py:171`), so today it renders a "Response Schema:
  `{"type": "string"}`" block that looks present but conveys nothing
  useful. `file_readfile` and `get_browser_state` — the two tools named
  in the issue's failure trace — are both in this bucket.

Both shapes need to be treated as "weak schema" for this fix to cover
the tools the issue actually names.

## Goal

When a tool has no real declared output schema, give the model a
**targeted, per-tool** instruction to probe it in isolation, and once
that probe happens, **surface the real observed shape** back into the
prompt for the rest of the session — so the model stops re-guessing the
same tool's shape every time it's called.

Scope: CugaLite only. No changes to CugaSupervisor, execution modes, or
native function calling (explicitly out of scope per the issue text).

## Design

Components are labeled A/B/C to match the candidate solutions explored
while writing this spec. **B (structural enforcement of the block
split) is deferred — see [Deferred work](#deferred-work-stage-2)** — so
Stage 1 below covers only A and C.

### A — Weak-schema detection + per-tool probing directive

New helper, `is_weak_schema_tool(tool) -> bool` in `prompt_utils.py`,
true when `tool.func._response_schemas` is empty/missing **or** its
`success` entry is exactly the generic MCP placeholder
`{"type": "string"}`.

In `get_tool_docs` (`prompt_utils.py:150-212`), when
`is_weak_schema_tool(tool)` is true, `response_doc` becomes a directive
instead of the existing (empty or placeholder) schema text:

> ⚠️ No declared output schema for this tool. Call it **alone** in its
> own ```python block and `print()` the raw result — don't write code in
> the same block that indexes, slices, or assumes its shape. Write
> follow-up code using the real shape once you see it.

This is a recommendation, not an enforced rule — nothing in this Stage 1
slice stops the runner from combining multiple fenced blocks in one
turn (see [Deferred work](#deferred-work-stage-2)). The copy must not
imply enforcement that doesn't exist.

In `prepare_tools_and_apps` (`prepare_node.py`), immediately before the
existing `create_mcp_prompt(tools_for_prompt, ...)` call (~line 603),
compute once:

```python
adapter._weak_schema_tool_names = frozenset(
    t.name for t in tools_for_prompt if is_weak_schema_tool(t)
)
```

### C — Post-probe session enrichment

`AgentGraphAdapter.__init__` (`graph_adapter.py:44-71`) gains two plain
instance attributes, initialized to empty defaults — no constructor
signature change, no graph-construction-site changes:

```python
self._weak_schema_tool_names: frozenset[str] = frozenset()
self._observed_tool_shapes: dict[str, str] = {}
```

In `sandbox_node.py`, after a script executes, for each call recorded by
`ToolCallTracker` (`tracker.py:69-107`) whose tool name is in
`adapter._weak_schema_tool_names` and not yet a key in
`adapter._observed_tool_shapes`, derive a short shape description from
the call's `result` and store it (first observation wins, per session
only — lost when the session/process ends, same lifetime as
`_tools_context`):

- `dict` → top-level keys (truncated if many)
- `list`/`tuple` → length + type of first element
- `str` → length + a short excerpt
- anything else → `type(result).__name__`

Extend `AgentGraphAdapter.prepare_system_content`
(`graph_adapter.py:91-97`) to append, after the existing todos block, one
line per tool with a stored shape:

> **Observed output (this session) — `file_readfile`:** list[str], 15
> items, e.g. `"Total Wins,Team,..."`. Use this shape directly — no need
> to probe again.

This runs every turn (`shared_nodes.py:73`), so it reaches the model on
the very next turn after a probe — without needing to re-render the
one-shot tool-docs block.

### Files touched (Stage 1)

- `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py` — `is_weak_schema_tool`, `get_tool_docs` directive branch.
- `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/prepare_node.py` — compute `_weak_schema_tool_names`.
- `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/sandbox_node.py` — capture `_observed_tool_shapes`.
- `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/graph_adapter.py` — init new attributes, extend `prepare_system_content`.

No changes to `shared_nodes.py`, `code_extraction.py`, or `graph_nodes.py`
(`CoreGraphAdapter` ABC) — those are exactly the files Stage 2 would
touch, deferred below.

## Testing

- `is_weak_schema_tool`: true for empty `{}`, true for the
  `{"type": "string"}` placeholder, false for a real declared schema.
- `get_tool_docs`: renders the probing directive (not the placeholder
  schema text) for a weak-schema tool; unchanged for a tool with a real
  schema.
- `sandbox_node`: a tool call result for a weak-schema tool populates
  `_observed_tool_shapes` exactly once, even across repeated calls to the
  same tool in the same session; a call to a non-weak-schema tool never
  populates it.
- `prepare_system_content`: includes the observed-shape note once
  populated, omits it before any probe has happened, and the existing
  todos-block behavior is unchanged.

## Deferred work (Stage 2)

**Structural enforcement of the block split.** Today,
`extract_and_combine_codeblocks` (`code_extraction.py:23-43`) joins
*every* fenced python block in one LLM turn with `\n\n` and executes them
as a single script — this is the literal mechanism behind the issue's
motivating trace (a probe call and a slicing call written in the same
completion, then run together before the model ever saw the probe's real
result). Component A's directive cannot make the model comply; nothing
in Stage 1 stops the runner from combining blocks regardless of what the
prompt asks for.

The deferred design: give `extract_and_combine_codeblocks` /
`extract_code_from_model_response` an optional
`tools_needing_probing: frozenset[str]` parameter (default empty —
zero behavior change for any existing caller); scan fenced blocks in
order and keep only up to and including the first block that
regex-matches a name in that set, silently dropping the rest. Wire it
through a new `CoreGraphAdapter.get_tools_needing_probing()` hook
(default `frozenset()`, so CugaSupervisor is unaffected) computed as
`adapter._weak_schema_tool_names - adapter._observed_tool_shapes.keys()`
— once a tool has been observed, it drops out of the isolation
requirement automatically, so the round-trip cost is paid at most once
per weak-schema tool per session.

**Trigger to revisit:** re-run the Vakra(M3) eval after Stage 1 ships.
If pass rate and mean tool-calls-per-task on weak-schema tasks are still
materially worse than tasks with well-described tools, build Stage 2 as
a fast-follow. If Stage 1 alone closes most of the gap, Stage 2 may not
be worth its larger surface area (it touches `shared_nodes.py`, shared
with CugaSupervisor, and the core `code_extraction.py` function).

**Also explicitly deferred, not part of Stage 1 or the named Stage 2:**
tool guardrails / payload capping (solution #1 in the design deck).
Stage 1's probing directive asks the model to `print()` a weak-schema
tool's raw result to observe it, with no size cap — an oversized raw
payload (e.g. a large browser-state snapshot) can still blow up context
on a single probe call. This is a known residual risk on the token/time
axis, not addressed by this spec.
