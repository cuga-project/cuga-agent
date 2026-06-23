# Weak Tool Output Schema Probing — Stage 1 (A+C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give CugaLite a per-tool directive that tells the model to probe weak-schema tools in isolation, and surface the real observed output shape back into the prompt for the rest of the session once a probe happens.

**Architecture:** Two independent, additive pieces wired through existing seams — no new abstractions, no shared-loop changes. (A) `PromptUtils.get_tool_docs` renders a probing directive instead of an empty/placeholder schema block when a tool has no real declared output schema. (C) `sandbox_node.py` captures the real shape of a weak-schema tool's first successful result into a per-session dict on the adapter; `AgentGraphAdapter.prepare_system_content` (already called before every `call_model` turn) appends it as a small note once populated.

**Tech Stack:** Python 3.12, pytest, pytest-asyncio, LangGraph, LangChain `StructuredTool`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-23-weak-tool-schema-probing-design.md`. This plan covers Stage 1 (components A and C) only.
- CugaLite only. Do **not** touch `shared_nodes.py`, `code_extraction.py`, or `graph_nodes.py` (`CoreGraphAdapter` ABC) — those belong to Stage 2 (component B), planned and implemented separately on its own branch.
- Every new piece of state defaults to empty/falsy. Behavior for tools with a real declared output schema must be byte-identical to today — verify this with an explicit regression test in Task 1.
- Match existing code style: plain `hasattr`/`getattr` duck-typing (no new Protocols/ABCs), `SimpleNamespace`-based test fixtures (matches `test_agent_graph_adapter.py` and `test_prepare_node_task_todos_reset.py`), no docstring bloat.
- Run the full suite for any file you touch before committing it: `uv run pytest <test file> -v`.

## File Structure

- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py` — weak-schema detection + probing directive text.
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/prepare_node.py` — compute the weak-schema tool-name set once per session.
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/graph_adapter.py` — new per-session state + per-turn enrichment note.
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/sandbox_node.py` — capture the first observed shape per weak-schema tool.
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prompt_utils_weak_schema.py` (new)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prepare_node_weak_schema_tools.py` (new)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_sandbox_node_weak_schema_shapes.py` (new)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py` (extend existing `prepare_system_content` section)

---

### Task 1: Weak-schema detection + probing directive in `get_tool_docs`

**Files:**
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py:16-17` (new module constant), `:149-150` (new method), `:163-174` (directive branch)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prompt_utils_weak_schema.py`

**Interfaces:**
- Produces: `PromptUtils.is_weak_schema_tool(tool: StructuredTool) -> bool` (static method) — used by Task 2.
- Produces: module constant `_WEAK_SCHEMA_PROBE_DIRECTIVE: str` in `prompt_utils.py` (not exported elsewhere).

- [ ] **Step 1: Write the failing tests**

Create `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prompt_utils_weak_schema.py`:

```python
"""Weak-schema detection and probing directive in PromptUtils.get_tool_docs (issue #272)."""

from __future__ import annotations

from types import SimpleNamespace

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils


def _make_tool(response_schemas=None, has_response_schemas_attr=True):
    func = SimpleNamespace(_response_schemas=response_schemas) if has_response_schemas_attr else object()
    return SimpleNamespace(name="some_tool", func=func, args_schema=None)


def test_is_weak_schema_tool_true_when_empty_dict():
    tool = _make_tool(response_schemas={})
    assert PromptUtils.is_weak_schema_tool(tool) is True


def test_is_weak_schema_tool_true_when_attr_missing():
    tool = _make_tool(has_response_schemas_attr=False)
    assert PromptUtils.is_weak_schema_tool(tool) is True


def test_is_weak_schema_tool_true_when_generic_mcp_placeholder():
    tool = _make_tool(response_schemas={"success": {"type": "string"}, "failure": {"type": "string"}})
    assert PromptUtils.is_weak_schema_tool(tool) is True


def test_is_weak_schema_tool_false_when_real_schema_declared():
    tool = _make_tool(
        response_schemas={"success": {"type": "object", "properties": {"id": {"type": "integer"}}}}
    )
    assert PromptUtils.is_weak_schema_tool(tool) is False


def test_get_tool_docs_renders_probing_directive_for_weak_schema_tool():
    tool = _make_tool(response_schemas={})
    _params_doc, response_doc = PromptUtils.get_tool_docs(tool)
    assert "No declared output schema" in response_doc
    assert "ALONE" in response_doc


def test_get_tool_docs_renders_probing_directive_for_mcp_placeholder_schema():
    tool = _make_tool(response_schemas={"success": {"type": "string"}, "failure": {"type": "string"}})
    _params_doc, response_doc = PromptUtils.get_tool_docs(tool)
    assert "No declared output schema" in response_doc


