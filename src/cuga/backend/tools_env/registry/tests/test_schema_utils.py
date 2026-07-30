import pytest

from cuga.backend.tools_env.registry.utils.schema_utils import json_schema_type

pytestmark = pytest.mark.unit


def test_pydantic_optional_anyof_keeps_real_type():
    """The AppWorld 321ec38_1 regression: repeat_days is a list, not a str."""
    repeat_days = {
        "anyOf": [{"items": {}, "type": "array"}, {"type": "null"}],
        "default": None,
        "title": "Repeat Days",
    }
    assert json_schema_type(repeat_days) == "array"


def test_optional_scalars_keep_their_types():
    for jtype in ("integer", "number", "boolean", "string"):
        prop = {"anyOf": [{"type": jtype}, {"type": "null"}], "default": None}
        assert json_schema_type(prop) == jtype


def test_plain_type_and_openapi_31_list_form():
    assert json_schema_type({"type": "string"}) == "string"
    assert json_schema_type({"type": ["string", "null"]}) == "string"


def test_implicit_object_and_fallback():
    assert json_schema_type({"properties": {"a": {"type": "string"}}}) == "object"
    assert json_schema_type({}) == "string"


if __name__ == "__main__":
    test_pydantic_optional_anyof_keeps_real_type()
    test_optional_scalars_keep_their_types()
    test_plain_type_and_openapi_31_list_form()
    test_implicit_object_and_fallback()
    print("ok")
