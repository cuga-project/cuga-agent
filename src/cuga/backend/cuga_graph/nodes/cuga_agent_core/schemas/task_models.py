from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class DecomposedTask(BaseModel):
    task: str = Field(..., description="task")
    app: str = Field(..., description="app name")
    type: Literal["api", "web"] = Field(..., description="app name")


class TaskDecompositionPlan(BaseModel):
    thoughts: str = Field(..., description="your thoughts")
    task_decomposition: List[DecomposedTask] = Field(..., description="the subtask decomposition")

    def format_as_list(self):
        return [
            "{} (type = '{}', app='{}')".format(p.task, p.type, p.app[:30])
            for p in self.task_decomposition
        ]


class AnalyzeTaskOutput(BaseModel):
    attrs: Optional[dict] = None
    paraphrased_intent: Optional[str] = None
    navigation_paths: Optional[dict] = None
    resolved_intent: Optional[str] = None
