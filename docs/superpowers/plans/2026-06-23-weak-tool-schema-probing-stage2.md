# Weak Tool Output Schema Probing — Stage 2 (B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Structurally enforce the probing directive Stage 1 only recommends — when an LLM turn contains multiple fenced code blocks and an early one calls a tool that still needs probing, drop the later blocks instead of executing all of them combined, so the model is forced to see the tool's real result before it can write code that depends on it.

**Architecture:** A new optional parameter on the existing shared code-extraction function (`code_extraction.py`) does the actual truncation. A new `CoreGraphAdapter` hook (`graph_nodes.py`), defaulting to a no-op, lets `shared_nodes.py`'s `call_model` ask "which tools still need isolation this turn" without CugaSupervisor having to know anything changed. `AgentGraphAdapter` (CugaLite) implements the hook using the state Stage 1 already built (`_weak_schema_tool_names`, `_observed_tool_shapes`).

**Tech Stack:** Python 3.12, pytest, pytest-asyncio, LangGraph.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-23-weak-tool-schema-probing-design.md` (see "Deferred work (Stage 2)"). Branch: `feat/272-stage2-block-isolation`, branched off `feat/272-tool-output-schema-probing` (Stage 1, already merged into this branch's history).
- Every new parameter/hook defaults to empty (`frozenset()`). Behavior for any existing caller that doesn't pass `tools_needing_probing` — including `tool_approval_handler.py`'s call to `extract_and_combine_codeblocks` — must be byte-identical to today. Do not modify `tool_approval_handler.py`; its call site intentionally keeps the default (approval-resumption code re-derivation is out of scope for probing enforcement).
- CugaSupervisor must require zero changes — the new hook's default (`frozenset()`) on `CoreGraphAdapter` covers it.
- Match existing code style: this codebase favors regex-based lightweight parsing over AST for code-extraction (see `_recover_non_closing_python_fence` in `code_extraction.py`) — use the same approach for the new truncation logic, not an AST-based one.
- `code_extraction.py`, `graph_nodes.py`, and `shared_nodes.py` already have `from __future__ import annotations`, so bare generic type hints like `frozenset[str]` are safe to use without quoting.

## File Structure

- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/execution/code_extraction.py` — truncation logic.
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/graph/graph_nodes.py` — new `CoreGraphAdapter.get_tools_needing_probing()` hook.
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/graph/shared_nodes.py` — wire the hook into the code-extraction call.
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/graph_adapter.py` — `AgentGraphAdapter`'s override.
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/execution/test_code_extraction.py` (extend existing)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_graph_adapter_hooks.py` (extend existing)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_shared_call_model.py` (extend existing)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py` (extend existing)

---

### Task 1: Truncate multi-block responses around a probing-required tool call

**Files:**
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/execution/code_extraction.py:23-56` (the two functions; new helper inserted between them)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/execution/test_code_extraction.py` (extend)

**Interfaces:**
- Produces: `extract_and_combine_codeblocks(text: str, tools_needing_probing: frozenset[str] = frozenset()) -> str` (new optional 2nd param, default preserves old behavior exactly).
- Produces: `extract_code_from_model_response(content, reasoning_content, tools_needing_probing: frozenset[str] = frozenset()) -> str` (same).
- Both consumed by Task 3 (`shared_nodes.py`).

- [ ] **Step 1: Write the failing tests**

Add to the end of `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/execution/test_code_extraction.py` (the import block at the top already brings in `extract_and_combine_codeblocks` and `extract_code_from_model_response` — no import changes needed):

```python
def test_truncates_after_first_block_referencing_probing_tool():
    text = (
        "```python\nres = await file_readfile('x')\nprint(res)\n```\n"
        "```python\nres_2 = res[0]\nprint(res_2)\n```"
    )
    code = extract_and_combine_codeblocks(text, tools_needing_probing=frozenset({"file_readfile"}))
    assert code == "res = await file_readfile('x')\nprint(res)"


