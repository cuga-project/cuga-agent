# Find-tools schema trim (#641) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `find_tools` discovery markdown from duplicating flat schemas, while still emitting Input Schema JSON when Parameters would lose nested/rich detail, and never dropping Description or weak-schema probe text.

**Architecture:** Render-time gates only. Keep building `Tool` with full `input_` / `output_schema` / docs. Add pure helpers `input_schema_adds_detail` and `should_emit_output_schema`, extract `_render_find_tools_markdown` for unit testing without the shortlister LLM, and gate Input/Output Schema blocks in that renderer.

**Tech Stack:** Python 3.12, pytest (`@pytest.mark.unit`), existing `prompt_utils.py` / `Tool` model, `uv run pytest`.

## Global Constraints

- No shortlister payload changes (`_build_shortlister_payload` still includes full `args_schema` / `_response_schemas` / `_param_constraints`).
- Do not enrich/rewrite Parameters; do not trim Description; do not change weak-schema probing in `get_tool_docs`.
- Prefer Parameters; emit Input Schema only when `input_schema_adds_detail` is true.
- Drop Output Schema when `response_doc` is non-empty.
- Every new/changed test marked `@pytest.mark.unit`.
- Conventional Commits with `-s` (DCO); use `uv` for pytest/ruff.

## File map

| File | Responsibility |
|---|---|
| `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py` | Helpers + `_render_find_tools_markdown` + wire `find_tools` through it |
| `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py` | Unit matrix for helpers + markdown render (new) |
| `docs/superpowers/specs/2026-08-12-find-tools-schema-trim-design.md` | Spec (already committed; do not rewrite unless gaps found) |

---

### Task 1: `input_schema_adds_detail` helper (TDD)

**Files:**
- Create: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py`
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py` (module-level helpers near top, after imports / constants)
- Test: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `input_schema_adds_detail(schema: Any) -> bool`

- [ ] **Step 1: Write failing tests for trim (False) and keep (True) cases**

Create `test_find_tools_schema_trim.py`:

```python
"""find_tools discovery markdown schema trim (#641)."""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import input_schema_adds_detail


@pytest.mark.unit
@pytest.mark.parametrize(
    "schema",
    [
        {},
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "n", "title": "Name"},
                "count": {"type": "integer", "default": 1},
                "ok": {"type": "boolean"},
                "score": {"type": "number"},
            },
            "required": ["name"],
        },
        {
            "type": "object",
            "properties": {
                "maybe": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
        },
        {
            "type": "object",
            "properties": {
                "maybe": {"type": ["string", "null"]},
            },
        },
        {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
    ],
    ids=["empty", "flat-primitives", "optional-anyOf-null", "optional-type-list-null", "array-of-primitives"],
)
def test_input_schema_adds_detail_false_for_lossy_safe_schemas(schema):
    assert input_schema_adds_detail(schema) is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "schema",
    [
        {
            "type": "object",
            "$defs": {
                "Address": {
                    "type": "object",
                    "properties": {"street": {"type": "string"}, "zip": {"type": "string"}},
                    "required": ["street", "zip"],
                }
            },
            "properties": {
                "address": {"anyOf": [{"$ref": "#/$defs/Address"}, {"type": "null"}]},
            },
        },
        {
            "type": "object",
            "properties": {
                "address": {
                    "type": "object",
                    "properties": {"street": {"type": "string"}},
                },
            },
        },
        {
            "type": "object",
            "properties": {
                "role": {"type": "string", "enum": ["admin", "user"]},
            },
        },
        {
            "type": "object",
            "properties": {
                "zip": {"type": "string", "pattern": r"^\d{5}$"},
            },
        },
        {
            "type": "object",
            "properties": {
                "email": {"type": "string", "format": "email"},
            },
        },
        {
            "type": "object",
            "properties": {
                "age": {"type": "integer", "minimum": 0, "maximum": 120},
            },
        },
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 80},
            },
        },
        {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "minItems": 1, "maxItems": 10, "items": {"type": "string"}},
            },
        },
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                    },
                },
            },
        },
        {
            "type": "object",
            "properties": {
                "value": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            },
        },
        {
            "type": "object",
            "definitions": {
                "Addr": {"type": "object", "properties": {"s": {"type": "string"}}},
            },
            "properties": {
                "address": {"$ref": "#/definitions/Addr"},
            },
        },
        {
            # Richness only under $defs; top-level looks like a bare $ref property
            "type": "object",
            "$defs": {
                "Nested": {
                    "type": "object",
                    "properties": {"inner": {"type": "string", "enum": ["a", "b"]}},
                }
            },
            "properties": {"payload": {"$ref": "#/$defs/Nested"}},
        },
    ],
    ids=[
        "pydantic-defs-ref",
        "inline-nested-properties",
        "enum",
        "pattern",
        "format",
        "min-max",
        "min-max-length",
        "min-max-items",
        "items-object",
        "real-union",
        "legacy-definitions",
        "richness-only-under-defs",
    ],
)
def test_input_schema_adds_detail_true_for_rich_schemas(schema):
    assert input_schema_adds_detail(schema) is True


@pytest.mark.unit
def test_input_schema_adds_detail_false_for_non_dict():
    assert input_schema_adds_detail(None) is False
    assert input_schema_adds_detail([]) is False
    assert input_schema_adds_detail("x") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py -v -m unit`