def test_get_tool_docs_renders_real_schema_for_known_schema_tool():
    """Regression: tools with a real schema must render exactly as before."""
    tool = _make_tool(
        response_schemas={"success": {"type": "object", "properties": {"id": {"type": "integer"}}}}
    )
    _params_doc, response_doc = PromptUtils.get_tool_docs(tool)
    assert "Returns (on success) - Response Schema" in response_doc
    assert "No declared output schema" not in response_doc
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prompt_utils_weak_schema.py -v`
Expected: 6 failed, 1 passed. The 4 `is_weak_schema_tool` tests fail with `AttributeError: type object 'PromptUtils' has no attribute 'is_weak_schema_tool'`. The 2 `get_tool_docs` probing-directive tests fail with a plain `assert` failure (old code doesn't render the directive yet). `test_get_tool_docs_renders_real_schema_for_known_schema_tool` already passes — it's a regression guard for existing behavior, not new behavior; it stays green through Step 4 too.

- [ ] **Step 3: Implement**

In `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py`, after the existing imports (current lines 1-16, ending `from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import runtime_defaults_for_model`), add the module constant:

```python
_WEAK_SCHEMA_PROBE_DIRECTIVE = (
    "\n    \n    ⚠️ No declared output schema for this tool. Call it ALONE in its own "
    "```python block and print() the raw result — don't write code in the same block "
    "that indexes, slices, or assumes its shape. Write follow-up code using the real "
    "shape once you see it on your next turn."
)
```

In the `PromptUtils` class, immediately before the existing `get_tool_docs` method (currently starting at line 150 with `@staticmethod` / line 151 `def get_tool_docs(...)`), add:

```python
    @staticmethod
    def is_weak_schema_tool(tool: StructuredTool) -> bool:
        """True when a tool has no real declared output schema.

        Covers both the OpenAPI-derived case (empty ``response_schemas``) and
        the MCP fallback case, where ``response_schemas`` is present but its
        ``success`` entry is the generic synthetic placeholder MCP tools get
        when they declare no ``outputSchema`` (see mcp_manager.py).
        """
        response_schemas = {}
        if hasattr(tool, 'func') and hasattr(tool.func, '_response_schemas'):
            response_schemas = tool.func._response_schemas

        if not response_schemas or not isinstance(response_schemas, dict):
            return True

        return response_schemas.get('success') == {'type': 'string'}

```

Then replace the existing schema-rendering block (current lines 171-174):

```python
        if response_schemas and isinstance(response_schemas, dict):
            if 'success' in response_schemas:
                success_schema = json.dumps(response_schemas['success'], indent=4)
                response_doc = f"\n    \n    Returns (on success) - Response Schema:\n{success_schema}"
```

with:

```python
        if PromptUtils.is_weak_schema_tool(tool):
            response_doc = _WEAK_SCHEMA_PROBE_DIRECTIVE
        elif response_schemas and isinstance(response_schemas, dict) and 'success' in response_schemas:
            success_schema = json.dumps(response_schemas['success'], indent=4)
            response_doc = f"\n    \n    Returns (on success) - Response Schema:\n{success_schema}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prompt_utils_weak_schema.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prompt_utils_weak_schema.py