def test_keeps_all_blocks_when_no_probing_tool_referenced():
    text = (
        "```python\nres = await file_readfile('x')\nprint(res)\n```\n"
        "```python\nres_2 = res[0]\nprint(res_2)\n```"
    )
    code = extract_and_combine_codeblocks(text, tools_needing_probing=frozenset({"some_other_tool"}))
    assert code == "res = await file_readfile('x')\nprint(res)\n\nres_2 = res[0]\nprint(res_2)"


def test_default_tools_needing_probing_preserves_old_combine_behavior():
    text = "```python\na = 1\n```\n```python\nb = 2\n```"
    assert extract_and_combine_codeblocks(text) == "a = 1\n\nb = 2"


def test_truncation_keeps_prefix_blocks_before_the_matching_one():
    text = (
        "```python\nx = 1\nprint(x)\n```\n"
        "```python\nres = await file_readfile('x')\nprint(res)\n```\n"
        "```python\nres_2 = res[0]\n```"
    )
    code = extract_and_combine_codeblocks(text, tools_needing_probing=frozenset({"file_readfile"}))
    assert code == "x = 1\nprint(x)\n\nres = await file_readfile('x')\nprint(res)"


def test_truncation_is_word_boundary_safe():
    """A tool name that's a prefix of a longer identifier must not false-match."""
    text = "```python\nfile_readfile_v2('x')\n```\n```python\ny = 2\n```"
    code = extract_and_combine_codeblocks(text, tools_needing_probing=frozenset({"file_readfile"}))
    assert code == "file_readfile_v2('x')\n\ny = 2"


def test_extract_code_from_model_response_threads_tools_needing_probing_through():
    content = (
        "```python\nres = await file_readfile('x')\nprint(res)\n```\n```python\nres_2 = res[0]\n```"
    )
    code = extract_code_from_model_response(
        content, None, tools_needing_probing=frozenset({"file_readfile"})
    )
    assert code == "res = await file_readfile('x')\nprint(res)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/execution/test_code_extraction.py -v -k "truncat or probing"`
Expected: 5 of 6 fail with `TypeError: extract_and_combine_codeblocks() got an unexpected keyword argument 'tools_needing_probing'` (or the equivalent for `extract_code_from_model_response`) — every test that passes the new kwarg. `test_default_tools_needing_probing_preserves_old_combine_behavior` already passes — it calls the function with only the original `text` argument, which works under the current signature too; it's a regression guard for the no-arg case, not a red/green check.

- [ ] **Step 3: Implement**

In `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/execution/code_extraction.py`, replace the current `extract_and_combine_codeblocks` (lines 23-43):

```python
def extract_and_combine_codeblocks(text: str) -> str:
    """Extract all ```python codeblocks from text and combine them."""
    code_blocks = re.findall(BACKTICK_PATTERN, text, re.DOTALL)

    if code_blocks:
        return "\n\n".join(block.strip() for block in code_blocks)

    recovered = _recover_non_closing_python_fence(text)
    if recovered:
        return recovered

    stripped_text = text.strip()

    if "print(" not in stripped_text:
        return ""

    try:
        compile(stripped_text.replace("await ", ""), "<string>", "exec")
        return stripped_text
    except SyntaxError:
        return ""
```

with:

```python
def extract_and_combine_codeblocks(text: str, tools_needing_probing: frozenset[str] = frozenset()) -> str:
    """Extract all ```python codeblocks from text and combine them.

    When ``tools_needing_probing`` is non-empty, fenced blocks are scanned in
    order and only kept up to and including the first block whose code calls
    one of those tool names — the rest are dropped. This forces a fresh model
    turn (with the real tool result visible) before any later block runs,
    instead of running a blind guess in the same execution.
    """
    code_blocks = re.findall(BACKTICK_PATTERN, text, re.DOTALL)

    if code_blocks:
        blocks = [block.strip() for block in code_blocks]
        if tools_needing_probing:
            blocks = _truncate_after_first_probing_block(blocks, tools_needing_probing)
        return "\n\n".join(blocks)

    recovered = _recover_non_closing_python_fence(text)
    if recovered:
        return recovered

    stripped_text = text.strip()

    if "print(" not in stripped_text:
        return ""

    try:
        compile(stripped_text.replace("await ", ""), "<string>", "exec")
        return stripped_text
    except SyntaxError:
        return ""


def _truncate_after_first_probing_block(blocks: list[str], tools_needing_probing: frozenset[str]) -> list[str]:
    pattern = re.compile(r"\b(" + "|".join(re.escape(name) for name in tools_needing_probing) + r")\s*\(")
    for i, block in enumerate(blocks):
        if pattern.search(block):
            return blocks[: i + 1]
    return blocks
```