Expected: FAIL with `ImportError` / `cannot import name 'input_schema_adds_detail'`

- [ ] **Step 3: Implement `input_schema_adds_detail`**

In `prompt_utils.py`, add (module level, after `_SYNTHETIC_PLACEHOLDER_KEY`):

```python
_RICH_SCHEMA_KEYS = frozenset(
    {
        "enum",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    }
)


def input_schema_adds_detail(schema: Any) -> bool:
    """True when raw Input Schema JSON carries detail Parameters would lose."""
    if not isinstance(schema, dict) or not schema:
        return False
    return _schema_node_adds_detail(schema, is_root=True)


def _non_null_variants(node: dict) -> list:
    variants: list = []
    for key in ("anyOf", "oneOf"):
        for variant in node.get(key) or []:
            if isinstance(variant, dict) and variant.get("type") != "null":
                variants.append(variant)
    t = node.get("type")
    if isinstance(t, list):
        for x in t:
            if x != "null":
                variants.append({"type": x})
    return variants


def _schema_node_adds_detail(node: Any, *, is_root: bool = False) -> bool:
    if not isinstance(node, dict):
        return False
    if "$ref" in node:
        return True
    for map_key in ("$defs", "definitions"):
        defs = node.get(map_key)
        if isinstance(defs, dict) and defs:
            return True
    if any(k in node for k in _RICH_SCHEMA_KEYS):
        return True

    variants = _non_null_variants(node)
    if "anyOf" in node or "oneOf" in node or isinstance(node.get("type"), list):
        if len(variants) > 1:
            return True
        if len(variants) == 1 and _schema_node_adds_detail(variants[0]):
            return True

    items = node.get("items")
    if isinstance(items, dict):
        if items.get("type") == "object" or "properties" in items or "$ref" in items:
            return True
        if _schema_node_adds_detail(items):
            return True
    elif isinstance(items, list):
        if any(_schema_node_adds_detail(i) for i in items if isinstance(i, dict)):
            return True

    props = node.get("properties")
    if isinstance(props, dict):
        for prop in props.values():
            if not isinstance(prop, dict):
                continue
            if "$ref" in prop:
                return True
            if "properties" in prop and isinstance(prop.get("properties"), dict):
                return True
            if _schema_node_adds_detail(prop):
                return True

    # Walk composited branches already handled; also scan allOf
    for variant in node.get("allOf") or []:
        if _schema_node_adds_detail(variant):
            return True

    return False
```

