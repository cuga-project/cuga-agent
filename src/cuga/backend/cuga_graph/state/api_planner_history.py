from typing import List, Optional, Union, Any, Literal
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum
from typing_extensions import Annotated


class ConcludeTaskStatus(str, Enum):
    """Status for the ConcludeTask action (for input_to_agent)."""

    SUCCESS = "success"
    FAILURE = "failure"


class VariableMetadata(BaseModel):
    """Metadata for a variable in CoderAgent's variables_summary."""

    number_of_items: Optional[int] = None
    text_length: Optional[int] = None
    is_large_value: Optional[bool] = Field(default=False)
    is_sensitive: Optional[bool] = Field(default=False)
    preview: Optional[str] = None

    # Allow extra fields in metadata if any, though specific ones are defined
    model_config = ConfigDict(extra='allow')


class VariableSummaryEntry(BaseModel):
    """An entry for a single variable in CoderAgent's variables_summary."""

    type: str
    description: Optional[str] = None
    metadata: Optional[VariableMetadata] = Field(default_factory=dict)  # Handles empty {} metadata
    value: Optional[Any] = None


class CoderAgentHistoricalOutput(BaseModel):
    """Output structure for a CoderAgent action as recorded in history."""

    variables_summary: Optional[str] = None
    final_output: Any


class FilteredApiEntry(BaseModel):
    """Structure for an API entry within ApiFilteringAgent's historical output."""

    app_name: str
    api_name: str
    description: Optional[str]
    reasoning: Optional[str]
    # api_description is not in the example output for filtered_apis, so omitted here


class ApiFilteringAgentHistoricalOutput(BaseModel):
    """Output structure for an ApiFilteringAgent action as recorded in history."""

    filtered_apis: List[FilteredApiEntry] = Field(default_factory=list)


# ConcludeTask output in history is not explicitly defined beyond its input's final_response.
# We can assume agent_output for a ConcludeTask might be its final_response or a simple status.
# For simplicity, we'll use Dict[str, Any] or allow it to be the final_response directly.
# ConcludeTaskHistoricalOutput = Union[str, Dict[str, Any]]
class ConcludeTaskHistoricalOutput(BaseModel):
    """Output structure for an ApiFilteringAgent action as recorded in history."""

    answer: str = Field(description="Final answer")


# --- Main Historical Action Model ---

AgentOutputHistory = Annotated[
    Optional[
        Union[CoderAgentHistoricalOutput, ApiFilteringAgentHistoricalOutput, ConcludeTaskHistoricalOutput]
    ],
    Field(discriminator='agent_type'),
]
#
# class AgentOutputHistoryV1(BaseModel):
#
#


class HistoricalAction(BaseModel):
    """
    Represents a single action entry in the history of actions.
    """

    action_taken: Literal['CoderAgent', 'ConcludeTask', 'ApiShortlistingAgent', 'ConsultWithHuman'] = Field(
        ..., description="The type of action that was performed."
    )
    input_to_agent: Optional[Any] = None
    agent_output: Optional[Any] = None

    # @model_validator(mode='after')
    # def validate_input_and_output_types(self) -> 'HistoricalAction':
    #     """
    #     Validates that 'input_to_agent' and 'agent_output' fields match
    #     the types expected by the 'action_taken' field.
    #     """
    #     action_input_valid = False
    #     agent_output_valid = False
    #
    #     # Validate input_to_agent
    #     expected_input_type = None
    #     if self.action_taken == ActionName.CODER_AGENT:
    #         expected_input_type = CoderAgentInput
    #         if isinstance(self.input_to_agent, CoderAgentInput):
    #             action_input_valid = True
    #     elif self.action_taken == ActionName.API_FILTERING_AGENT:
    #         expected_input_type = ApiShortlistingAgentInput
    #         if isinstance(self.input_to_agent, ApiShortlistingAgentInput):
    #             action_input_valid = True
    #     elif self.action_taken == ActionName.CONCLUDE_TASK:
    #         expected_input_type = ConcludeTaskInput
    #         if isinstance(self.input_to_agent, ConcludeTaskInput):
    #             action_input_valid = True
    #
    #     if not action_input_valid and expected_input_type and isinstance(self.input_to_agent, dict):
    #         try:
    #             self.input_to_agent = expected_input_type(**self.input_to_agent)
    #             action_input_valid = True
    #         except Exception:
    #             pass  # Will be caught by the final check if still invalid
    #
    #     if not action_input_valid:
    #         raise ValueError(
    #             f"For action_taken '{self.action_taken}', 'input_to_agent' is not of the expected type "
    #             f"'{expected_input_type.__name__ if expected_input_type else 'unknown'}'. "
    #             f"Got type '{type(self.input_to_agent).__name__}'."
    #         )
    #
    #     # Validate agent_output
    #     expected_output_type = None
    #     if self.action_taken == ActionName.CODER_AGENT:
    #         expected_output_type = CoderAgentHistoricalOutput
    #         if isinstance(self.agent_output, CoderAgentHistoricalOutput):
    #             agent_output_valid = True
    #     elif self.action_taken == ActionName.API_FILTERING_AGENT:
    #         expected_output_type = ApiFilteringAgentHistoricalOutput
    #         if isinstance(self.agent_output, ApiFilteringAgentHistoricalOutput):
    #             agent_output_valid = True
    #     elif self.action_taken == ActionName.CONCLUDE_TASK:
    #         # ConcludeTaskHistoricalOutput is a Union, direct isinstance is tricky.
    #         # We accept string or dict for ConcludeTask output.
    #         if isinstance(self.agent_output, (str, dict)):
    #             agent_output_valid = True
    #         # If it's a dict, we don't try to parse it into a specific model unless defined.
    #         # For now, this loose check is fine based on prompt.
    #
    #     if (
    #         not agent_output_valid
    #         and expected_output_type
    #         and isinstance(self.agent_output, dict)
    #         and self.action_taken != ActionName.CONCLUDE_TASK
    #     ):
    #         try:
    #             self.agent_output = expected_output_type(**self.agent_output)
    #             agent_output_valid = True
    #         except Exception:
    #             pass
    #
    #     if not agent_output_valid:
    #         # For ConcludeTask, we already validated it's a str or dict.
    #         if self.action_taken != ActionName.CONCLUDE_TASK:
    #             raise ValueError(
    #                 f"For action_taken '{self.action_taken}', 'agent_output' is not of the expected type "
    #                 f"'{expected_output_type.__name__ if expected_output_type else 'unknown'}'. "
    #                 f"Got type '{type(self.agent_output).__name__}'."
    #             )
    #     return self