Then replace the current `extract_code_from_model_response` (lines 46-56):

```python
def extract_code_from_model_response(content: Optional[str], reasoning_content: Optional[str]) -> str:
    """Extract code from a model response, falling back to reasoning.

    Tries fenced/raw code in ``content`` first; only if that yields nothing
    does it look at ``reasoning_content``. Mirrors the (previously
    duplicated) logic in the Lite and Supervisor loop nodes.
    """
    code = extract_and_combine_codeblocks(content) if content else ""
    if not code and reasoning_content:
        code = extract_and_combine_codeblocks(reasoning_content)
    return code
```

with:

```python
def extract_code_from_model_response(
    content: Optional[str],
    reasoning_content: Optional[str],
    tools_needing_probing: frozenset[str] = frozenset(),
) -> str:
    """Extract code from a model response, falling back to reasoning.

    Tries fenced/raw code in ``content`` first; only if that yields nothing
    does it look at ``reasoning_content``. Mirrors the (previously
    duplicated) logic in the Lite and Supervisor loop nodes.
    """
    code = extract_and_combine_codeblocks(content, tools_needing_probing) if content else ""
    if not code and reasoning_content:
        code = extract_and_combine_codeblocks(reasoning_content, tools_needing_probing)
    return code
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/execution/test_code_extraction.py -v`
Expected: all passed (existing + 6 new)

Also run the sibling suite that exercises the same functions under different names, to confirm the default-argument change didn't regress it:

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/tests/test_extract_codeblocks.py -v`
Expected: all passed, unchanged

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/cuga_graph/nodes/cuga_agent_core/execution/code_extraction.py src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/execution/test_code_extraction.py
git commit -m "feat(272): truncate multi-block responses around a probing-required tool call"
```

---

### Task 2: `CoreGraphAdapter.get_tools_needing_probing()` hook

**Files:**
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/graph/graph_nodes.py:140-142` (insert between `normalize_response` and `on_response_processed`)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_graph_adapter_hooks.py` (extend)

**Interfaces:**
- Produces: `CoreGraphAdapter.get_tools_needing_probing(self) -> frozenset[str]` (default `frozenset()`) — consumed by Task 3 (`shared_nodes.py`) and overridden by Task 4 (`AgentGraphAdapter`).

- [ ] **Step 1: Write the failing test**

Add to `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_graph_adapter_hooks.py`, in the "1. Default hook values" section (after `test_default_prepare_system_content_returns_base_prompt`, before `test_default_normalize_response_extracts_content_and_reasoning`):

```python
def test_default_get_tools_needing_probing_returns_empty_frozenset():
    adapter = _MinimalAdapter()
    assert adapter.get_tools_needing_probing() == frozenset()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_graph_adapter_hooks.py -v -k get_tools_needing_probing`
Expected: FAIL — `AttributeError: '_MinimalAdapter' object has no attribute 'get_tools_needing_probing'`

- [ ] **Step 3: Implement**

In `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/graph/graph_nodes.py`, the current `normalize_response` method ends at line 140 (`return content, reasoning`) and `on_response_processed` starts at line 142. Insert between them:

```python

    def get_tools_needing_probing(self) -> frozenset[str]:
        """Tool names that must be called alone in their own code block this
        turn (no declared output schema, not yet observed this session).
        Default: empty (Supervisor and any adapter that hasn't opted in)."""
        return frozenset()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_graph_adapter_hooks.py -v`
