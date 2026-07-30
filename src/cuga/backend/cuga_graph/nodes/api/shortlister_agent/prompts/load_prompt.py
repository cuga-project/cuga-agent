from typing import List, Literal

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field

from cuga.backend.activity_tracker.tracker import ActivityTracker

tracker = ActivityTracker()


class APIDetails(BaseModel):
    name: str = Field(
        ...,
        description="API Name",
    )
    relevance_score: float = Field(
        ...,
        description="Relevance score",
    )
    reasoning: str = Field(
        ...,
        description="Reasoning",
    )


class ShortListerOutput(BaseModel):
    thoughts: List[str]
    result: List[APIDetails]


class ShortListerOutputLite(BaseModel):
    result: List[APIDetails]


class Metadata(BaseModel):
    estimated_steps: int = Field(
        ...,
        description="Estimated number of steps (clicks or types or answer) needed to perform by the action agent.",
    )
    confidence_level: float = Field(
        ...,
        description="Confidence level in the accuracy of the plan, represented as a float between 0 and 1.",
    )


class NextAgentPlan(BaseModel):
    thoughts: List[str] = Field(
        ...,
        description="A list of step by step thoughts.",
    )
    next_agent: Literal['ActionAgent', 'MemorizeAgent', 'QaAgent', 'ConcludeTaskAgent']
    instruction: str


parser = PydanticOutputParser(pydantic_object=NextAgentPlan)


class ExplicitParameterItem(BaseModel):
    """
    Represents an explicitly stated parameter.
    """

    parameter_name: str = Field(..., description="The name of the explicit parameter.")
    value: str = Field(..., description="The value of the explicit parameter as stated by the user.")


class ImplicitParameterItem(BaseModel):
    """
    Represents an implicitly required parameter.
    """

    parameter_name: str = Field(..., description="The name of the implicit parameter.")
    value_description: str = Field(
        ..., description="A description of what is implicitly needed or assumed for this parameter."
    )


class Parameters(BaseModel):
    """
    Contains lists of explicit and implicit parameters.
    """

    explicit: List[ExplicitParameterItem] = Field(
        default_factory=list, description="A list of explicitly identified parameters."
    )
    implicit: List[ImplicitParameterItem] = Field(
        default_factory=list, description="A list of implicitly identified parameters."
    )


class ParameterAnalysisOutput(BaseModel):
    """
    Defines the overall structure of the LLM's output for parameter extraction.
    """

    thoughts: List[str] = Field(..., description="A list of strings representing the LLM's reasoning steps.")
    parameters: Parameters = Field(
        ..., description="An object containing the extracted explicit and implicit parameters."
    )


class Tool(BaseModel):
    """
    Represents a matching tool with its name and input schema.
    """

    name: str = Field(..., description="The name of the tool.")
    input_: dict = Field(
        ...,
        alias="input",
        description="The input parameters/schema for the tool as a dictionary.",
    )


class FindToolsOutput(BaseModel):
    """
    Output schema for the find_tools function.
    Returns relevant matching tools for a natural language query (no fixed count).
    """

    tools: List[Tool] = Field(
        ...,
        description="Matching tools ordered by relevance to the query. Include all tools needed for the workflow.",
    )


def find_tools(query: str) -> FindToolsOutput:
    """
    Find the relevant matching tools based on a natural language query.

    Args:
        query: A natural language query describing what tools are needed.

    Returns:
        FindToolsOutput: Matching tools (no fixed count), each with a name and input schema.
    """
    # Implementation logic goes here
    pass