git commit -m "feat(272): detect weak-schema tools and inject a probing directive"
```

---

### Task 2: Compute the weak-schema tool-name set once per session

**Files:**
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/prepare_node.py:36-41` (import), `:594-599` (computation)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prepare_node_weak_schema_tools.py`

**Interfaces:**
- Consumes: `PromptUtils.is_weak_schema_tool(tool) -> bool` (Task 1).
- Produces: `adapter._weak_schema_tool_names: frozenset[str]`, set once per `prepare_tools_and_apps` run — consumed by Task 4 (`sandbox_node.py`).

- [ ] **Step 1: Write the failing test**

Create `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prepare_node_weak_schema_tools.py`:

```python
"""prepare_tools_and_apps computes adapter._weak_schema_tool_names (issue #272)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage


def _make_fake_tool(name: str, response_schemas: dict):
    func = SimpleNamespace(_response_schemas=response_schemas)
    return SimpleNamespace(name=name, func=func, description="d", args_schema=None)


def _build_mock_adapter(tools: list) -> MagicMock:
    adapter = MagicMock()
    adapter._task_todos_ref = []
    adapter._tools_context = {}
    adapter._instructions = ""
    adapter._special_instructions = None
    adapter._static_prompt = None
    adapter._thread_id = "test-thread"
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
        thread_id="test-thread",
    )


@pytest.mark.asyncio
async def test_weak_schema_tool_names_populated_from_tools_for_prompt():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )

    weak_tool = _make_fake_tool("file_readfile", {})
    placeholder_tool = _make_fake_tool("get_browser_state", {"success": {"type": "string"}})
    known_tool = _make_fake_tool("get_weather", {"success": {"type": "object"}})
    adapter = _build_mock_adapter([weak_tool, placeholder_tool, known_tool])
    state = _make_state()

    configurable = {"enable_todos": False, "shortlisting_tool_threshold": 35}
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled",
        new=False,
    ):
        node = create_prepare_tools_and_apps_node(adapter, lc_bind_tools_meta={})
        await node(state, config={"configurable": configurable})

    assert adapter._weak_schema_tool_names == frozenset({"file_readfile", "get_browser_state"})


@pytest.mark.asyncio
async def test_weak_schema_tool_names_empty_when_all_tools_have_real_schemas():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )

    known_tool = _make_fake_tool("get_weather", {"success": {"type": "object"}})
    adapter = _build_mock_adapter([known_tool])
    state = _make_state()

    configurable = {"enable_todos": False, "shortlisting_tool_threshold": 35}
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled",
        new=False,
    ):
        node = create_prepare_tools_and_apps_node(adapter, lc_bind_tools_meta={})
        await node(state, config={"configurable": configurable})

    assert adapter._weak_schema_tool_names == frozenset()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prepare_node_weak_schema_tools.py -v`
Expected: 1 failed, 1 passed. `test_weak_schema_tool_names_populated_from_tools_for_prompt` fails (`assert frozenset() == frozenset({'file_readfile', 'get_browser_state'})`, since nothing sets the attribute yet). `test_weak_schema_tool_names_empty_when_all_tools_have_real_schemas` already passes — both sides are `frozenset()` before implementation too; it's a regression guard for the all-real-schemas case, not a red/green check.

- [ ] **Step 3: Implement**

In `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/prepare_node.py`, add `PromptUtils` to the existing import (current lines 36-41):

```python
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
    PromptUtils,
    create_mcp_prompt,
    format_apps_for_prompt,
    normalize_mcp_few_shot_examples,
    resolve_cuga_lite_few_shots_enabled,
)
```

Then, right before the `# Create prompt dynamically` comment (current line 599), insert:

```python
        adapter._weak_schema_tool_names = frozenset(
            t.name
            for t in (tools_for_prompt or [])
            if getattr(t, "name", None) and PromptUtils.is_weak_schema_tool(t)
        )

```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prepare_node_weak_schema_tools.py -v`
Expected: 2 passed

Also re-run the pre-existing regression suite for this file to confirm nothing broke:

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prepare_node_task_todos_reset.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/prepare_node.py src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prepare_node_weak_schema_tools.py
git commit -m "feat(272): compute weak-schema tool-name set in prepare_tools_and_apps"
```

---

### Task 3: Per-session observed-shape state + per-turn enrichment note

**Files:**
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/graph_adapter.py:33-34` (new module function, placed after imports), `:69-71` (new instance attributes), `:91-97` (`prepare_system_content`)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py` (extend)

**Interfaces:**
- Produces: `AgentGraphAdapter._weak_schema_tool_names: frozenset[str]` (default `frozenset()`), `AgentGraphAdapter._observed_tool_shapes: dict[str, str]` (default `{}`) — both consumed by Task 4 (`sandbox_node.py`).
- Produces: module-level `_format_observed_tool_shapes_block(shapes: dict[str, str]) -> str` in `graph_adapter.py`.

- [ ] **Step 1: Write the failing tests**

Add to the end of `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py` (after the existing `# ── 9. prepare_system_content hook` section, before `test_resolve_max_steps_uses_override_when_given`):

```python
def test_prepare_system_content_appends_observed_tool_shapes_when_present():
    adapter = _make_adapter(task_todos_ref=[])
    adapter._observed_tool_shapes = {"file_readfile": "list of 3 items"}
    state = SimpleNamespace(task_todos=None)
    result = adapter.prepare_system_content(state, {}, "You are an agent.")
    assert result.startswith("You are an agent.")
    assert "file_readfile" in result
    assert "list of 3 items" in result


def test_prepare_system_content_omits_observed_shapes_block_when_empty():
    adapter = _make_adapter(task_todos_ref=[])
    state = SimpleNamespace(task_todos=None)
    result = adapter.prepare_system_content(state, {}, "You are an agent.")
    assert result == "You are an agent."


def test_prepare_system_content_combines_todos_and_observed_shapes():
    todos = [{"title": "Step 1", "status": "pending"}]
    adapter = _make_adapter(task_todos_ref=todos)
    adapter._observed_tool_shapes = {"file_readfile": "list of 3 items"}
    state = SimpleNamespace(task_todos=None)
    result = adapter.prepare_system_content(state, {}, "You are an agent.")
    assert "file_readfile" in result
    assert "list of 3 items" in result
    assert result.startswith("You are an agent.")


def test_new_adapter_has_empty_weak_schema_state_by_default():
    adapter = _make_adapter()
    assert adapter._weak_schema_tool_names == frozenset()
    assert adapter._observed_tool_shapes == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py -v -k "observed_tool_shapes or weak_schema_state"`
Expected: all 4 fail. `test_new_adapter_has_empty_weak_schema_state_by_default` fails with `AttributeError: 'AgentGraphAdapter' object has no attribute '_weak_schema_tool_names'` (nothing sets it in `__init__` yet). The other three fail with a plain `assert` failure — ad-hoc-assigning `adapter._observed_tool_shapes = {...}` in the test works fine (Python allows arbitrary attribute assignment), but the current `prepare_system_content` doesn't read it back yet, so the expected text never appears in `result`.

- [ ] **Step 3: Implement**

In `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/graph_adapter.py`, after the existing imports (current line 33 `from cuga.config import settings`), before `class AgentGraphAdapter(CoreGraphAdapter):`, add:

```python
def _format_observed_tool_shapes_block(shapes: Dict[str, str]) -> str:
    lines = ["", "---", "", "## Observed tool output shapes (this session)", ""]
    for name, description in shapes.items():
        lines.append(f"- `{name}`: {description}. Use this shape directly — no need to probe again.")
    return "\n".join(lines) + "\n"

```

In `__init__`, after the existing `self._thread_id = thread_id` line (current line 71), add:

```python
        self._weak_schema_tool_names: frozenset = frozenset()
        self._observed_tool_shapes: Dict[str, str] = {}
```

Replace the existing `prepare_system_content` method (current lines 91-97):

```python
    def prepare_system_content(self, state: Any, configurable: dict, base_prompt: str) -> str:
        if self._task_todos_ref:
            return base_prompt + format_task_todos_system_block(self._task_todos_ref)
        task_todos = getattr(state, "task_todos", None)
        if task_todos:
            return base_prompt + format_current_plan_section(task_todos)
        return base_prompt
```

with:

```python
    def prepare_system_content(self, state: Any, configurable: dict, base_prompt: str) -> str:
        if self._task_todos_ref:
            content = base_prompt + format_task_todos_system_block(self._task_todos_ref)
        else:
            task_todos = getattr(state, "task_todos", None)
            content = base_prompt + format_current_plan_section(task_todos) if task_todos else base_prompt

        if self._observed_tool_shapes:
            content += _format_observed_tool_shapes_block(self._observed_tool_shapes)
        return content
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py -v`
Expected: all passed (existing + 4 new)

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/graph_adapter.py src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py
git commit -m "feat(272): add per-session observed-tool-shape state and prompt enrichment"
```

---

### Task 4: Capture the first observed shape per weak-schema tool in the sandbox

**Files:**
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/sandbox_node.py:30-33` (new helpers, placed after `_llm_manager = LLMManager()`), `:181` (success path), `:212` (exception path)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_sandbox_node_weak_schema_shapes.py`

**Interfaces:**
- Consumes: `adapter._weak_schema_tool_names` and `adapter._observed_tool_shapes` (Task 3).
- Produces: `_describe_observed_shape(result: Any) -> str` and `_record_weak_schema_shapes(adapter: Any, tool_calls: list) -> None` (both private to `sandbox_node.py`).

- [ ] **Step 1: Write the failing tests**

Create `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_sandbox_node_weak_schema_shapes.py`:

```python
"""Observed-shape capture for weak-schema tools in the sandbox node (issue #272)."""

from __future__ import annotations

from types import SimpleNamespace

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import (
    _describe_observed_shape,
    _record_weak_schema_shapes,
)


def test_describe_observed_shape_dict():
    assert "dict with keys" in _describe_observed_shape({"a": 1, "b": 2})


def test_describe_observed_shape_list():
    assert "list of 3 items" in _describe_observed_shape(["x", "y", "z"])


def test_describe_observed_shape_empty_list():
    assert _describe_observed_shape([]) == "empty list"


def test_describe_observed_shape_str():
    assert "str of 11 chars" in _describe_observed_shape("hello world")


def test_describe_observed_shape_other_type():
    assert _describe_observed_shape(42) == "int"


def test_record_weak_schema_shapes_stores_first_observation():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset({"file_readfile"}), _observed_tool_shapes={})
    _record_weak_schema_shapes(adapter, [{"name": "file_readfile", "result": ["a", "b"], "error": None}])
    assert "file_readfile" in adapter._observed_tool_shapes