Expected: all passed (existing + 1 new)

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/cuga_graph/nodes/cuga_agent_core/graph/graph_nodes.py src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_graph_adapter_hooks.py
git commit -m "feat(272): add CoreGraphAdapter.get_tools_needing_probing hook"
```

---

### Task 3: Wire the hook into `call_model`'s code extraction

**Files:**
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/graph/shared_nodes.py:185`
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_shared_call_model.py` (extend)

**Interfaces:**
- Consumes: `extract_code_from_model_response(..., tools_needing_probing=...)` (Task 1), `adapter.get_tools_needing_probing()` (Task 2).

- [ ] **Step 1: Write the failing tests**

Add to `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_shared_call_model.py`, after the `_TestAdapter` class definition (after line 41, before `_make_state`):

```python
class _ProbingAdapter(_TestAdapter):
    def get_tools_needing_probing(self) -> frozenset:
        return frozenset({"file_readfile"})
```

Then add these two tests at the end of the file (after `test_configurable_llm_overrides_base_model`):

```python
# ── 8. Multi-block response truncated when a probing tool is referenced ───


@pytest.mark.asyncio
@patch(
    "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
    new_callable=AsyncMock,
)
async def test_multi_block_response_truncated_when_probing_tool_referenced(mock_summarize):
    mock_summarize.side_effect = lambda messages, *args, **kwargs: messages

    adapter = _ProbingAdapter()
    state = _make_state()
    model = _mock_model(
        "```python\nres = await file_readfile('./x')\nprint(res)\n```\n"
        "```python\nres_2 = res[0][0:15]\nprint(res_2)\n```"
    )
    settings = _mock_settings()

    node = _get_factory()(adapter, model, settings)
    result = await node(state, config=None)

    assert result.update["script"] == "res = await file_readfile('./x')\nprint(res)"