Notes for implementer:
- Empty `$defs: {}` must be False (only non-empty maps count).
- Optional `T | null` must be False unless the non-null branch itself is rich.
- Presence of non-empty `$defs`/`definitions` alone is enough True (covers richness-only-under-defs).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py -v -m unit`

Expected: all Task-1 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py \
  src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py
git commit -s -m "$(cat <<'EOF'
feat(cuga-lite): detect when find_tools input schema adds detail (#641)

- Add input_schema_adds_detail for nested/enum/constraint JSON Schema
- Cover flat-trim vs rich-keep cases in unit tests

EOF
)"
```

---

### Task 2: `should_emit_output_schema` helper (TDD)

**Files:**
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py`
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py`

**Interfaces:**
- Consumes: none
- Produces: `should_emit_output_schema(response_doc: str, output_schema: Any) -> bool`

- [ ] **Step 1: Write failing tests**

Append to `test_find_tools_schema_trim.py`:

```python
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
    input_schema_adds_detail,
    should_emit_output_schema,
)


@pytest.mark.unit
def test_should_emit_output_schema_false_when_response_doc_present():
    assert should_emit_output_schema("Returns (on success)...", {"type": "object"}) is False


@pytest.mark.unit
def test_should_emit_output_schema_false_for_weak_probe_text():
    assert (
        should_emit_output_schema(
            "⚠️ No declared output schema for this tool. Call it ALONE",
            {"type": "string"},
        )
        is False
    )


@pytest.mark.unit
def test_should_emit_output_schema_true_when_response_doc_empty():
    assert should_emit_output_schema("", {"type": "object", "properties": {"id": {"type": "integer"}}}) is True
    assert should_emit_output_schema("   ", {"type": "string"}) is True


@pytest.mark.unit
def test_should_emit_output_schema_false_when_both_empty():
    assert should_emit_output_schema("", {}) is False
    assert should_emit_output_schema("", None) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py::test_should_emit_output_schema_false_when_response_doc_present -v`

Expected: FAIL import / name not found

- [ ] **Step 3: Implement helper**

```python
def should_emit_output_schema(response_doc: str, output_schema: Any) -> bool:
    """Emit Output Schema JSON only when Response Schema text is absent."""
    if response_doc and str(response_doc).strip():
        return False
    return isinstance(output_schema, dict) and bool(output_schema)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py -v -m unit -k should_emit_output_schema`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py \
  src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py
git commit -s -m "$(cat <<'EOF'
feat(cuga-lite): gate find_tools Output Schema on empty response_doc (#641)

- Emit Output Schema only when Response Schema text is absent
- Unit-cover weak probe, populated, and empty cases

EOF
)"
```

---

### Task 3: Extract `_render_find_tools_markdown` and apply gates

**Files:**
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py` (markdown loop in `find_tools`, ~424–458)
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py`

**Interfaces:**
- Consumes: `input_schema_adds_detail`, `should_emit_output_schema`, `Tool`
- Produces: `_render_find_tools_markdown(query: str, enriched_tools: List[Tool], tool_descriptions: Dict[str, Optional[str]]) -> str`

- [ ] **Step 1: Write failing markdown integration tests**

Append (imports may need `Tool`, `_render_find_tools_markdown`):

```python
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
    Tool,
    _render_find_tools_markdown,
    input_schema_adds_detail,
    should_emit_output_schema,
)


def _tool(**kwargs) -> Tool:
    defaults = dict(
        name="t",
        input={},
        reasoning="why",
        output_schema={},
        params_doc="- `x`: str (required) - x",
        response_doc="",
    )
    defaults.update(kwargs)
    return Tool(**defaults)


@pytest.mark.unit
def test_render_flat_tool_omits_input_and_output_schema():
    flat_input = {
        "type": "object",
        "properties": {"x": {"type": "string", "description": "x"}},
        "required": ["x"],
    }
    md = _render_find_tools_markdown(
        "q",
        [
            _tool(
                name="flat_tool",
                input=flat_input,
                output_schema={"type": "object", "properties": {"id": {"type": "integer"}}},
                params_doc="- `x`: str (required) - x",
                response_doc='\n    \n    Returns (on success) - Response Schema:\n{\n    "type": "object"\n}',
            )
        ],
        {"flat_tool": "Does a flat thing"},
    )
    assert "**Description:** Does a flat thing" in md
    assert "**Parameters:**" in md
    assert "**Response Schema:**" in md
    assert "**Input Schema:**" not in md
    assert "**Output Schema:**" not in md


