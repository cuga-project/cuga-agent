"""
Flow Configuration - YAML-based configuration for FlowAgent.

This module provides utilities for loading FlowAgent configurations from YAML files,
including BPMN file references, task agent definitions, hooks, and process variables.
"""

import yaml
from typing import Dict, List, Any, Optional
from pathlib import Path
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent import FlowAgent
from cuga.backend.cuga_graph.nodes.cuga_flow.decision_agent import DecisionAgent
from cuga.backend.cuga_graph.nodes.cuga_flow.hook_manager import Hook, HookType, HookAction, HookResult
from cuga.backend.cuga_graph.nodes.cuga_flow.task_agent import TaskAgent
from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent_state import FlowState
from cuga.sdk import CugaAgent


class FlowConfig:
    """
    Configuration for FlowAgent loaded from YAML.

    YAML Structure:
        flow:
          name: "Approval Process"
          bpmn_file: "process.bpmn"

        llm:
          provider: "openai"
          model: "gpt-4"
          temperature: 0.0

        tasks:
          - id: "review_task"
            agent:
              name: "reviewer"
              description: "Reviews documents"
              tools: ["read_file", "search"]

          - id: "approve_task"
            agent:
              name: "approver"
              description: "Approves or rejects"
              tools: ["send_email"]

        hooks:
          - id: "escalation_hook"
            type: "pre_edge"
            location: "flow_to_approve"
            condition: "amount > 10000"
            action: "reform_graph"

        variables:
          max_amount: 10000
          approval_required: true
    """

    def __init__(self, config_dict: Dict[str, Any], config_file_dir: Optional[str] = None):
        """
        Initialize from configuration dictionary.

        Args:
            config_dict: Configuration dictionary (typically loaded from YAML)
            config_file_dir: Directory containing the config file (for resolving relative paths)
        """
        self.config = config_dict
        self.config_file_dir = config_file_dir
        self.config_path: Optional[str] = None  # set by from_yaml()
        self.flow_config = config_dict.get("flow", {})
        self.llm_config = config_dict.get("llm", {})
        self.tasks_config = config_dict.get("tasks", [])
        self.hooks_config = config_dict.get("hooks", [])
        self.variables = config_dict.get("variables", {})
        # gateways section: gateway_id -> {mode, policy, flows: {flow_id -> {condition, ...}}}
        self.gateways_config: Dict[str, Any] = config_dict.get("gateways", {})
        # action_permissions: permitted_actions / prohibited_actions lists for hook enforcement
        self.action_permissions: Dict[str, Any] = config_dict.get("action_permissions", {})

    def _resolve_path(self, path: str) -> Path:
        """Resolve a config-relative path to an absolute Path."""
        p = Path(path)
        if self.config_file_dir and not p.is_absolute():
            return Path(self.config_file_dir) / p
        return p

    @classmethod
    def from_yaml(cls, yaml_file: str) -> "FlowConfig":
        """
        Load configuration from YAML file.

        Args:
            yaml_file: Path to YAML configuration file

        Returns:
            FlowConfig instance
        """
        logger.info(f"Loading flow configuration from: {yaml_file}")

        with open(yaml_file, 'r') as f:
            config_dict = yaml.safe_load(f)

        config_file_dir = str(Path(yaml_file).parent)
        instance = cls(config_dict, config_file_dir)
        instance.config_path = yaml_file
        return instance

    def to_dict(self) -> dict:
        """Serialise to the raw config dict."""
        return self.config

    def to_engine_config(self) -> dict:
        """
        Return the engine-consumable config derived from the YAML annotations.

        Produces the same structure the engine previously received from
        FlowAgent._get_static_config, but sourced directly from the registry
        rather than from FlowAgent's runtime state.
        """
        agentic_task_ids = []
        tool_tasks: Dict[str, Optional[str]] = {}
        task_instructions: Dict[str, str] = {}

        for task in self.tasks_config:
            task_id = task.get("id")
            if not task_id:
                continue
            mode = task.get("mode", "")
            has_agent = bool(task.get("agent"))
            agent_cfg = task.get("agent", {})
            instruction = agent_cfg.get("system_instruction") or task.get("instruction", "")
            if instruction:
                task_instructions[task_id] = instruction
            if mode == "task_agent" or (mode != "tool" and has_agent):
                agentic_task_ids.append(task_id)
            else:
                tool_tasks[task_id] = task.get("tool") or None

        decision_gateway_ids = [
            gw_id for gw_id, gw_cfg in self.gateways_config.items()
            if isinstance(gw_cfg, dict) and gw_cfg.get("mode", "tool") == "decision_agent"
        ]

        flow_conditions: Dict[str, str] = {}
        for gw_cfg in self.gateways_config.values():
            if not isinstance(gw_cfg, dict):
                continue
            for flow_id, flow_cfg in gw_cfg.get("flows", {}).items():
                if isinstance(flow_cfg, dict) and flow_cfg.get("condition"):
                    flow_conditions[flow_id] = flow_cfg["condition"]

        hooks_data = [
            {
                "id": h.get("id"),
                "hook_type": h.get("type", "pre_edge"),
                "location": h.get("location"),
                "enabled": h.get("enabled", True),
            }
            for h in self.hooks_config
            if h.get("id") and h.get("location")
        ]

        return {
            "agentic_task_ids": agentic_task_ids,
            "decision_gateway_ids": decision_gateway_ids,
            "task_instructions": task_instructions,
            "hooks": hooks_data,
            "flow_conditions": flow_conditions,
            "tool_tasks": tool_tasks,
            "action_permissions": self.get_action_permissions(),
        }

    def get_bpmn_file(self) -> str:
        """Get BPMN file path from configuration, resolving relative paths."""
        bpmn_file = self.flow_config.get("bpmn_file")
        if not bpmn_file:
            raise ValueError("No bpmn_file specified in flow configuration")

        return str(self._resolve_path(bpmn_file))

    def get_flow_name(self) -> Optional[str]:
        """Get flow name from configuration."""
        return self.flow_config.get("name")

    def get_flow_id(self) -> Optional[str]:
        """Get flow ID from configuration."""
        return self.flow_config.get("id")

    def get_flow_version(self) -> Optional[str]:
        """Get flow version from configuration."""
        return self.flow_config.get("version")

    def get_model_name(self) -> Optional[str]:
        """
        Get LLM model name from configuration.

        Returns:
            Model name string or None if not configured
        """
        if not self.llm_config:
            return None
        return self.llm_config.get("model")

    def create_task_agents(self) -> Dict[str, TaskAgent]:
        """
        Create TaskAgent instances for tasks declared with mode: task_agent.

        A task uses task_agent mode when:
          - mode: task_agent is explicitly set, OR
          - an agent: sub-section is present (implicit task_agent, backward-compatible).

        Tasks with mode: tool (or no agent:) are handled by create_tool_tasks() instead.

        Returns:
            Dict mapping task_id -> TaskAgent
        """
        task_agents: Dict[str, TaskAgent] = {}

        for task_config in self.tasks_config:
            task_id = task_config.get("id")
            if not task_id:
                logger.warning("Task configuration missing 'id', skipping")
                continue

            mode = task_config.get("mode", "")
            agent_config = task_config.get("agent", {})

            # tool mode: skip — handled by create_tool_tasks()
            if mode == "tool":
                continue
            # task_agent mode: requires agent: section
            if not agent_config:
                continue

            try:
                tools_config = agent_config.get("tools") or []
                cuga_agent = CugaAgent(
                    special_instructions=agent_config.get("system_instruction") or None,
                    tools=tools_config if tools_config else None,
                )
                task_agent = TaskAgent(
                    task_id=task_id,
                    task_name=agent_config.get("name", task_id),
                    agent=cuga_agent,
                    input_mapping=task_config.get("input_mapping"),
                    output_mapping=task_config.get("output_mapping"),
                )
                task_agents[task_id] = task_agent
                logger.info(f"Created TaskAgent for: {task_id}")
            except Exception as e:
                logger.error(f"Error creating TaskAgent for task {task_id}: {e}")

        return task_agents

    def create_tool_tasks(self) -> Dict[str, Optional[str]]:
        """
        Collect tool-mode task declarations from the YAML.

        A task is in tool mode when mode: tool is explicitly set OR when no
        agent: section is present (default).  The optional tool: field names
        the callable in FlowAgent.tools that the node will invoke.

        Returns:
            Dict mapping task_id -> tool_name (None if no tool: field set)
        """
        tool_tasks: Dict[str, Optional[str]] = {}

        for task_config in self.tasks_config:
            task_id = task_config.get("id")
            if not task_id:
                continue

            mode = task_config.get("mode", "")
            has_agent = bool(task_config.get("agent"))

            # Skip tasks that are unambiguously task_agent mode
            if mode == "task_agent" or (mode == "" and has_agent):
                continue

            tool_name: Optional[str] = task_config.get("tool") or None
            tool_tasks[task_id] = tool_name
            logger.info(
                f"Registered tool task: {task_id}"
                + (f" → tool '{tool_name}'" if tool_name else " (pass-through)")
            )

        return tool_tasks

    def create_task_instructions(self) -> Dict[str, str]:
        """
        Extract the task instruction (system_instruction) per task_id from the YAML.

        This is the static description of what the task should accomplish.
        The WorkflowEngine discloses it in ControlPointContext.task_instruction
        at callback time so FlowAgent does not need to re-derive it from the BPMN element.

        Returns:
            Dict mapping task_id -> instruction text string.
        """
        instructions: Dict[str, str] = {}
        for task_config in self.tasks_config:
            task_id = task_config.get("id")
            if not task_id:
                continue
            agent_config = task_config.get("agent", {})
            instruction = agent_config.get("system_instruction") or task_config.get("instruction", "")
            if instruction:
                instructions[task_id] = instruction
        return instructions

    def create_task_policies(self) -> Dict[str, str]:
        """
        Load policy markdown text for each task that declares a policy: path.

        Returns:
            Dict mapping task_id -> policy text string.
        """
        policies: Dict[str, str] = {}

        for task_config in self.tasks_config:
            task_id = task_config.get("id")
            if not task_id:
                continue
            # Policy may live under agent: (preferred) or at task level (legacy)
            agent_config = task_config.get("agent", {})
            policy_path = agent_config.get("policy") or task_config.get("policy", "")
            if not policy_path:
                continue

            resolved = self._resolve_path(policy_path)
            try:
                policies[task_id] = resolved.read_text()
                logger.info(f"Loaded task policy for {task_id} from {resolved}")
            except Exception as e:
                logger.warning(f"Could not load task policy '{resolved}' for {task_id}: {e}")

        return policies

    def create_hooks(self) -> List[Hook]:
        """
        Create hooks from configuration.

        If a hook declares a ``policy:`` path the hook's LLM-based reasoning
        is driven by that policy markdown.  The FlowAgent will call
        ``_llm_hook_decision`` for policy-enabled hooks instead of the static
        handler.

        Returns:
            List of Hook instances
        """
        hooks = []

        for hook_config in self.hooks_config:
            try:
                hook_id = hook_config.get("id")
                hook_type_str = hook_config.get("type", "pre_edge")
                location = hook_config.get("location")
                condition_str = hook_config.get("condition")
                action_str = hook_config.get("action", "continue")
                policy_path = hook_config.get("policy", "")

                if not hook_id or not location:
                    logger.warning("Hook configuration missing 'id' or 'location', skipping")
                    continue

                # Parse hook type
                hook_type = HookType[hook_type_str.upper()] if hook_type_str else HookType.PRE_EDGE

                # Parse action (used only when no policy is present)
                action = HookAction[action_str.upper()] if action_str else HookAction.CONTINUE

                # Create condition function if specified
                condition_func = None
                if condition_str:
                    condition_func = self._create_condition_function(condition_str)

                # Static fallback handler — used when no policy drives LLM reasoning
                handler = self._create_hook_handler(action, hook_config.get("message"))

                # Load policy text for LLM-based hook reasoning
                policy_text: Optional[str] = None
                if policy_path:
                    resolved = self._resolve_path(policy_path)
                    try:
                        policy_text = resolved.read_text()
                        logger.info(f"Loaded hook policy for {hook_id} from {resolved}")
                    except Exception as e:
                        logger.warning(f"Could not load hook policy '{resolved}' for {hook_id}: {e}")

                hook = Hook(
                    id=hook_id,
                    hook_type=hook_type,
                    location=location,
                    handler=handler,
                    condition=condition_func,
                    policy=policy_text,
                )

                hooks.append(hook)
                logger.info(
                    f"Created hook: {hook_id} at {location}" + (" (policy-driven)" if policy_text else "")
                )

            except Exception as e:
                logger.error(f"Error creating hook: {e}")

        return hooks

    def create_gateway_agents(self) -> Dict[str, DecisionAgent]:
        """
        Create one DecisionAgent per gateway configured with mode: decision_agent.

        Returns:
            Dict mapping gateway_id -> DecisionAgent instance.
            Gateways with mode: tool (or no mode) are not included.
        """
        agents: Dict[str, DecisionAgent] = {}

        for gateway_id, gw_cfg in self.gateways_config.items():
            if not isinstance(gw_cfg, dict):
                continue
            if gw_cfg.get("mode", "tool") != "decision_agent":
                continue

            policy_path = gw_cfg.get("policy", "")
            policy_text = ""
            if policy_path:
                resolved = self._resolve_path(policy_path)
                try:
                    policy_text = resolved.read_text()
                    logger.info(f"Loaded policy for {gateway_id} from {resolved}")
                except Exception as e:
                    logger.warning(f"Could not load policy file '{resolved}': {e}")

            gateway_condition = gw_cfg.get("condition") or None
            flow_decisions: Dict[str, str] = {
                flow_id: flow_cfg["decision"]
                for flow_id, flow_cfg in gw_cfg.get("flows", {}).items()
                if isinstance(flow_cfg, dict) and "decision" in flow_cfg
            }
            model_name = self.get_model_name()
            agents[gateway_id] = DecisionAgent(
                gateway_id=gateway_id,
                policy=policy_text,
                condition=gateway_condition,
                flow_decisions=flow_decisions or None,
                model_name=model_name,
            )
            logger.info(
                f"Created DecisionAgent for gateway: {gateway_id}"
                + (f" (condition: {gateway_condition})" if gateway_condition else "")
            )

        return agents

    def create_flow_conditions(self) -> Dict[str, str]:
        """
        Extract flow condition expressions from the gateways config section.

        These override (or supplement) conditions embedded in the BPMN XML,
        allowing conditions to live in YAML rather than in the diagram.

        Returns:
            Dict mapping flow_id -> condition expression string.
        """
        conditions: Dict[str, str] = {}

        for gw_cfg in self.gateways_config.values():
            if not isinstance(gw_cfg, dict):
                continue
            for flow_id, flow_cfg in gw_cfg.get("flows", {}).items():
                if isinstance(flow_cfg, dict) and flow_cfg.get("condition"):
                    conditions[flow_id] = flow_cfg["condition"]

        return conditions

    def _create_condition_function(self, condition_str: str):
        """Create a condition function from string expression."""
        from cuga.backend.cuga_graph.nodes.cuga_flow.decision_agent import eval_condition

        def condition(state: FlowState) -> bool:
            return eval_condition(condition_str, state)

        return condition

    def _create_hook_handler(self, action: HookAction, message: Optional[str] = None):
        """Create a hook handler function."""

        def handler(state: FlowState) -> HookResult:
            return HookResult(action=action, message=message or f"Hook action: {action.value}")

        return handler

    def get_action_permissions(self) -> Dict[str, List[str]]:
        """
        Get permitted and prohibited hook action lists from configuration.

        Returns:
            Dict with keys 'permitted_actions' and 'prohibited_actions' (each a list of str).
        """
        return {
            "permitted_actions": self.action_permissions.get("permitted_actions", []),
            "prohibited_actions": self.action_permissions.get("prohibited_actions", []),
        }

    def get_initial_variables(self) -> Dict[str, Any]:
        """Get initial process variables from configuration."""
        return self.variables.copy()

    def to_flow_agent(self) -> FlowAgent:
        """
        Create a FlowAgent instance from this configuration via ProcessRegistry.

        Returns:
            Configured FlowAgent instance
        """
        if not self.config_path:
            raise ValueError(
                "to_flow_agent() requires a FlowConfig loaded via from_yaml(); "
                "use load_flow_from_yaml() or FlowConfig.from_yaml()."
            )

        from cuga.backend.cuga_graph.nodes.cuga_flow.process_registry import ProcessRegistry
        from cuga.backend.server.cuga_flo_mcp.bridge import MCPFlowBridge
        from cuga.backend.cuga_graph.nodes.cuga_flow.langgraph_engine import LangGraphWorkflowEngine

        bridge = MCPFlowBridge(name="cuga-flo-bridge")
        registry = ProcessRegistry(bridge=bridge)
        process_key = bridge.load_flow(self.config_path)
        flow_agent = FlowAgent(process_key=process_key, bridge=bridge)
        LangGraphWorkflowEngine(bridge=bridge)

        return flow_agent

    def to_ordo_flow_agent(self, ordo_workflow_id: str) -> FlowAgent:
        """
        Create a FlowAgent wired to MCPOrdo via the MCP2MCPMediator.

        OrdoEngine is the stub workflow engine served by MCPOrdo.
        MCP2MCPMediator is registered in both MCP servers:
          • MCPFlowBridge — provides run_process (via _MediatorEngineAdapter)
          • MCPOrdo       — provides execute_task_proxy / route_gateway_proxy /
                            evaluate_hook_proxy

        Args:
            ordo_workflow_id: Workflow ID served by OrdoEngine inside MCPOrdo
                              (e.g. 'loan_approval_stub').

        Returns:
            Configured FlowAgent backed by MCPOrdo + MCP2MCPMediator.
        """
        from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNProcess
        from cuga.backend.server.cuga_flo_mcp.bridge import MCPFlowBridge
        from cuga.backend.server.cuga_flo_mcp.mediator import MCP2MCPMediator
        from cuga.backend.server.cuga_flo_mcp.ordo import MCPOrdo

        bridge = MCPFlowBridge(name="cuga-flo-bridge-ordo")

        task_agents = self.create_task_agents()
        task_policies = self.create_task_policies()
        initial_variables = self.get_initial_variables()

        stub_bpmn = BPMNProcess(
            id=ordo_workflow_id, name=ordo_workflow_id, elements={}, flows=[]
        )

        # FlowAgent registers execute_task / route_gateway / evaluate_hook on bridge.
        # Bridge passed explicitly → no auto LangGraphWorkflowEngine.
        flow_agent = FlowAgent(
            bpmn_process=stub_bpmn,
            task_agents=task_agents,
            task_policies=task_policies,
            process_variables=initial_variables,
            bridge=bridge,
        )

        # MCPOrdo serves OrdoEngine (the stub workflow engine).
        ordo = MCPOrdo()

        # MCP2MCPMediator registers in BOTH servers:
        #   bridge side → run_process (via _MediatorEngineAdapter)
        #   ordo side   → execute_task_proxy / route_gateway_proxy / evaluate_hook_proxy
        mediator = MCP2MCPMediator(bridge, ordo, ordo_workflow_id)
        mediator.register()

        logger.info(
            f"FlowConfig: ordo FlowAgent ready — workflow_id='{ordo_workflow_id}', "
            f"{len(task_agents)} task agent(s)"
        )
        return flow_agent


