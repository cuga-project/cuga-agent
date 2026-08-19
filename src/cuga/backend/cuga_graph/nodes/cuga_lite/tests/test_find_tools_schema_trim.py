"""find_tools discovery markdown schema trim (#641)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
    PromptUtils,
    Tool,
    _render_find_tools_markdown,
    input_schema_adds_detail,
    should_emit_output_schema,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "schema",
    [
        {},
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "n", "title": "Name"},
                "count": {"type": "integer"},
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
        {"type": "object", "prefixItems": []},
    ],
    ids=[
        "empty",
        "flat-primitives",
        "optional-anyOf-null",
        "optional-type-list-null",
        "array-of-primitives",
        "empty-prefix-items",
    ],
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
            "type": "object",
            "$defs": {
                "Nested": {
                    "type": "object",
                    "properties": {"inner": {"type": "string", "enum": ["a", "b"]}},
                }
            },
            "properties": {"payload": {"$ref": "#/$defs/Nested"}},
        },
        {
            "type": "object",
            "properties": {"count": {"type": "integer", "multipleOf": 5}},
        },
        {
            "type": "object",
            "properties": {"items": {"type": "array", "uniqueItems": True}},
        },
        {
            "type": "object",
            "patternProperties": {"^x-": {"type": "string"}},
        },
        {
            "type": "object",
            "properties": {"items": {"type": "array", "contains": {"type": "string"}}},
        },
        {
            "type": "object",
            "dependentSchemas": {"mode": {"required": ["value"]}},
        },
        {
            "type": "object",
            "properties": {"value": {"not": {"type": "null"}}},
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
        "multiple-of",
        "unique-items",
        "pattern-properties",
        "contains",
        "dependent-schemas",
        "not",
    ],
)
def test_input_schema_adds_detail_true_for_rich_schemas(schema):
    assert input_schema_adds_detail(schema) is True


@pytest.mark.unit
def test_input_schema_adds_detail_true_for_default():
    schema = {"type": "object", "properties": {"page": {"type": "integer", "default": 1}}}
    assert input_schema_adds_detail(schema) is True


@pytest.mark.unit
def test_input_schema_adds_detail_false_for_non_dict():
    assert input_schema_adds_detail(None) is False
    assert input_schema_adds_detail([]) is False
    assert input_schema_adds_detail("x") is False


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
    assert (
        should_emit_output_schema("", {"type": "object", "properties": {"id": {"type": "integer"}}}) is True
    )
    assert should_emit_output_schema("   ", {"type": "string"}) is True


@pytest.mark.unit
def test_should_emit_output_schema_false_when_both_empty():
    assert should_emit_output_schema("", {}) is False
    assert should_emit_output_schema("", None) is False


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