@pytest.mark.unit
def test_render_nested_pydantic_keeps_input_schema():
    nested = {
        "type": "object",
        "$defs": {
            "Address": {
                "type": "object",
                "properties": {"street": {"type": "string"}},
                "required": ["street"],
            }
        },
        "properties": {"address": {"$ref": "#/$defs/Address"}},
    }
    assert input_schema_adds_detail(nested) is True
    md = _render_find_tools_markdown(
        "q",
        [_tool(name="nested_tool", input=nested, params_doc="- `address`: dict (required) -")],
        {"nested_tool": "Create with address"},
    )
    assert "**Input Schema:**" in md
    assert "$defs" in md
    assert "**Description:** Create with address" in md


@pytest.mark.unit
def test_render_enum_keeps_input_schema_values():
    schema = {"type": "object", "properties": {"role": {"type": "string", "enum": ["admin", "user"]}}}
    md = _render_find_tools_markdown(
        "q",
        [_tool(name="enum_tool", input=schema, params_doc="- `role`: str (required) -")],
        {},
    )
    assert "**Input Schema:**" in md
    assert "admin" in md and "user" in md


@pytest.mark.unit
def test_render_weak_schema_keeps_probe_drops_output_schema():
    md = _render_find_tools_markdown(
        "q",
        [
            _tool(
                name="weak",
                input={},
                params_doc="No parameters required",
                response_doc="⚠️ No declared output schema for this tool. Call it ALONE",
                output_schema={"type": "string"},
            )
        ],
        {},
    )
    assert "No declared output schema" in md
    assert "**Output Schema:**" not in md


@pytest.mark.unit
def test_render_emits_output_schema_only_when_response_doc_missing():
    md = _render_find_tools_markdown(
        "q",
        [
            _tool(
                name="orphan_out",
                response_doc="",
                output_schema={"type": "object", "properties": {"id": {"type": "integer"}}},
            )
        ],
        {},
    )
    assert "**Output Schema:**" in md
    assert '"id"' in md
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py -v -m unit -k render`

Expected: FAIL cannot import `_render_find_tools_markdown`

- [ ] **Step 3: Extract renderer and gate emits**

Add module-level (or `@staticmethod`) helper and replace the inline loop in `find_tools` with a call:

```python
def _render_find_tools_markdown(
    query: str,
    enriched_tools: List[Tool],
    tool_descriptions: Dict[str, Optional[str]],
) -> str:
    markdown_lines = [
        f"# Found {len(enriched_tools)} Matching Tool(s)\n",
        f"**Query:** {query}\n",
    ]
    for idx, tool in enumerate(enriched_tools, 1):
        markdown_lines.append(f"## {idx}. `{tool.name}`\n")

        tool_description = tool_descriptions.get(tool.name)
        if tool_description:
            markdown_lines.append(f"**Description:** {tool_description}\n")

        markdown_lines.append(f"**Reasoning:** {tool.reasoning}\n")

        if tool.params_doc:
            markdown_lines.append("**Parameters:**\n")
            markdown_lines.append(f"{tool.params_doc}\n")
        else:
            markdown_lines.append("**Parameters:** No parameters required\n")

        if tool.response_doc:
            markdown_lines.append("**Response Schema:**\n")
            markdown_lines.append(f"{tool.response_doc}\n")

        if tool.input_ and tool.input_ != {} and input_schema_adds_detail(tool.input_):
            markdown_lines.append("**Input Schema:**\n")
            markdown_lines.append(f"```json\n{json.dumps(tool.input_, indent=2)}\n```\n")

        if should_emit_output_schema(tool.response_doc, tool.output_schema):
            markdown_lines.append("**Output Schema:**\n")
            markdown_lines.append(f"```json\n{json.dumps(tool.output_schema, indent=2)}\n```\n")

        markdown_lines.append("---\n")

    return "\n".join(markdown_lines)