def load_ordo_flow_from_yaml(yaml_file: str, ordo_workflow_id: str) -> FlowAgent:
    """
    Load a FlowAgent backed by MCPOrdo from a YAML task-only configuration.

    Args:
        yaml_file: Path to the ordo_config.yaml (tasks only, no bpmn_file needed).
        ordo_workflow_id: Workflow ID registered in MCPOrdo (e.g. 'loan_approval_stub').

    Returns:
        Configured FlowAgent wired to MCPOrdo + MCP2MCPMediator.
    """
    config = FlowConfig.from_yaml(yaml_file)
    return config.to_ordo_flow_agent(ordo_workflow_id)


def load_flow_from_yaml(yaml_file: str) -> FlowAgent:
    """
    Convenience function to load a FlowAgent from YAML configuration.

    Args:
        yaml_file: Path to YAML configuration file

    Returns:
        Configured FlowAgent instance

    Example:
        flow_agent = load_flow_from_yaml("approval_process.yaml")
        result = await flow_agent.invoke("Start approval for $15000 purchase")
    """
    config = FlowConfig.from_yaml(yaml_file)
    return config.to_flow_agent()


def load_flow_app(app_folder: str) -> FlowAgent:
    """
    Auto-discover and load a FlowAgent from an app folder.

    Expects the folder to contain a ``config/`` sub-directory with exactly one
    YAML file that has a ``flow:`` section and references the BPMN file.  All
    policy paths inside the YAML are resolved relative to the ``config/``
    directory, so the layout is self-contained:

        <app_folder>/
        └── config/
            ├── process.bpmn
            ├── process_config.yaml
            └── policies/
                └── *.md

    Args:
        app_folder: Path to the app directory (parent of ``config/``).

    Returns:
        Configured FlowAgent instance.

    Raises:
        FileNotFoundError: If ``config/`` directory does not exist.
        ValueError: If zero or more than one YAML config file is found.
    """
    config_dir = Path(app_folder) / "config"
    if not config_dir.is_dir():
        raise FileNotFoundError(f"config/ directory not found in: {app_folder}")

    yaml_files = list(config_dir.glob("*.yaml"))
    # Pick the YAML that contains a flow: section
    flow_yamls = []
    for yf in yaml_files:
        try:
            with open(yf) as f:
                doc = yaml.safe_load(f)
            if isinstance(doc, dict) and "flow" in doc:
                flow_yamls.append(yf)
        except Exception:
            pass

    if not flow_yamls:
        raise ValueError(f"No YAML file with a 'flow:' section found in: {config_dir}")
    if len(flow_yamls) > 1:
        raise ValueError(
            f"Multiple flow YAML files found in {config_dir}: "
            f"{[p.name for p in flow_yamls]}. Pass the explicit file path instead."
        )

    yaml_file = str(flow_yamls[0])
    logger.info(f"load_flow_app: discovered config '{yaml_file}'")
    return load_flow_from_yaml(yaml_file)


# Made with Bob
