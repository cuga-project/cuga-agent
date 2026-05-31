"""Pydantic schema for supervisor YAML configuration files."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


AgentType = Literal["flow_agent", "flow_agent_ordo"]


class SupervisorConfig(BaseModel):
    """Top-level supervisor block."""

    model: Optional[str] = None
    description: str
    special_instructions: str = ""


class AgentConfig(BaseModel):
    """A single agent entry under `agents:`."""

    name: str
    type: AgentType
    flow_config: str
    description: str = ""
    ordo_workflow_id: Optional[str] = None


class SupervisorYaml(BaseModel):
    """Root model for a supervisor YAML file."""

    supervisor: SupervisorConfig
    agents: list[AgentConfig] = Field(default_factory=list)
