from typing import List

from pydantic import BaseModel, Field


class APIDetails(BaseModel):
    name: str = Field(..., description="API Name")
    relevance_score: float = Field(..., description="Relevance score")
    reasoning: str = Field(..., description="Reasoning")


class ShortListerOutputLite(BaseModel):
    result: List[APIDetails]


class Tool(BaseModel):
    name: str = Field(..., description="The name of the tool.")
    input_: dict = Field(..., alias="input", description="The input parameters/schema for the tool.")


class FindToolsOutput(BaseModel):
    tools: List[Tool] = Field(
        ...,
        description="Matching tools ordered by relevance to the query.",
    )
