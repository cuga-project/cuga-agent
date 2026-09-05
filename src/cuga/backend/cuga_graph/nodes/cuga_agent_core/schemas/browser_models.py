from typing import List, Literal

from pydantic import BaseModel, Field


class NextAgentPlan(BaseModel):
    thoughts: List[str] = Field(..., description="A list of step by step thoughts.")
    next_agent: Literal["ActionAgent", "MemorizeAgent", "QaAgent", "ConcludeTaskAgent"]
    instruction: str
