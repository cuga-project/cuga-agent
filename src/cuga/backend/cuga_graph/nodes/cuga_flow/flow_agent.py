"""
FlowAgent - Agent-binding harness for BPMN process orchestration.

Implements the TDF Model (Task-Decision-Flow) harness:
  - Holds DURABLE state: task_id→TaskAgent (with tooling), gateway_id→DecisionAgent,
    hooks, task_policies, and action permissions — loaded from YAML at init.
  - Does NOT hold: BPMNProcess model, FlowState, execution history, or compiled graph.
    All of these live inside the WorkflowEngine during execution.

Communication with the WorkflowEngine happens exclusively via MCPFlowBridge.
FlowAgent registers its control-point tools on the bridge; invoke() calls the
engine's run_process MCP tool.  No ControlOverlay is passed between the two sides.

The engine discloses at each control point:
  - current_state, execution_history, process_model_summary  (engine provides)
  - task_instruction (WHAT the task should accomplish)        (engine discloses from YAML)

FlowAgent contributes:
  - The right TaskAgent / DecisionAgent for the element      (WHO does it + tooling)
  - Policy text and hook decisions                           (HOW it is governed)
"""

import json
from typing import Any, Callable, Dict, List, Optional, Union

from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_flow.decision_agent import DecisionAgent
from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent_state import FlowState
from cuga.backend.cuga_graph.nodes.cuga_flow.hook_manager import (
    Hook,
    HookAction,
    HookResult,
)
from cuga.backend.cuga_graph.nodes.cuga_flow.task_agent import TaskAgent
from cuga.backend.cuga_graph.nodes.cuga_flow.workflow_engine import ControlPointContext
from cuga.backend.activity_tracker.tracker import ActivityTracker, Step

tracker = ActivityTracker()


