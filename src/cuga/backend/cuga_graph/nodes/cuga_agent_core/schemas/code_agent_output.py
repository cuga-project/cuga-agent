from typing import List, Optional

from pydantic import BaseModel, Field


class CodeAgentOutput(BaseModel):
    code: str
    execution_output: str
    steps_summary: List[str] = Field(default_factory=list)
    summary: str
    variables: Optional[dict] = None
