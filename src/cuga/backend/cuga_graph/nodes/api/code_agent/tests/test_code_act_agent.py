from typing import Any, Dict

from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

from cuga.backend.cuga_graph.nodes.api.code_agent.code_act_agent import create_default_prompt


def create_test_tool_with_nested_schema():
    """Creates a sample tool with a nested object schema for testing."""

    WhenModel = create_model(
        "WhenModel",
        start=(str, Field(..., description="Start time in ISO 8601 format")),
        end=(str, Field(..., description="End time in ISO 8601 format")),
    )

    EventModel = create_model(
        "EventModel",
        title=(str, Field(..., description="Title of the event")),
        when=(WhenModel, Field(..., description="Timing of the event")),
    )

    InputModel = create_model(
        "create_eventInput",
        event=(EventModel, Field(..., description="The event to create")),
    )

    def create_event(event: Dict[str, Any]) -> str:
        """
        Creates a new calendar event.
        """
        return f"Event '{event.get('title')}' created."

    tool = StructuredTool.from_function(
        func=create_event,
        name="create_event",
        description="Creates a new calendar event.",
        args_schema=InputModel,
    )
    return tool


def test_create_default_prompt_includes_json_schema_for_nested_models():
    """Test that create_default_prompt includes nested model schema details."""
    tool = create_test_tool_with_nested_schema()
    prompt = create_default_prompt(tools=[tool])

    assert "The arguments for `create_event` should follow this JSON schema:" in prompt
    assert "```json" in prompt
    assert "EventModel" in prompt
    assert "WhenModel" in prompt
    assert '"event"' in prompt
    assert '"title"' in prompt
    assert '"when"' in prompt
    assert '"start"' in prompt
    assert '"end"' in prompt
    assert "def create_event(event: Dict[str, Any]) -> str:" in prompt
