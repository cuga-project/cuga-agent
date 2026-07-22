"""Weak-schema detection and probing directive in PromptUtils.get_tool_docs (issue #272)."""

from __future__ import annotations

from types import MappingProxyType, SimpleNamespace
from collections import UserDict

from pydantic import BaseModel

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils, _normalize_response_schemas


def _make_tool(response_schemas=None, has_response_schemas_attr=True):
    func = SimpleNamespace(_response_schemas=response_schemas) if has_response_schemas_attr else object()
    return SimpleNamespace(name="some_tool", func=func, args_schema=None)


# All calls below pin mode="truncate_at_first_probe" explicitly: these tests exercise pure
# schema-shape detection, which is orthogonal to the advanced_features.cuga_lite_weak_schema_probing_mode
# gate (default "combine_and_execute" would make is_weak_schema_tool always return False).
_MODE = "truncate_at_first_probe"


def test_is_weak_schema_tool_true_when_empty_dict():
    tool = _make_tool(response_schemas={})
    assert PromptUtils.is_weak_schema_tool(tool, mode=_MODE) is True


def test_is_weak_schema_tool_true_when_attr_missing():
    tool = _make_tool(has_response_schemas_attr=False)
    assert PromptUtils.is_weak_schema_tool(tool, mode=_MODE) is True


def test_is_weak_schema_tool_true_when_generic_mcp_placeholder():
    # The MCP manager tags the synthetic no-schema placeholder it injects.
    tool = _make_tool(
        response_schemas={
            "success": {"type": "string"},
            "failure": {"type": "string"},
            "_synthetic_placeholder": True,
        }
    )
    assert PromptUtils.is_weak_schema_tool(tool, mode=_MODE) is True


def test_is_weak_schema_tool_false_when_real_schema_declared():
    tool = _make_tool(
        response_schemas={"success": {"type": "object", "properties": {"id": {"type": "integer"}}}}
    )
    assert PromptUtils.is_weak_schema_tool(tool, mode=_MODE) is False


def test_is_weak_schema_tool_false_for_genuine_string_returning_tool():
    """Regression: a real string-returning tool (OpenAPI text body or an MCP
    tool that actually declares outputSchema {"type": "string"}) has a success
    schema identical to the synthetic placeholder but carries no marker — it
    must NOT be flagged weak and have its schema suppressed. Also answers
    Sami's PR #417 review question ("-> str is string ok?")."""
    tool = _make_tool(response_schemas={"success": {"type": "string"}, "failure": {"type": "string"}})
    assert PromptUtils.is_weak_schema_tool(tool, mode=_MODE) is False


def test_get_tool_docs_renders_probing_directive_for_weak_schema_tool():
    tool = _make_tool(response_schemas={})
    _params_doc, response_doc = PromptUtils.get_tool_docs(tool, mode=_MODE)
    assert "No declared output schema" in response_doc
    assert "ALONE" in response_doc


def test_get_tool_docs_renders_probing_directive_for_mcp_placeholder_schema():
    tool = _make_tool(
        response_schemas={
            "success": {"type": "string"},
            "failure": {"type": "string"},
            "_synthetic_placeholder": True,
        }
    )
    _params_doc, response_doc = PromptUtils.get_tool_docs(tool, mode=_MODE)
    assert "No declared output schema" in response_doc


def test_get_tool_docs_renders_real_schema_for_genuine_string_tool():
    """Regression: an unmarked bare-string success schema renders as a real
    schema, not the probing directive."""
    tool = _make_tool(response_schemas={"success": {"type": "string"}, "failure": {"type": "string"}})
    _params_doc, response_doc = PromptUtils.get_tool_docs(tool, mode=_MODE)
    assert "Returns (on success) - Response Schema" in response_doc
    assert "No declared output schema" not in response_doc


def test_get_tool_docs_renders_real_schema_for_known_schema_tool():
    """Regression: tools with a real schema must render exactly as before."""
    tool = _make_tool(
        response_schemas={"success": {"type": "object", "properties": {"id": {"type": "integer"}}}}
    )
    _params_doc, response_doc = PromptUtils.get_tool_docs(tool, mode=_MODE)
    assert "Returns (on success) - Response Schema" in response_doc
    assert "No declared output schema" not in response_doc


