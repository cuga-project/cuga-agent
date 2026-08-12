from typing import Any

import pytest

from cuga.backend.tools_env.registry.utils.schema_utils import (
    json_schema_type,
    python_type_for_schema,
    schema_type_is_ambiguous,
)

pytestmark = pytest.mark.unit


def test_pydantic_optional_anyof_keeps_real_type():
    """The AppWorld 321ec38_1 regression: repeat_days is a list, not a str."""
    repeat_days = {
        "anyOf": [{"items": {}, "type": "array"}, {"type": "null"}],
        "default": None,
        "title": "Repeat Days",
    }
    assert json_schema_type(repeat_days) == "array"
    assert python_type_for_schema(repeat_days) is list


def test_optional_scalars_keep_their_types():
    for jtype, py in (("integer", int), ("number", float), ("boolean", bool), ("string", str)):
        prop = {"anyOf": [{"type": jtype}, {"type": "null"}], "default": None}
        assert json_schema_type(prop) == jtype
        assert python_type_for_schema(prop) is py


def test_plain_type_and_openapi_31_list_form():
    assert json_schema_type({"type": "string"}) == "string"
    assert json_schema_type({"type": ["string", "null"]}) == "string"
    assert python_type_for_schema({"type": ["integer", "null"]}) is int


def test_implicit_object_and_fallback():
    assert json_schema_type({"properties": {"a": {"type": "string"}}}) == "object"
    assert json_schema_type({}) == "string"
    assert python_type_for_schema({}) is Any


def test_ambiguous_unions_and_refs_use_any():
    union = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert schema_type_is_ambiguous(union)
    assert python_type_for_schema(union) is Any
    assert schema_type_is_ambiguous({"$ref": "#/components/schemas/Body"})
    assert python_type_for_schema({"$ref": "#/components/schemas/Body"}) is Any


def test_openapi31_type_array_unions_are_ambiguous():
    """OpenAPI 3.1 type arrays are the same union class as anyOf."""
    union = {"type": ["string", "integer"]}
    assert schema_type_is_ambiguous(union)
    assert python_type_for_schema(union) is Any
    # Optional spellings stay narrow
    assert not schema_type_is_ambiguous({"type": ["string", "null"]})
    assert python_type_for_schema({"type": ["string", "null"]}) is str
    assert not schema_type_is_ambiguous({"type": ["integer", "null"]})
    assert python_type_for_schema({"type": ["integer", "null"]}) is int


if __name__ == "__main__":
    test_pydantic_optional_anyof_keeps_real_type()
    test_optional_scalars_keep_their_types()
    test_plain_type_and_openapi_31_list_form()
    test_implicit_object_and_fallback()
    test_ambiguous_unions_and_refs_use_any()
    test_openapi31_type_array_unions_are_ambiguous()
    print("ok")