class FlowAgent:
    """
    Agent-binding harness for BPMN-driven process execution.

    Holds which agent handles which task (and with what tools), which decision
    agent handles each gateway, and which hooks govern each flow edge.
    Does NOT hold the process model, compiled graph, or execution state.

    Communication with the WorkflowEngine uses MCPFlowBridge — both sides register
    their services on a shared FastMCP server and call each other via MCP tool calls.

    Usage (registry-based — recommended):
        registry = ProcessRegistry()
        registry.register_from_directory("flow_agent_app_inline/")
        agent = FlowAgent(process_key="receive_order", registry=registry)
        state = await agent.invoke("Order ID 42, priority HIGH")

    Usage (backward-compatible, direct bpmn_file):
        agent = FlowAgent(bpmn_file="process.bpmn", task_agents={...}, ...)
        state = await agent.invoke("Start approval for $15 000")
    """

    def __init__(
        self,
        # ── Primary interface (registry-based) ──────────────────────────────
        process_key: Optional[str] = None,
        registry: Optional[Any] = None,  # ProcessRegistry, typed as Any to avoid circular import
        bridge: Optional[Any] = None,  # MCPFlowBridge; created automatically if None
        # ── Direct / backward-compatible interface ──────────────────────────
        bpmn_file: Optional[str] = None,
        bpmn_process: Optional[Any] = None,  # BPMNProcess
        model_name: Optional[str] = None,
        # ── Agent / tool bindings (overrides for both modes) ─────────────────
        task_agents: Optional[Dict[str, TaskAgent]] = None,
        tool_tasks: Optional[Dict[str, Optional[str]]] = None,
        gateway_agents: Optional[Dict[str, DecisionAgent]] = None,
        flow_conditions: Optional[Dict[str, str]] = None,
        task_policies: Optional[Dict[str, str]] = None,
        task_instructions: Optional[Dict[str, str]] = None,
        tools: Optional[Dict[str, Callable]] = None,
        hooks: Optional[List[Hook]] = None,
        process_variables: Optional[Dict[str, Any]] = None,
        action_permissions: Optional[Dict[str, List[str]]] = None,
    ):
        if process_key and registry:
            # ── Registry-based init ─────────────────────────────────────────
            self.process_key = process_key
            self.registry = registry

            config = registry.get_flow_config(process_key)
            self.task_agents: Dict[str, TaskAgent] = task_agents or config.create_task_agents()
            self.tool_tasks: Dict[str, Optional[str]] = tool_tasks or config.create_tool_tasks()
            self.gateway_agents: Dict[str, DecisionAgent] = gateway_agents or config.create_gateway_agents()
            self.flow_conditions: Dict[str, str] = flow_conditions or config.create_flow_conditions()
            self.task_policies: Dict[str, str] = task_policies or config.create_task_policies()
            self.task_instructions: Dict[str, str] = task_instructions or config.create_task_instructions()
            self.tools: Dict[str, Callable] = tools or {}
            _perms = action_permissions or config.get_action_permissions()
            self.hooks: List[Hook] = hooks or config.create_hooks()
            self.initial_variables: Dict[str, Any] = config.get_initial_variables()
            self.model_name: Optional[str] = model_name or config.get_model_name()

        elif bpmn_file or bpmn_process:
            # ── Backward-compatible direct init ────────────────────────────
            from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNParser
            from cuga.backend.cuga_graph.nodes.cuga_flow.process_registry import (
                ProcessDefinition,
                ProcessRegistry,
            )

            _proc = BPMNParser().parse_file(bpmn_file) if bpmn_file else bpmn_process
            if _proc is None:
                raise ValueError("bpmn_process must not be None")

            _reg = ProcessRegistry()
            _key = _proc.id or "anonymous"
            _reg._definitions[_key] = ProcessDefinition(
                key=_key,
                name=_proc.name or _key,
                bpmn_path=bpmn_file or "",
                config_path="",
                policies_dir="",
            )
            _reg._bpmn_cache[_key] = _proc

            self.process_key = _key
            self.registry = _reg
            self.task_agents = task_agents or {}
            self.tool_tasks = tool_tasks or {}
            self.gateway_agents = gateway_agents or {}
            self.flow_conditions = flow_conditions or {}
            self.task_policies = task_policies or {}
            self.task_instructions = task_instructions or {}
            self.tools = tools or {}
            self.hooks = hooks or []
            self.initial_variables = process_variables or {}
            self.model_name = model_name
            _perms = action_permissions or {}

        else:
            raise ValueError("Either (process_key + registry) or (bpmn_file / bpmn_process) must be provided")

        _perms_dict = _perms if isinstance(_perms, dict) else {}
        self._permitted_actions: List[str] = _perms_dict.get("permitted_actions", [])
        self._prohibited_actions: List[str] = _perms_dict.get("prohibited_actions", [])

        # Resolve LLM once for hook policy reasoning
        from cuga.backend.llm.models import LLMManager
        from cuga.config import settings

        self._llm = LLMManager().get_model(settings.agent.planner.model)

        # ── MCP bridge setup ────────────────────────────────────────────────
        # If a bridge is provided (shared/external), the caller is responsible for
        # registering the engine on it.  If created here, we wire everything automatically.
        from cuga.backend.server.cuga_flo_mcp.bridge import MCPFlowBridge
        from cuga.backend.cuga_graph.nodes.cuga_flow.langgraph_engine import LangGraphWorkflowEngine

        self.bridge: MCPFlowBridge = bridge or MCPFlowBridge()
        if bridge is None:
            self.bridge.register_registry(self.registry)
        self.bridge.register_flow_agent(self)
        if bridge is None:
            LangGraphWorkflowEngine(bridge=self.bridge)

        logger.info(
            f"FlowAgent initialised for process '{self.process_key}' "
            f"— {len(self.task_agents)} task agents, "
            f"{len(self.gateway_agents)} gateway agents, "
            f"{len(self.hooks)} hooks"
        )

    # ──────────────────────────────────────────────────────────────
    # MCP tool handlers (called by MCPFlowBridge tools)
    # ──────────────────────────────────────────────────────────────

    async def _handle_task(self, task_id: str, ctx: ControlPointContext) -> dict:
        """Execute an agentic task.  ENGINE provides WHAT; FlowAgent provides WHO+HOW."""
        agent = self.task_agents.get(task_id)
        if agent is None:
            return {
                "execution_path": [task_id],
                "process_variables": ctx.current_state.process_variables,
                "task_results": {task_id: {"status": "failed", "error": f"No agent for task {task_id!r}"}},
            }

        task_input = self._build_task_input(task_id, ctx)
        tracker.collect_step(
            Step(
                name=f"Task: {ctx.element_name or task_id} — delegating",
                data=task_input,
            )
        )
        result = await agent.execute(ctx.current_state, task_input)
        output = result.get("output", result.get("error", ""))
        tracker.collect_step(Step(name=ctx.element_name or task_id, data=str(output)))
        return {
            "execution_path": [task_id],
            "process_variables": ctx.current_state.process_variables,
            "task_results": {task_id: result},
        }

    async def _handle_gateway(self, gateway_id: str, ctx: ControlPointContext) -> str:
        """Route a gateway via DecisionAgent."""
        agent = self.gateway_agents.get(gateway_id)
        flows = ctx.available_flows or []
        if agent is None:
            return flows[0].id if flows else ""
        try:
            flow_id = await agent.route(flows, ctx.current_state)
            tracker.collect_step(
                Step(
                    name=f"Gateway {ctx.element_name or gateway_id}",
                    data=f"Routed to flow: {flow_id}",
                )
            )
            return flow_id
        except Exception as e:
            logger.error(f"  DecisionAgent error for {gateway_id}: {e}")
            return flows[0].id if flows else ""

    async def _handle_hook(self, hook: Hook, ctx: ControlPointContext) -> HookResult:
        """Evaluate a hook — check condition, then apply policy or handler."""
        if hook.condition and not hook.condition(ctx.current_state):
            return HookResult(action=HookAction.CONTINUE)
        if hook.policy:
            result = self._llm_hook_decision(hook, ctx)
        elif hook.handler:
            result = hook.handler(ctx.current_state)
        else:
            result = HookResult(action=HookAction.CONTINUE)
        tracker.collect_step(
            Step(
                name=f"Hook: {hook.id}",
                data=result.message or result.action.value,
            )
        )
        return result

    def _get_static_config(self) -> dict:
        """Return static config the engine needs at graph-build time."""
        hooks_data = [
            {
                "id": h.id,
                "hook_type": h.hook_type.value,
                "location": h.location,
                "enabled": h.enabled,
            }
            for h in self.hooks
        ]
        return {
            "agentic_task_ids": list(self.task_agents.keys()),
            "decision_gateway_ids": list(self.gateway_agents.keys()),
            "task_instructions": dict(self.task_instructions),
            "hooks": hooks_data,
            "flow_conditions": dict(self.flow_conditions),
            "tool_tasks": {k: v for k, v in self.tool_tasks.items()},
            "action_permissions": {
                "permitted_actions": list(self._permitted_actions),
                "prohibited_actions": list(self._prohibited_actions),
            },
        }

    # ──────────────────────────────────────────────────────────────
    # Task input builder
    # ──────────────────────────────────────────────────────────────

    def _build_task_input(self, task_id: str, ctx: ControlPointContext) -> str:
        """Assemble the task input string from engine-provided context and FlowAgent policy."""
        parts: List[str] = []
        if ctx.task_instruction:
            parts.append(f"Task instruction: {ctx.task_instruction}")
        policy = self.task_policies.get(task_id)
        if policy:
            parts.append(f"\n## Task Policy\n{policy}")
        state = ctx.current_state
        user_message = state.process_variables.get("_user_message", "")
        if user_message:
            parts.append(f"\n## Request\n{user_message}")
        visible_vars = {k: v for k, v in state.process_variables.items() if not k.startswith("_")}
        if visible_vars:
            parts.append(f"\n## Process Variables\n{visible_vars}")
        if state.task_results:
            parts.append(f"\n## Previous Task Results\n{state.task_results}")
        return "\n".join(parts)

    # ──────────────────────────────────────────────────────────────
    # Hook policy reasoning
    # ──────────────────────────────────────────────────────────────

    def _llm_hook_decision(self, hook: Hook, ctx: ControlPointContext) -> HookResult:
        """
        FlowAgent meta-agent reasoning: use the hook policy + engine-provided context
        to decide what structural intervention (if any) to apply.
        """
        from langchain_core.messages import HumanMessage

        remaining_tasks = {
            eid: info.get("name", eid)
            for eid, info in ctx.process_model_summary.get("elements", {}).items()
            if "task" in info.get("type", "").lower() and eid not in ctx.execution_history
        }

        state = ctx.current_state
        prompt = f"""You are the FlowAgent — a meta-agent overseeing a BPMN process execution.
You have intercepted the flow at edge '{ctx.edge_id}' via hook '{hook.id}'.
Your task is to assess the current process state against the hook policy and decide
what structural action (if any) the FlowAgent should apply.

## Hook Policy
{hook.policy}

## Current Process State
- Execution path so far: {ctx.execution_history}
- Process variables: {state.process_variables}
- Task results: {
            {
                k: v.get('output', v.get('error', v)) if isinstance(v, dict) else v
                for k, v in state.task_results.items()
            }
        }

## Remaining Unexecuted Tasks (valid SKIP_TO targets)
{json.dumps(remaining_tasks, indent=2)}

## Available Actions
- "continue"            — process complies with policy; proceed normally
- "skip_node"           — skip the immediate next node only
- "skip_to"             — jump directly to a task from the list above (provide target_node)
- "swap_nodes"          — swap two nodes: provide node_a and node_b
- "request_user_input"  — pause and ask the user a question (provide user_prompt)
- "terminate"           — halt the process immediately
- "remove_node"         — remove a specific node from the execution path (provide remove_node)
- "add_node"            — insert a new task node before the next node (provide add_node with node_id and task_instruction)

Respond ONLY with a JSON object:
{{
  "action": "<action>",
  "target_node": "<node_id or null>",
  "node_a": "<first node_id for swap_nodes or null>",
  "node_b": "<second node_id for swap_nodes or null>",
  "user_prompt": "<question or null>",
  "remove_node": "<node_id to remove or null>",
  "add_node": {{"node_id": "<new node_id or null>", "task_instruction": "<instruction or null>"}},
  "reason": "<one sentence explanation>",
  "state_updates": {{}}
}}
"""
        try:
            response = self._llm.invoke([HumanMessage(content=prompt)])
            raw = response.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()
            if not raw.startswith("{"):
                start, end = raw.find("{"), raw.rfind("}")
                if start != -1 and end > start:
                    raw = raw[start : end + 1]

            decision = json.loads(raw)
            action_str = decision.get("action", "continue").lower().replace(" ", "_")
            action = HookAction(action_str)
            reason = decision.get("reason", "")

            logger.info(f"  LLM hook decision for {hook.id}: {action.value} — {reason}")
            detail = f"Action: {action.value}"
            if decision.get("target_node"):
                detail += f" → {decision['target_node']}"
            if reason:
                detail += f"\nReason: {reason}"
            tracker.collect_step(Step(name=f"Hook {hook.id}: policy reasoning", data=detail))

            node_a = decision.get("node_a")
            node_b = decision.get("node_b")
            state_updates = decision.get("state_updates") or {}
            add_node = decision.get("add_node")
            return HookResult(
                action=action,
                message=reason,
                skip_to_node=decision.get("target_node"),
                user_prompt=decision.get("user_prompt"),
                swap_nodes=(node_a, node_b) if node_a and node_b else None,
                state_updates=state_updates if isinstance(state_updates, dict) else {},
                remove_node=decision.get("remove_node"),
                add_node=add_node if isinstance(add_node, dict) else None,
            )
        except Exception as e:
            logger.error(f"  LLM hook decision failed for {hook.id}: {e}; defaulting to CONTINUE")
            return HookResult(action=HookAction.CONTINUE, message=f"LLM error: {e}")

    # ──────────────────────────────────────────────────────────────
    # Invocation
    # ──────────────────────────────────────────────────────────────

    async def invoke(
        self,
        input_data: Union[str, Dict[str, Any]],
        process_variables: Optional[Dict[str, Any]] = None,
    ) -> FlowState:
        """
        Execute the BPMN process via the MCP bridge.

        Calls the engine's run_process MCP tool; the engine calls back into FlowAgent
        via execute_task / route_gateway / evaluate_hook tools on the same bridge.

        Args:
            input_data: User message (str) or key/value dict merged into process_variables.
            process_variables: Additional process variables (override initial values).

        Returns:
            Terminal FlowState after the process completes or halts.
        """
        from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNProcess

        initial_inputs: Dict[str, Any] = dict(self.initial_variables)
        initial_inputs.update(process_variables or {})
        if isinstance(input_data, str):
            initial_inputs["_user_message"] = input_data
        elif isinstance(input_data, dict):
            initial_inputs.update(input_data)

        try:
            async with self.bridge.get_client() as c:
                bpmn_data = await c.call_tool("get_bpmn_process", {"process_key": self.process_key})
                bpmn_process = BPMNProcess.from_dict(bpmn_data.data)
                logger.info(f"Invoking process '{bpmn_process.name}' via MCPFlowBridge")
                result = await c.call_tool(
                    "run_process",
                    {"process_key": self.process_key, "initial_inputs": initial_inputs},
                )
            final_state = FlowState.model_validate(result.data)
            if not final_state.is_halted:
                final_state.mark_complete()
            summary = self._build_completion_message(final_state, bpmn_process)
            if final_state.messages is None:
                final_state.messages = []
            elif not isinstance(final_state.messages, list):
                final_state.messages = list(final_state.messages)
            final_state.messages.append({"role": "assistant", "content": summary})
            logger.info(f"Process execution completed: is_complete={final_state.is_complete}")
            return final_state
        except Exception as e:
            logger.error(f"Error executing process: {e}")
            error_state = FlowState(
                process_id=bpmn_process.id,
                process_name=bpmn_process.name,
                process_variables=initial_inputs,
            )
            error_state.halt(f"Execution error: {str(e)}")
            error_state.messages = [{"role": "assistant", "content": f"Workflow failed: {str(e)}"}]
            return error_state

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    def _build_completion_message(self, state: FlowState, process: Any) -> str:
        parts = [f"Workflow '{state.process_name}' completed."]
        if state.is_halted:
            parts.append(f"Process was halted: {state.halt_reason}")
            return "\n".join(parts)
        for task_id, result in state.task_results.items():
            element = process.elements.get(task_id) if process else None
            task_name = element.name if element else task_id
            status = result.get("status", "unknown")
            if status == "completed" and "output" in result:
                parts.append(f"\n{task_name}:\n{result['output']}")
            elif status == "failed":
                parts.append(f"\n{task_name}: Failed — {result.get('error', 'unknown error')}")
            elif status == "skipped":
                parts.append(f"\n{task_name}: Skipped — {result.get('reason', '')}")
        return "\n".join(parts)

    def get_process_info(self) -> Dict[str, Any]:
        """Return metadata about this FlowAgent's process binding."""
        defn = None
        try:
            defn = self.registry.get_definition(self.process_key)
        except Exception:
            pass
        return {
            "process_key": self.process_key,
            "process_name": defn.name if defn else self.process_key,
            "bridge": type(self.bridge).__name__,
            "registered_task_agents": list(self.task_agents.keys()),
            "registered_gateway_agents": list(self.gateway_agents.keys()),
            "registered_hooks": len(self.hooks),
        }

    # ──────────────────────────────────────────────────────────────
    # Backward-compat helpers (used by tests / existing callers)
    # ──────────────────────────────────────────────────────────────

    def register_task(self, task_id: str, agent: TaskAgent) -> None:
        self.task_agents[task_id] = agent
        logger.info(f"Registered TaskAgent '{agent.task_name}' for task '{task_id}'")

    def register_hook(self, hook: Hook) -> None:
        self.hooks.append(hook)

    def unregister_hook(self, hook_id: str) -> None:
        self.hooks = [h for h in self.hooks if h.id != hook_id]
