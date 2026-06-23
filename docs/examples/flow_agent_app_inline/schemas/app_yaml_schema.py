"""Pydantic schema for flow/app YAML configuration files."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


TaskMode = Literal["task_agent", "native"]
GatewayMode = Literal["decision_agent", "native"]
HookType = Literal["edge"]
AgentType = Literal["cuga_agent", "wxo", "claude_agent", "langgraph", "crewAI"]
HookAction = Literal[
    "continue",
    "skip_node",
    "skip_to",
    "terminate",
    "swap_nodes",
    "remove_node",
    "add_node",
]


class FlowBlock(BaseModel):
    """The `flow:` block — process identity and BPMN source."""

    name: str
    id: str
    version: Optional[str] = None
    bpmn_file: Optional[str] = None
    agent_type: AgentType = "cuga_agent"


class LLMConfig(BaseModel):
    """Optional `llm:` block for provider override."""

    provider: str
    model: str
    temperature: float = 0.0


class TaskAgentConfig(BaseModel):
    """The `agent:` sub-block inside a task_agent task."""

    name: str
    system_instruction: str
    tools: list[str] = Field(default_factory=list)
    policy: Optional[str] = None
    agent_type: AgentType = "cuga_agent"


class TaskConfig(BaseModel):
    """A single entry under `tasks:`."""

    id: str
    mode: TaskMode
    agent: Optional[TaskAgentConfig] = None
    tool: Optional[str] = None
    input_mapping: Optional[dict[str, str]] = None
    output_mapping: Optional[dict[str, str]] = None


class GatewayFlowConfig(BaseModel):
    """A single outgoing flow inside a gateway's `flows:` map."""

    condition: Optional[str] = None
    decision: Optional[str] = None


class GatewayConfig(BaseModel):
    """A single entry in the `gateways:` map (keyed by gateway ID)."""

    mode: GatewayMode
    condition: Optional[str] = None
    policy: Optional[str] = None
    flows: Optional[dict[str, GatewayFlowConfig]] = None
    agent_type: AgentType = "cuga_agent"


class ActionPermissionsConfig(BaseModel):
    """The `action_permissions:` block."""

    permitted_actions: list[HookAction] = Field(default_factory=list)
    prohibited_actions: list[HookAction] = Field(default_factory=list)


class HookConfig(BaseModel):
    """A single entry under `hooks:`."""

    id: str
    type: HookType
    location: str
    policy: str


class AppYaml(BaseModel):
    """Root model for a flow/app YAML file."""

    flow: FlowBlock
    llm: Optional[LLMConfig] = None
    variables: dict[str, Any] = Field(default_factory=dict)
    tasks: list[TaskConfig] = Field(default_factory=list)
    gateways: Optional[dict[str, GatewayConfig]] = None
    action_permissions: Optional[ActionPermissionsConfig] = None
    hooks: Optional[list[HookConfig]] = None