# --- PR #417 review thread follow-up: Pydantic model / non-dict Mapping edge cases ---
# Sami asked CodeRabbit "what if response schema is pydantic object" and about -> Dict / -> dict
# edge cases; CodeRabbit identified these as real false positives in is_weak_schema_tool.


class _SampleOutput(BaseModel):
    id: int
    name: str


def test_is_weak_schema_tool_false_when_pydantic_model_class_response_schema():
    tool = _make_tool(response_schemas=_SampleOutput)
    assert PromptUtils.is_weak_schema_tool(tool, mode=_MODE) is False


def test_is_weak_schema_tool_false_when_pydantic_model_instance_response_schema():
    tool = _make_tool(response_schemas=_SampleOutput(id=1, name="x"))
    assert PromptUtils.is_weak_schema_tool(tool, mode=_MODE) is False


def test_get_tool_docs_renders_real_schema_for_pydantic_model_class_response_schema():
    tool = _make_tool(response_schemas=_SampleOutput)
    _params_doc, response_doc = PromptUtils.get_tool_docs(tool, mode=_MODE)
    assert "Returns (on success) - Response Schema" in response_doc
    assert "name" in response_doc
    assert "No declared output schema" not in response_doc


def test_is_weak_schema_tool_false_when_mappingproxytype_response_schema():
    schema = MappingProxyType({"success": {"type": "object", "properties": {"id": {"type": "integer"}}}})
    tool = _make_tool(response_schemas=schema)
    assert PromptUtils.is_weak_schema_tool(tool, mode=_MODE) is False


def test_is_weak_schema_tool_false_when_userdict_response_schema():
    schema = UserDict({"success": {"type": "object"}})
    tool = _make_tool(response_schemas=schema)
    assert PromptUtils.is_weak_schema_tool(tool, mode=_MODE) is False


def test_is_weak_schema_tool_true_when_mappingproxytype_empty():
    tool = _make_tool(response_schemas=MappingProxyType({}))
    assert PromptUtils.is_weak_schema_tool(tool, mode=_MODE) is True


def test_is_weak_schema_tool_true_when_mappingproxytype_synthetic_placeholder():
    schema = MappingProxyType({"success": {"type": "string"}, "_synthetic_placeholder": True})
    tool = _make_tool(response_schemas=schema)
    assert PromptUtils.is_weak_schema_tool(tool, mode=_MODE) is True


def test_dict_and_dict_typed_return_schemas_not_weak():
    """Answers Sami's review question: tools annotated -> Dict / -> dict with a real
    object schema must not be classified as weak."""
    tool = _make_tool(
        response_schemas={"success": {"type": "object", "properties": {"a": {"type": "string"}}}}
    )
    assert PromptUtils.is_weak_schema_tool(tool, mode=_MODE) is False


# --- Direct unit coverage of _normalize_response_schemas ---


def test_normalize_response_schemas_none():
    assert _normalize_response_schemas(None) is None


def test_normalize_response_schemas_unsupported_shape_returns_none():
    # A bare list is not a recognized schema container; stays weak (preserves prior behavior).
    assert _normalize_response_schemas(["not", "a", "schema"]) is None


def test_normalize_response_schemas_dict_passthrough():
    schema = {"success": {"type": "string"}}
    assert _normalize_response_schemas(schema) is schema


def test_normalize_response_schemas_mappingproxytype_passthrough():
    schema = MappingProxyType({"success": {"type": "string"}})
    assert _normalize_response_schemas(schema) is schema


def test_normalize_response_schemas_pydantic_class():
    normalized = _normalize_response_schemas(_SampleOutput)
    assert "success" in normalized
    assert normalized["success"]["properties"]["name"]["type"] == "string"


def test_normalize_response_schemas_pydantic_instance():
    normalized = _normalize_response_schemas(_SampleOutput(id=1, name="x"))
    assert "success" in normalized
    assert normalized["success"]["properties"]["id"]["type"] == "integer"