def test_record_weak_schema_shapes_skips_non_weak_tools():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset({"file_readfile"}), _observed_tool_shapes={})
    _record_weak_schema_shapes(adapter, [{"name": "other_tool", "result": "x", "error": None}])
    assert adapter._observed_tool_shapes == {}


def test_record_weak_schema_shapes_first_observation_wins():
    adapter = SimpleNamespace(
        _weak_schema_tool_names=frozenset({"file_readfile"}),
        _observed_tool_shapes={"file_readfile": "old"},
    )
    _record_weak_schema_shapes(adapter, [{"name": "file_readfile", "result": ["z"], "error": None}])
    assert adapter._observed_tool_shapes["file_readfile"] == "old"


def test_record_weak_schema_shapes_skips_errored_calls():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset({"file_readfile"}), _observed_tool_shapes={})
    _record_weak_schema_shapes(adapter, [{"name": "file_readfile", "result": None, "error": "boom"}])
    assert adapter._observed_tool_shapes == {}


def test_record_weak_schema_shapes_noop_when_no_weak_schema_tools():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset(), _observed_tool_shapes={})
    _record_weak_schema_shapes(adapter, [{"name": "file_readfile", "result": ["a"], "error": None}])
    assert adapter._observed_tool_shapes == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_sandbox_node_weak_schema_shapes.py -v`
Expected: FAIL — `ImportError: cannot import name '_describe_observed_shape' from 'cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node'`

- [ ] **Step 3: Implement**

In `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/sandbox_node.py`, after the existing `_llm_manager = LLMManager()` line (current line 30), add:

```python
def _describe_observed_shape(result: Any) -> str:
    """Render a short, human-readable description of an observed tool result."""
    if isinstance(result, dict):
        keys = list(result.keys())[:8]
        suffix = ", ..." if len(result) > len(keys) else ""
        return f"dict with keys [{', '.join(repr(k) for k in keys)}{suffix}]"
    if isinstance(result, (list, tuple)):
        kind = type(result).__name__
        if result:
            return (
                f"{kind} of {len(result)} items, e.g. first item: "
                f"{type(result[0]).__name__} {str(result[0])[:120]!r}"
            )
        return f"empty {kind}"
    if isinstance(result, str):
        return f"str of {len(result)} chars, e.g. {result[:120]!r}"
    return type(result).__name__