@pytest.mark.asyncio
@patch(
    "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
    new_callable=AsyncMock,
)
async def test_multi_block_response_not_truncated_when_no_probing_tools(mock_summarize):
    """Regression guard: an adapter with no probing-required tools (the
    default, e.g. Supervisor or Lite before any weak-schema tool appears)
    must keep combining all blocks exactly as before this change."""
    mock_summarize.side_effect = lambda messages, *args, **kwargs: messages

    adapter = _TestAdapter()
    state = _make_state()
    model = _mock_model(
        "```python\nres = await file_readfile('./x')\nprint(res)\n```\n"
        "```python\nres_2 = res[0][0:15]\nprint(res_2)\n```"
    )
    settings = _mock_settings()

    node = _get_factory()(adapter, model, settings)
    result = await node(state, config=None)

    assert result.update["script"] == (
        "res = await file_readfile('./x')\nprint(res)\n\nres_2 = res[0][0:15]\nprint(res_2)"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_shared_call_model.py -v -k truncat`
Expected: `test_multi_block_response_truncated_when_probing_tool_referenced` FAILS (`assert "res = await file_readfile('./x')\nprint(res)\n\nres_2 = res[0][0:15]\nprint(res_2)" == "res = await file_readfile('./x')\nprint(res)"` — both blocks still combined, since nothing reads the hook yet). `test_multi_block_response_not_truncated_when_no_probing_tools` already passes (current behavior already combines everything; this test is a regression guard, not a red/green check).

- [ ] **Step 3: Implement**

In `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/graph/shared_nodes.py`, replace the current line 185:

```python
        code = extract_code_from_model_response(content, reasoning)
```

with:

```python
        code = extract_code_from_model_response(
            content, reasoning, tools_needing_probing=adapter.get_tools_needing_probing()
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_shared_call_model.py -v`
Expected: all passed (existing 7 + 2 new)

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/cuga_graph/nodes/cuga_agent_core/graph/shared_nodes.py src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_shared_call_model.py
git commit -m "feat(272): wire get_tools_needing_probing into call_model code extraction"
```

---

### Task 4: `AgentGraphAdapter.get_tools_needing_probing()` override

**Files:**
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/graph_adapter.py:109-111` (insert between `prepare_system_content` and `get_tracker`)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py` (extend)

**Interfaces:**
- Consumes: `self._weak_schema_tool_names`, `self._observed_tool_shapes` (both already exist on `AgentGraphAdapter`, added in Stage 1).
- Overrides: `CoreGraphAdapter.get_tools_needing_probing` (Task 2).

- [ ] **Step 1: Write the failing tests**

Add to the end of `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py`:

```python
def test_get_tools_needing_probing_returns_unobserved_weak_schema_tools():
    adapter = _make_adapter()
    adapter._weak_schema_tool_names = frozenset({"file_readfile", "get_browser_state"})
    adapter._observed_tool_shapes = {"file_readfile": "list of 3 items"}
    assert adapter.get_tools_needing_probing() == frozenset({"get_browser_state"})


def test_get_tools_needing_probing_empty_when_all_observed():
    adapter = _make_adapter()
    adapter._weak_schema_tool_names = frozenset({"file_readfile"})
    adapter._observed_tool_shapes = {"file_readfile": "list of 3 items"}
    assert adapter.get_tools_needing_probing() == frozenset()


def test_get_tools_needing_probing_empty_by_default():
    adapter = _make_adapter()
    assert adapter.get_tools_needing_probing() == frozenset()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py -v -k get_tools_needing_probing`
Expected: only `test_get_tools_needing_probing_returns_unobserved_weak_schema_tools` fails (`assert frozenset() == frozenset({'get_browser_state'})`) — `AgentGraphAdapter` already inherits `get_tools_needing_probing` from `CoreGraphAdapter` (Task 2, already on this branch), which unconditionally returns `frozenset()` regardless of `self`. The other two tests (`empty_when_all_observed`, `empty_by_default`) already pass — for those specific inputs the inherited stub's `frozenset()` happens to match the expected result too, so they're regression guards, not red/green checks; only the first test actually exercises the real set-difference logic.

- [ ] **Step 3: Implement**

In `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/graph_adapter.py`, the current `prepare_system_content` method ends at line 109 (`return content`) and `get_tracker` starts at line 111. Insert between them:

```python

    def get_tools_needing_probing(self) -> frozenset[str]:
        return self._weak_schema_tool_names - self._observed_tool_shapes.keys()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py -v`
Expected: all passed (existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/graph_adapter.py src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py
git commit -m "feat(272): implement get_tools_needing_probing for CugaLite"
```

---

### Task 5: Full-suite regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full CugaLite and cuga_agent_core test directories**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/ src/cuga/backend/cuga_graph/nodes/cuga_agent_core/ -q`
Expected: all passed except the 3 pre-existing, unrelated `test_e2b_lite.py` failures (`RuntimeError: e2b-code-interpreter package not installed`) — do not attempt to fix those.

- [ ] **Step 2: Run the CugaSupervisor test directory**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_supervisor/ -q`
Expected: all passed — this is the explicit check that the new hook's default didn't change Supervisor behavior at all.

- [ ] **Step 3: Run ruff on all touched files**

Run: `uv run ruff check --fix src/cuga/backend/cuga_graph/nodes/cuga_agent_core/execution/code_extraction.py src/cuga/backend/cuga_graph/nodes/cuga_agent_core/graph/graph_nodes.py src/cuga/backend/cuga_graph/nodes/cuga_agent_core/graph/shared_nodes.py src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/graph_adapter.py src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/execution/test_code_extraction.py src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_graph_adapter_hooks.py src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/graph/test_shared_call_model.py src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_agent_graph_adapter.py`
Expected: no remaining issues (auto-fixed or clean)

- [ ] **Step 4: Commit if ruff made changes**

```bash
git add -u
git commit -m "style(272): ruff fixes for Stage 2 block-isolation changes" --allow-empty
```
