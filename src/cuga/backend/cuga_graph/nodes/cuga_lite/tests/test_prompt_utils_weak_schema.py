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
    # The MCP manager tags the synthetic no-schema placeholder it injects.
    tool = _make_tool(
        response_schemas={
            "success": {"type": "string"},
            "failure": {"type": "string"},
            "_synthetic_placeholder": True,
        }
    )
    assert PromptUtils.is_weak_schema_tool(tool) is True


def test_is_weak_schema_tool_false_when_real_schema_declared():
    tool = _make_tool(
        response_schemas={"success": {"type": "object", "properties": {"id": {"type": "integer"}}}}
    )
    assert PromptUtils.is_weak_schema_tool(tool) is False


def test_is_weak_schema_tool_false_for_genuine_string_returning_tool():
    """Regression: a real string-returning tool (OpenAPI text body or an MCP
    tool that actually declares outputSchema {"type": "string"}) has a success
    schema identical to the synthetic placeholder but carries no marker — it
    must NOT be flagged weak and have its schema suppressed."""
    tool = _make_tool(response_schemas={"success": {"type": "string"}, "failure": {"type": "string"}})
    assert PromptUtils.is_weak_schema_tool(tool) is False


def test_get_tool_docs_renders_probing_directive_for_weak_schema_tool():
    tool = _make_tool(response_schemas={})
    _params_doc, response_doc = PromptUtils.get_tool_docs(tool)
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
    _params_doc, response_doc = PromptUtils.get_tool_docs(tool)
    assert "No declared output schema" in response_doc


def test_get_tool_docs_renders_real_schema_for_genuine_string_tool():
    """Regression: an unmarked bare-string success schema renders as a real
    schema, not the probing directive."""
    tool = _make_tool(response_schemas={"success": {"type": "string"}, "failure": {"type": "string"}})
    _params_doc, response_doc = PromptUtils.get_tool_docs(tool)
    assert "Returns (on success) - Response Schema" in response_doc
    assert "No declared output schema" not in response_doc


def test_get_tool_docs_renders_real_schema_for_known_schema_tool():
    """Regression: tools with a real schema must render exactly as before."""
    tool = _make_tool(
        response_schemas={"success": {"type": "object", "properties": {"id": {"type": "integer"}}}}
    )
    _params_doc, response_doc = PromptUtils.get_tool_docs(tool)
    assert "Returns (on success) - Response Schema" in response_doc
    assert "No declared output schema" not in response_doc
