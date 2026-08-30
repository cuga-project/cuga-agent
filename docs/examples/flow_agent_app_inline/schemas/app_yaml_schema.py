"""Pydantic schema for flow/app YAML configuration files."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


WorkflowEngineType = Literal["langgraph", "flowable", "kogito"]

TaskMode = Literal["task_agent", "native"]
GatewayMode = Literal["decision_agent", "native"]
HookType = Literal["edge"]
# Built-in agent kinds. Gateways and the flow block accept only these: their agent is the
# one that *decides*, and routing/hook authority stays local by design. TaskAgentConfig
# widens to a plain str so a task may also name a `remote_agents:` key — a task's work is
# the delegation, so handing it out changes nothing about who owns the process.
AgentType = Literal["cuga_agent", "claude_agent", "langgraph", "crewAI"]
HookAction = Literal[
    "continue",
    "skip_node",
    "skip_to",
    "terminate",
    "swap_nodes",
    "remove_node",
    "add_node",
]


class WorkflowEngineConfig(BaseModel):
    """Optional `workflow_engine:` block — selects the engine backing MCPFlowBridge."""

    type: WorkflowEngineType = "langgraph"
    url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    deploy: bool = False
    process_definition_key: Optional[str] = None
    # kogito only — Kogito compiles BPMN in at build time, so there is no `deploy`
    # equivalent; process_id names an already-built process in the running service.
    process_id: Optional[str] = None
    callback_port: int = 8090
    callback_host: str = "host.docker.internal"


class RemoteAgentConfig(BaseModel):
    """A single entry in the `remote_agents:` map, keyed by the name that
    `agent_type:` and `human_consultation:` refer to."""

    url: str
    protocol: Literal["a2a"] = "a2a"
    # Overrides the a2a-sdk default of 30s, which sits below the 120s a Kogito script task
    # allows for an entire control point — leaving it unset silently truncates slow work.
    timeout: Optional[float] = None
    # e.g. {"type": "bearer", "token": "..."}; passed through to the A2A client.
    auth: Optional[dict[str, str]] = None


class FlowBlock(BaseModel):
    """The `flow:` block — process identity and BPMN source."""

    name: str
    id: str
    version: Optional[str] = None
    bpmn_file: Optional[str] = None
    # The FlowAgent's own reasoning agent — genuinely process-wide. Consultation is not
    # declared here: it belongs per hook, on HookConfig.
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
    # An AgentType built-in, or a key of `remote_agents:` to delegate the whole task over
    # A2A instead of building a local CugaAgent. Plain str because a Literal cannot
    # enumerate names the application defines.
    agent_type: str = "cuga_agent"


class TaskConfig(BaseModel):
    """A single entry under `tasks:`."""

    id: str
    mode: TaskMode
    agent: Optional[TaskAgentConfig] = None
    tool: Optional[str] = None
    # Top-level instruction: alternative to agent.system_instruction for native tasks
    # or when agent: section is absent.  flow_config.py reads task.get("instruction").
    instruction: Optional[str] = None
    # Top-level policy path: legacy alternative to agent.policy.
    # flow_config.py reads agent_config.get("policy") or task_config.get("policy").
    policy: Optional[str] = None
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
    # Names a `remote_agents:` key bound as a *tool* on this gateway's DecisionAgent, so it
    # can consult a human before routing. Per-gateway because DecisionAgent is constructed
    # per gateway. The routing decision itself stays local — the remote agent reports, the
    # DecisionAgent still chooses from `flows:`.
    human_consultation: Optional[str] = None


class ActionPermissionsConfig(BaseModel):
    """The `action_permissions:` block."""

    permitted_actions: list[HookAction] = Field(default_factory=list)
    prohibited_actions: list[HookAction] = Field(default_factory=list)


class HookConfig(BaseModel):
    """A single entry under `hooks:`."""

    id: str
    type: HookType
    location: str
    # The hook's prose instruction, plus any `user escalation:` block naming what a human
    # must be asked. Parallels TaskAgentConfig.system_instruction and GatewayConfig.condition
    # — the other two fields a wrapper agent reasons from. Distinct from `message` below,
    # which is the static-fallback HookResult text, and from `condition`, a guard expression.
    instruction: Optional[str] = None
    # Names a `remote_agents:` key bound as a *tool* on this hook's reasoning agent, so the
    # hook can consult a human before deciding. Per hook — a hook needing no human binds
    # nothing. The hook decision itself stays local: the remote agent reports, FlowAgent
    # still produces the HookResult.
    human_consultation: Optional[str] = None
    # LLM-driven: flow_config.py reads policy path and loads markdown for _llm_hook_decision.
    # Required when the hook should reason against a policy; omit for static-action hooks.
    policy: Optional[str] = None
    # Static fallback: used when no policy is present.
    # flow_config.py reads action (default "continue") and message to build a fixed HookResult.
    action: HookAction = "continue"
    message: Optional[str] = None
    # Optional guard expression evaluated against process variables before the hook fires.
    # flow_config.py reads condition and wraps it via _create_condition_function.
    condition: Optional[str] = None
    # Set to false to disable the hook without removing it from config.
    # Used by to_engine_config() when building the engine-consumable hooks list.
    enabled: bool = True


class AppYaml(BaseModel):
    """Root model for a flow/app YAML file."""

    flow: FlowBlock
    workflow_engine: WorkflowEngineConfig = Field(default_factory=WorkflowEngineConfig)
    # Remote agents reachable over A2A, referenced by name from TaskAgentConfig.agent_type
    # (delegation) and from human_consultation on FlowBlock / GatewayConfig (tool binding).
    remote_agents: dict[str, RemoteAgentConfig] = Field(default_factory=dict)
    llm: Optional[LLMConfig] = None
    variables: dict[str, Any] = Field(default_factory=dict)
    tasks: list[TaskConfig] = Field(default_factory=list)
    gateways: Optional[dict[str, GatewayConfig]] = None
    action_permissions: Optional[ActionPermissionsConfig] = None
    hooks: Optional[list[HookConfig]] = None
