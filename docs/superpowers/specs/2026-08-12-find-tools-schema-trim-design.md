# Design: Trim duplicated schemas in `find_tools` (#641)

Part of #616. Bug: discovery markdown from `PromptUtils.find_tools` duplicates the same schema information as both human docs and raw JSON blocks. Input/Output Schema JSON alone was measured at ~55–65% of discovery output on AppWorld tasks `80acbaf_1` and `6d59d90_1`.

## Goal

Emit each schema **once** when the second form adds no information, without new product machinery. Treat **input**, **description**, and **output** separately. Prefer a free render-time win; never omit details that Parameters / Response Schema do not already carry.

## Non-goals

- Do not change the shortlister LLM payload (`_build_shortlister_payload` still includes full `args_schema`, `_response_schemas`, `_param_constraints`).
- Do not rewrite or “enrich” Parameters to absorb nested schemas (out of scope for #641).
- Do not trim or rewrite tool **Description**.
- Do not change weak-schema probing behavior in `get_tool_docs`.

## Current behavior (problem)

For each shortlisted tool, `find_tools` markdown emits:

1. **Description** (tool.description)
2. **Reasoning**
3. **Parameters** (`params_doc` from `get_tool_docs` — flat name/type/required/desc/constraints)
4. **Response Schema** (`response_doc` — full success JSON dump, or weak-schema probe directive)
5. **Input Schema** (raw `args_schema` JSON)
6. **Output Schema** (raw success `_response_schemas` JSON)

(5) largely overlaps (3) for flat tools but is **not** equivalent for nested Pydantic / rich JSON Schema. (6) largely duplicates (4) whenever `response_doc` already contains the success schema (or the probe text).

## Decisions

| Concern | Choice |
|---|---|
| Input canonical form | Prefer **Parameters**; emit **Input Schema** only when it adds detail Parameters lose |
| Output | Always drop **Output Schema** when `response_doc` is non-empty |
| Description | Always emit if present; never part of trim logic |
| Richness heuristic | Keep Input Schema on `$ref`/`$defs`/`definitions`, `enum`, nested properties, object `items`, pattern/format/bounds, non-null unions |
| Implementation shape | Render-time gates only (still populate `Tool` fields as today) |

## Approach

**Render-time gates in the `find_tools` markdown loop** (recommended and selected).

Keep constructing `Tool` with `input_`, `output_schema`, `params_doc`, and `response_doc` unchanged. Decide what to print only when assembling markdown. Small pure helpers, unit-tested heavily.

Rejected alternatives:

- Strip fields when constructing `Tool` — mixes data vs presentation; harder to assert “had vs showed”.
- Enrich Parameters and always drop Input Schema — new machinery / fidelity risk; out of #641 scope.

## Policy by section

| Section | Policy |
|---|---|
| **Description** | Always emit if present. |
| **Parameters** | Always emit (from `params_doc`). |
| **Input Schema** | Emit JSON only if non-empty **and** `input_schema_adds_detail(schema)` is true. |
| **Response Schema** | Always emit when `response_doc` is non-empty (schema dump or weak probe). |
| **Output Schema** | Emit only if `response_doc` is empty/whitespace **and** `output_schema` is a non-empty dict; otherwise omit. |

## Helpers

Colocate in `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py` (no new package).

### `input_schema_adds_detail(schema: dict) -> bool`

Recursively scan `schema` (including values under `$defs` / `definitions`). Return **true** if any node has:

- `$ref`
- Non-empty `$defs` or `definitions` maps (empty `{}` does not count)
- `enum`
- A property whose value is itself an object-with-`properties`, or a `$ref` (nested structure beyond flat top-level primitives)
- `items` that is an object schema (not a bare primitive `{type: ...}`)
- `pattern`, `format`
- `minimum` / `maximum` / `exclusiveMinimum` / `exclusiveMaximum`
- `minLength` / `maxLength` / `minItems` / `maxItems`
- `anyOf` / `oneOf` with more than one non-null variant (same spirit as existing `schema_type_is_ambiguous`)

Non-dict input → **false** (call site also skips emit when schema is empty/non-dict).

Return **false** for:

- Empty `{}`
- Root-only flat object whose properties are primitives (and optional-via-null unions only), with descriptions/titles/defaults as the only extras
- Arrays of primitives (`items: {type: string}`)

### `should_emit_output_schema(response_doc: str, output_schema: dict) -> bool`

True only when `response_doc` is empty/whitespace **and** `output_schema` is a non-empty dict.

### Wire-up

Only change the markdown loop in `PromptUtils.find_tools` that currently always appends Input/Output Schema blocks. Optional: extract a tiny `_render_find_tools_markdown(...)` for easier unit testing of the assembled string without mocking the shortlister LLM — allowed if it stays a pure formatting helper with no new behavior.

## Testing

New unit tests (pytest `@pytest.mark.unit`), e.g. `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_find_tools_schema_trim.py`.

### A. `input_schema_adds_detail` → False (trim)

1. Empty `{}`
2. Flat primitives only (`str` / `int` / `float` / `bool`)
3. Optional via `anyOf: [T, null]` or `type: [T, "null"]` only
4. Array of primitives
5. Descriptions / titles / `default` only

### B. `input_schema_adds_detail` → True (keep)

6. Nested Pydantic-style `$defs` + `$ref`
7. Inline nested `properties` on a field
8. `enum` / `Literal`
9. `pattern` / `format`
10. Numeric/string/array bounds (`minimum`/`maximum`, `minLength`/`maxLength`, `minItems`/`maxItems`)
11. `items` as object schema
12. Real unions (`anyOf` with two non-null types)
13. Richness only under `$defs` (flat-looking top-level `$ref`s)
14. Legacy OpenAPI `definitions` key

### C. Output emit gate

15. Non-empty `response_doc` + non-empty `output_schema` → do not emit
16. Weak-schema probe text in `response_doc` → do not emit
17. Empty `response_doc` + non-empty `output_schema` → emit
18. Both empty → emit neither

### D. Markdown integration

19. Flat tool: Parameters present; no Input Schema; no Output Schema
20. Nested Pydantic tool: Parameters **and** Input Schema containing `$defs`/`$ref` (or nested fields)
21. Enum tool: Input Schema kept; enum values visible in JSON
22. Description always present when the tool has a description
23. Weak schema: probe under Response Schema; no duplicate Output Schema JSON
24. Strong output schema: Response Schema present; Output Schema omitted; success shape still in `response_doc`

### E. Regression / non-goals

25. `_build_shortlister_payload` still includes full `args_schema` / `_response_schemas`
26. Existing `test_prompt_utils_weak_schema.py` still passes

## Error handling / edge cases

- Non-dict schemas: treat as no Input Schema emit (`input_schema_adds_detail` false / guard at call site).
- List-shaped success schemas already normalized to dict before `Tool` construction; no change there.
- If Parameters says “No parameters required” but Input Schema is rich → still emit Input Schema when `input_schema_adds_detail` is true.
- Never drop Response Schema probe text for weak tools in favor of placeholder Output Schema JSON.

## Success criteria

- Flat discovery tools no longer print duplicate Input/Output Schema JSON.
- Nested/rich input schemas still appear in discovery markdown.
- Description and weak-schema probe behavior unchanged.
- Unit matrix above green; no shortlister payload change.