```

In `find_tools`, replace the markdown assembly block after `tool_descriptions = {...}` with:

```python
        return _render_find_tools_markdown(query, enriched_tools, tool_descriptions)
```

Do not change `Tool` construction or shortlister payload.

- [ ] **Step 4: Run full trim suite + weak-schema regression**

Run:

```bash
uv run pytest \
  src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py \
  src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prompt_utils_weak_schema.py \
  -v -m unit
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py \
  src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py
git commit -s -m "$(cat <<'EOF'
fix(cuga-lite): trim duplicate find_tools schema blocks (#641)

- Gate Input Schema on input_schema_adds_detail
- Gate Output Schema on empty response_doc
- Extract _render_find_tools_markdown for unit coverage

EOF
)"
```

---

### Task 4: Payload non-regression + ruff

**Files:**
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py` (add one test)

**Interfaces:**
- Consumes: `PromptUtils._build_shortlister_payload`
- Produces: none

- [ ] **Step 1: Write failing/passing payload assertion**

```python
from types import SimpleNamespace
from unittest.mock import MagicMock

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils


@pytest.mark.unit
def test_build_shortlister_payload_still_includes_full_schemas():
    args_schema = MagicMock()
    args_schema.schema.return_value = {
        "type": "object",
        "properties": {"role": {"type": "string", "enum": ["a", "b"]}},
    }
    func = SimpleNamespace(
        _response_schemas={"success": {"type": "object", "properties": {"id": {"type": "integer"}}}},
        _param_constraints={"role": ["enum: a|b"]},
    )
    tool = MagicMock()
    tool.name = "rich_tool"
    tool.model_dump.return_value = {"name": "rich_tool", "description": "d"}
    tool.args_schema = args_schema
    tool.func = func

    tools_as_dict, _apps = PromptUtils._build_shortlister_payload([tool], [])
    assert tools_as_dict["rich_tool"]["args_schema"]["properties"]["role"]["enum"] == ["a", "b"]
    assert "success" in tools_as_dict["rich_tool"]["_response_schemas"]
    assert tools_as_dict["rich_tool"]["_param_constraints"]["role"] == ["enum: a|b"]
```

If `MagicMock` + `model_dump` is awkward with real `StructuredTool`, build a minimal stand-in that matches what `_build_shortlister_payload` accesses (`name`, `model_dump`, `args_schema`, `func`). Adjust to the lightest pattern that passes.

- [ ] **Step 2: Run test**

Run: `uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py::test_build_shortlister_payload_still_includes_full_schemas -v`

Expected: PASS

- [ ] **Step 3: Ruff**

Run: `uv run ruff check src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py && uv run ruff format src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py`

Expected: clean

- [ ] **Step 4: Full suite for touched tests**

Run:

```bash
uv run pytest \
  src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py \
  src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_prompt_utils_weak_schema.py \
  -v -m unit
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py \
  src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py
git commit -s -m "$(cat <<'EOF'
test(cuga-lite): assert shortlister payload keeps full schemas (#641)

- Guard against accidental payload stripping while trimming render output

EOF
)"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|---|---|
| Prefer Parameters; conditional Input Schema | 1, 3 |
| Drop Output Schema when response_doc non-empty | 2, 3 |
| Never trim Description | 3 (markdown tests) |
| Richness heuristic A (nested/enum/constraints/unions/definitions) | 1 |
| Render-time only; Tool fields still populated | 3 |
| No shortlister payload change | 4 |
| Weak-schema probe unchanged | 3 + weak_schema regression |
| Unit matrix A–E | 1–4 |

## Self-review notes

- No TBD/placeholders in steps.
- Helper names stable across tasks: `input_schema_adds_detail`, `should_emit_output_schema`, `_render_find_tools_markdown`.
- `$defs` empty-map edge case called out in Task 1 notes.
