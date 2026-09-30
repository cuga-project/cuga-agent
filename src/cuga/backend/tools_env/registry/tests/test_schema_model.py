"""Faithful registry-tool input models (docs/design/fc-deferred-tool-search.md §3.9)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cuga.backend.tools_env.registry.utils.schema_model import (
    build_input_model,
    constraints_to_keywords,
    params_to_json_schema,
    resolve_validation_mode,
)

_CORPUS = (
    Path(__file__).resolve().parents[3]
    / "cuga_graph"
    / "nodes"
    / "cuga_lite"
    / "tests"
    / "corpus"
    / "public_ops.json"
)

# Registry parameter-list shape, as openapi_parser_v0 emits it.
_REGISTRY_PARAMS = [
    {
        "name": "order",
        "type": "string",
        "required": False,
        "description": "Sort direction",
        "default": "desc",
        "constraints": ["must be one of: [asc, desc]"],
    },
    {
        "name": "limit",
        "type": "integer",
        "required": False,
        "description": "",
        "default": 50,
        "constraints": [">= 1", "<= 100"],
    },
    {
        "name": "ids",
        "type": "array",
        "required": True,
        "description": "",
        "constraints": ["min items: 1"],
        "schema": {"type": "array", "items": "integer"},
    },
    {"name": "page", "type": "integer", "required": False, "constraints": ["must be one of: [1, 2, 3]"]},
]


def _errors(model: Any, payload: dict) -> list[str]:
    with pytest.raises(ValidationError) as exc:
        model.model_validate(payload)
    return [e["type"] for e in exc.value.errors()]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "json_type", "expected"),
    [
        ("must be one of: [asc, desc]", "string", {"enum": ["asc", "desc"]}),
        ("must be one of: [1, 2]", "integer", {"enum": [1, 2]}),
        ("must be one of: [true, false]", "boolean", {"enum": [True, False]}),
        (">= 1", "integer", {"minimum": 1}),
        ("> 0.5", "number", {"exclusiveMinimum": 0.5}),
        ("<= 100", "integer", {"maximum": 100}),
        ("< 10", "integer", {"exclusiveMaximum": 10}),
        ("length >= 3", "string", {"minLength": 3}),
        ("length <= 8", "string", {"maxLength": 8}),
        ("min items: 1", "array", {"minItems": 1}),
        ("max items: 5", "array", {"maxItems": 5}),
        ("matches pattern: ^[a-z]+$", "string", {"pattern": "^[a-z]+$"}),
        ("format: date", "string", {"format": "date"}),
        ("multiple of 5", "integer", {"multipleOf": 5}),
        ("items must be unique", "array", {"uniqueItems": True}),
        ("something the parser never wrote", "string", {}),
    ],
)
def test_constraint_strings_round_trip(text, json_type, expected):
    assert constraints_to_keywords([text], json_type) == expected


@pytest.mark.unit
def test_enum_that_does_not_coerce_is_dropped_not_half_applied():
    assert constraints_to_keywords(["must be one of: [1, two]"], "integer") == {}


@pytest.mark.unit
def test_params_to_json_schema_recovers_structure():
    schema = params_to_json_schema(_REGISTRY_PARAMS)
    props = schema["properties"]
    assert schema["required"] == ["ids"]
    assert props["order"]["enum"] == ["asc", "desc"]
    assert props["limit"]["minimum"] == 1 and props["limit"]["maximum"] == 100
    assert props["ids"]["items"] == {"type": "integer"}
    assert props["ids"]["minItems"] == 1
    # The raw strings stay available for prompt rendering.
    assert props["order"]["constraints"] == ["must be one of: [asc, desc]"]


@pytest.mark.unit
def test_params_to_json_schema_merges_real_json_schema_from_tracker_defs():
    schema = params_to_json_schema(
        [{"name": "mode", "schema": {"type": "string", "enum": ["a", "b"]}, "required": True}]
    )
    assert schema["properties"]["mode"]["type"] == "string"
    assert schema["properties"]["mode"]["enum"] == ["a", "b"]


@pytest.mark.unit
def test_example_payload_in_schema_slot_is_ignored():
    # _summarize_param_schema returns an ``example`` in place of a shape when one exists.
    schema = params_to_json_schema(
        [{"name": "body", "type": "object", "schema": {"type": "wire-transfer", "amount": 5}}]
    )
    assert "properties" not in schema["properties"]["body"]


@pytest.mark.unit
def test_faithful_model_enforces_the_schema():
    model = build_input_model("list_games", _REGISTRY_PARAMS, mode="lax")

    assert _errors(model, {"ids": [1], "order": "ascending"}) == ["literal_error"]
    assert set(_errors(model, {"ids": [], "limit": 0})) == {"greater_than_equal", "too_short"}
    assert _errors(model, {"ids": [1], "team": "x"}) == ["extra_forbidden"]
    assert _errors(model, {}) == ["missing"]
    assert _errors(model, {"ids": [1], "page": 4}) == ["literal_error"]


@pytest.mark.unit
def test_lax_mode_coerces_and_strict_mode_does_not():
    lax = build_input_model("t", _REGISTRY_PARAMS, mode="lax")
    assert lax.model_validate({"ids": ["1", 2], "limit": "5"}).model_dump(exclude_unset=True) == {
        "ids": [1, 2],
        "limit": 5,
    }
    strict = build_input_model("t", _REGISTRY_PARAMS, mode="strict")
    _errors(strict, {"ids": ["1"]})


@pytest.mark.unit
def test_optional_fields_accept_none_and_keep_defaults():
    model = build_input_model("t", _REGISTRY_PARAMS, mode="lax")
    parsed = model.model_validate({"ids": [1], "limit": None})
    assert parsed.limit is None and parsed.order == "desc"


@pytest.mark.unit
def test_wire_schema_carries_enum_items_and_bounds():
    js = build_input_model("t", _REGISTRY_PARAMS, mode="lax").model_json_schema()
    dumped = json.dumps(js)
    assert '"enum": ["asc", "desc"]' in dumped
    assert js["properties"]["ids"]["items"] == {"type": "integer"}
    assert '"maximum": 100' in dumped
    assert js["additionalProperties"] is False


@pytest.mark.unit
def test_nested_objects_validate_fields_but_allow_unknown_keys():
    model = build_input_model(
        "create_order",
        {
            "type": "object",
            "properties": {
                "customer": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "tier": {"type": "string", "enum": ["a", "b"]}},
                    "required": ["id"],
                }
            },
            "required": ["customer"],
        },
        mode="lax",
    )
    model.model_validate({"customer": {"id": 1, "note": "extra keys pass at depth"}})
    assert _errors(model, {"customer": {"tier": "a"}}) == ["missing"]
    assert _errors(model, {"customer": {"id": 1, "tier": "z"}}) == ["literal_error"]


@pytest.mark.unit
def test_ambiguous_union_stays_any():
    model = build_input_model(
        "t",
        {"type": "object", "properties": {"v": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}},
        mode="lax",
    )
    for value in ("x", 3, [1], {"a": 1}):
        model.model_validate({"v": value})


@pytest.mark.unit
def test_nullable_union_unwraps_to_the_real_type():
    model = build_input_model(
        "t",
        {
            "type": "object",
            "properties": {"n": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}},
        },
        mode="lax",
    )
    model.model_validate({"n": None})
    model.model_validate({"n": 2})
    assert _errors(model, {"n": 0}) == ["greater_than_equal"]


@pytest.mark.unit
def test_off_mode_is_the_legacy_flat_model():
    model = build_input_model("t", _REGISTRY_PARAMS, mode="off")
    # Legacy: bare types, enums/bounds ignored, unknown keys dropped silently.
    parsed = model.model_validate({"ids": [1], "order": "ascending", "limit": 0, "team": "x"})
    assert parsed.order == "ascending" and parsed.limit == 0
    assert "team" not in parsed.model_dump()


@pytest.mark.unit
def test_invalid_regex_pattern_is_skipped_not_fatal():
    model = build_input_model(
        "t",
        [
            {
                "name": "code",
                "type": "string",
                "required": True,
                "constraints": ["matches pattern: ([unclosed"],
            }
        ],
        mode="lax",
    )
    model.model_validate({"code": "anything"})


@pytest.mark.unit
def test_unbuildable_model_falls_back_to_flat(monkeypatch):
    from cuga.backend.tools_env.registry.utils import schema_model

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(schema_model, "_field_definitions", boom)
    model = build_input_model("t", _REGISTRY_PARAMS, mode="lax")
    model.model_validate({"ids": [1], "order": "ascending"})  # flat model accepts it


@pytest.mark.unit
def test_every_public_corpus_operation_builds():
    corpus = json.loads(_CORPUS.read_text())
    built = 0
    for ops in corpus.values():
        if not isinstance(ops, list):
            continue
        for op in ops:
            schema = dict(op.get("input_schema") or {})
            # Leading-underscore corpus annotations are not parameters (Pydantic rejects them).
            schema["properties"] = {
                k: v for k, v in (schema.get("properties") or {}).items() if not k.startswith("_")
            }
            build_input_model(op.get("operation_id") or "op", schema, mode="lax").model_json_schema()
            built += 1
    assert built > 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"), [("lax", "lax"), ("STRICT", "strict"), ("off", "off"), ("", "lax"), ("bogus", "lax")]
)
def test_resolve_validation_mode(raw, expected):
    assert resolve_validation_mode(raw) == expected
