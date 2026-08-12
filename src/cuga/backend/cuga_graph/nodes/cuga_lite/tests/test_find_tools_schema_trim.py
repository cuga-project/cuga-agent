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
