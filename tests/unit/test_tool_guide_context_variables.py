from __future__ import annotations

from cuga.backend.cuga_graph.policy.models import ToolGuide


def _base_kwargs() -> dict:
    """Return minimal valid kwargs for constructing a ToolGuide."""
    return {
        "id": "my_guide",
        "name": "My Guide",
        "description": "Test guide.",
        "triggers": [],
        "target_tools": ["my_tool"],
        "guide_content": "Use this tool carefully.",
    }


def test_tool_guide_context_variables_defaults_to_empty_dict() -> None:
    """context_variables should default to an empty dict when not provided."""
    policy = ToolGuide(**_base_kwargs())
    assert policy.context_variables == {}


def test_tool_guide_context_variables_accepts_values() -> None:
    """context_variables should store arbitrary key-value pairs when provided."""
    policy = ToolGuide(
        **_base_kwargs(),
        context_variables={"result_count": 5, "stage": "SEARCHING"},
    )
    assert policy.context_variables["result_count"] == 5
    assert policy.context_variables["stage"] == "SEARCHING"


def test_tool_guide_context_variables_does_not_affect_existing_fields() -> None:
    """Setting context_variables should not alter guide_content or other fields."""
    kwargs = _base_kwargs()
    kwargs["guide_content"] = "Found {{ result_count }} result(s)."
    policy = ToolGuide(**kwargs, context_variables={"result_count": 3})
    # guide_content is stored as-is; rendering is the caller's responsibility
    assert "{{ result_count }}" in policy.guide_content
    assert policy.context_variables == {"result_count": 3}
