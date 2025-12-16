from typing import Optional, List, Any, Dict
from enum import Enum


from pydantic import BaseModel, Field, ConfigDict

from cuga.backend.cuga_graph.utils.nodes_names import ActionIds


class ActionType(str, Enum):
    """Enum for different types of follow-up actions."""

    NATURAL_LANGUAGE = "natural_language"
    BUTTON = "button"
    MULTI_SELECT = "multi_select"
    SINGLE_SELECT = "single_select"
    SELECT = "select"
    TEXT_INPUT = "text_input"
    CONFIRMATION = "confirmation"


class SelectOption(BaseModel):
    """Model for individual select options."""

    value: str = Field(..., description="The value to be returned when this option is selected")
    label: str = Field(..., description="Display text for the option")
    description: Optional[str] = Field(None, description="Additional description for the option")
    disabled: bool = Field(False, description="Whether this option is disabled")


class AdditionalData(BaseModel):
    tool: Optional[Any] = Field(None, description="tool definition optional")


class FollowUpAction(BaseModel):
    """Model for defining follow-up actions that users can take."""

    action_id: str = Field(..., description="Unique identifier for this action")
    action_name: str = Field(..., description="Human-readable name for the action")
    description: str = Field(..., description="Detailed description of what this action does")
    type: ActionType = Field(..., description="Type of interaction for this action")
    callback_url: str = Field(..., description="URL to send the response to when action is triggered")
    # Optional fields based on action type
    additional_data: Optional[AdditionalData] = Field(
        AdditionalData(tool=None), description="additional_data"
    )
    return_to: Optional[str] = Field(None, description="Return to node")
    button_text: Optional[str] = Field(None, description="Text to display on button (for button type)")
    placeholder: Optional[str] = Field(None, description="Placeholder text for input fields")
    options: Optional[List[SelectOption]] = Field(None, description="Available options for select types")
    max_selections: Optional[int] = Field(None, description="Maximum number of selections for multi_select")
    min_selections: Optional[int] = Field(1, description="Minimum number of selections for multi_select")
    required: bool = Field(True, description="Whether this action is required")
    timeout_seconds: Optional[int] = Field(None, description="Timeout for user response in seconds")

    # Validation and constraints
    validation_pattern: Optional[str] = Field(None, description="Regex pattern for text input validation")
    max_length: Optional[int] = Field(None, description="Maximum length for text inputs")
    min_length: Optional[int] = Field(None, description="Minimum length for text inputs")

    # Display properties
    priority: int = Field(1, description="Display priority (higher numbers shown first)")
    icon: Optional[str] = Field(None, description="Icon name or URL for the action")
    color: Optional[str] = Field(
        None, description="Color theme for the action (e.g., 'primary', 'warning', 'success')"
    )

    model_config = ConfigDict(use_enum_values=True)


def create_save_reuse_action():
    return FollowUpAction(
        action_name="Save for later",
        action_id=ActionIds.SAVE_REUSE,
        description="Save the current flow for later use",
        type=ActionType.CONFIRMATION,
        callback_url="/save",
        button_text="Save for later reuse",
    )


def create_flow_approve(tool: Any):
    return FollowUpAction(
        action_name="Approve & Run",
        action_id=ActionIds.FLOW_APPROVE,
        return_to="ChatAgent",
        additional_data=AdditionalData(tool=tool),
        description="Would you like me to run it?",
        type=ActionType.CONFIRMATION,
        callback_url="/save",
        button_text="Run",
    )


def create_new_flow_approve(tool: Any):
    return FollowUpAction(
        action_name="Approve & Run New Flow",
        action_id=ActionIds.NEW_FLOW_APPROVE,
        return_to="ChatAgent",
        additional_data=AdditionalData(tool=tool),
        description="I will run a new flow autonomously",
        type=ActionType.CONFIRMATION,
        callback_url="/save",
        button_text="Run",
    )


def create_get_more_utterances():
    return FollowUpAction(
        action_name="Provide example intents",
        action_id=ActionIds.SAVE_REUSE_INTENT,
        description="Can you provide me with more examples of utterances you would like me to handle next time?",
        type=ActionType.NATURAL_LANGUAGE,
        callback_url="/resume",
    )


class ActionResponse(BaseModel):
    """Model for responses to follow-up actions."""

    action_id: str = Field(..., description="ID of the action this response corresponds to")
    response_type: ActionType = Field(..., description="Type of the original action")
    timestamp: str = Field(..., description="ISO timestamp when response was submitted")
    user_id: Optional[str] = Field(None, description="ID of the user who submitted the response")
    session_id: Optional[str] = Field(None, description="Session ID for tracking")
    additional_data: Optional[AdditionalData] = Field(
        AdditionalData(tool=None), description="additional_data"
    )
    # Response data based on action type
    text_response: Optional[str] = Field(
        None, description="Text response for natural language or text input actions"
    )
    button_clicked: Optional[bool] = Field(None, description="Whether button was clicked (for button type)")
    selected_values: Optional[List[str]] = Field(None, description="Selected values for select types")
    selected_options: Optional[List[SelectOption]] = Field(None, description="Full selected option objects")
    confirmed: Optional[bool] = Field(None, description="Confirmation response (for confirmation type)")

    # Metadata
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata about the response")
    response_time_ms: Optional[int] = Field(None, description="Time taken to respond in milliseconds")
    client_info: Optional[Dict[str, str]] = Field(None, description="Client browser/device information")

    model_config = ConfigDict(use_enum_values=True)
