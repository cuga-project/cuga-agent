"""Schemas for the external workflow MCP lifecycle contract."""

from typing import Any, Literal

from pydantic import BaseModel, Field


RunLifecycleStatus = Literal["running", "paused", "completed", "stopped", "failed"]


class WorkflowSummary(BaseModel):
    """A registered workflow exposed by a remote workflow engine."""

    workflow_id: str
    name: str
    description: str = ""


class WorkflowList(BaseModel):
    """List of workflows available on the remote workflow engine."""

    workflows: list[WorkflowSummary] = Field(default_factory=list)


class WorkflowContext(BaseModel):
    """Serializable workflow pause context supplied by the remote engine."""

    vars: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)


class AgentGoal(BaseModel):
    """Instruction for CUGA FLO when a remote workflow pauses for agent input."""

    agent_name: str
    workflow_session_id: str
    context: WorkflowContext = Field(default_factory=WorkflowContext)


class RunResult(BaseModel):
    """Result returned by run/resume workflow lifecycle calls."""

    final_response: str | None = None
    agent_goal: AgentGoal | None = None


class UploadResult(BaseModel):
    """Result returned after registering a workflow definition."""

    workflow_id: str
    name: str
    description: str = ""
    registered: bool = True


class StopResult(BaseModel):
    """Result returned after stopping a workflow session."""

    session_id: str
    stopped: bool
    forced: bool = False
    status: RunLifecycleStatus = "stopped"
    message: str = ""


class SingleRunStatus(BaseModel):
    """Status for one remote workflow session."""

    session_id: str
    workflow_id: str
    status: RunLifecycleStatus
    final_response: str | None = None
    agent_goal: AgentGoal | None = None


class RunStatus(BaseModel):
    """Status response for one or more remote workflow sessions."""

    runs: list[SingleRunStatus] = Field(default_factory=list)