def _record_weak_schema_shapes(adapter: Any, tool_calls: list) -> None:
    """Stash the first observed output shape for any weak-schema tool this session."""
    weak_schema_tool_names = getattr(adapter, "_weak_schema_tool_names", frozenset())
    if not weak_schema_tool_names:
        return
    observed = adapter._observed_tool_shapes
    for call in tool_calls:
        name = call.get("name")
        if name not in weak_schema_tool_names or name in observed or call.get("error"):
            continue
        observed[name] = _describe_observed_shape(call.get("result"))

```

Then, in the success path, replace the current line 181:

```python
            execution_tool_calls = ToolCallTracker.stop_tracking()
```

with:

```python
            execution_tool_calls = ToolCallTracker.stop_tracking()
            _record_weak_schema_shapes(adapter, execution_tool_calls)
```

And in the exception path, replace the current line 212 (identical text, second occurrence):

```python
            execution_tool_calls = ToolCallTracker.stop_tracking()
```

with:

```python
            execution_tool_calls = ToolCallTracker.stop_tracking()
            _record_weak_schema_shapes(adapter, execution_tool_calls)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_sandbox_node_weak_schema_shapes.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/sandbox_node.py src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_sandbox_node_weak_schema_shapes.py
git commit -m "feat(272): capture first observed output shape for weak-schema tool calls"
```

---

### Task 5: Full-suite regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full CugaLite test directory**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/ -v`
Expected: all passed, no new failures relative to the pre-Task-1 baseline.

- [ ] **Step 2: Run ruff on all touched files**

Run: `uv run ruff check --fix src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/prepare_node.py src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/graph_adapter.py src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/sandbox_node.py src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prompt_utils_weak_schema.py src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prepare_node_weak_schema_tools.py src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_sandbox_node_weak_schema_shapes.py src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py`
Expected: no remaining issues (auto-fixed or clean)

- [ ] **Step 3: Commit if ruff made changes**

```bash
git add -u
git commit -m "style(272): ruff fixes for weak-schema probing changes" --allow-empty
```
