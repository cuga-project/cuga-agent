"""
LangGraphWorkflowEngine - Concrete LangGraph backend for BPMN process execution.

Implements WorkflowEngine using LangGraph StateGraph.  All graph-building logic
lives here; the FlowAgent harness communicates exclusively via the MCP bridge.

At each annotated control point the engine builds a ControlPointFlowKnowledge — embedding
current state, execution history, process model summary, and (for tasks) the task
instruction from the YAML — and calls the appropriate MCP tool on the bridge.

_ControlOverlay is a private internal dataclass used to wire LangGraph nodes.
It is no longer part of the public API between FlowAgent and WorkflowEngine.
"""

import copy
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNProcess
from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent_state import FlowState
from cuga.backend.cuga_graph.nodes.cuga_flow.hook_manager import (
    Hook,
    HookAction,
    HookResult,
)
from cuga.backend.cuga_graph.nodes.cuga_flow.workflow_engine import (
    ControlPointFlowKnowledge,
    WorkflowEngine,
)


@dataclass
class _ControlOverlay:
    """
    Internal LangGraph wiring: maps annotated elements to handler callables.

    In the MCP path these handlers are closures that call FlowAgent tools via
    the MCP client.  In direct/test paths they may be arbitrary callables.

    Handler signatures (all async):
      task_handlers[task_id](ctx: ControlPointFlowKnowledge) → dict
      gateway_handlers[gw_id](ctx: ControlPointFlowKnowledge) → str  (chosen flow_id)
      hook_evaluator(hook: Hook, ctx: ControlPointFlowKnowledge) → HookResult  (async)
    """

    task_handlers: Dict[str, Callable] = field(default_factory=dict)
    gateway_handlers: Dict[str, Callable] = field(default_factory=dict)
    hook_evaluator: Optional[Callable] = None
    task_instructions: Dict[str, str] = field(default_factory=dict)
    hooks: List[Hook] = field(default_factory=list)
    flow_conditions: Dict[str, str] = field(default_factory=dict)
    tool_tasks: Dict[str, Optional[str]] = field(default_factory=dict)
    tools: Dict[str, Callable] = field(default_factory=dict)
    action_permissions: Dict[str, List[str]] = field(default_factory=dict)


class LangGraphWorkflowEngine(WorkflowEngine):
    """LangGraph-backed WorkflowEngine.  Uses the MCP bridge for all harness callbacks."""

    def __init__(self, bridge: Any = None, registry: Any = None) -> None:
        if bridge is not None:
            bridge.register_engine(self)

    # ──────────────────────────────────────────────────────────────
    # Primary entry point (called by MCPFlowBridge's run_process tool)
    # ──────────────────────────────────────────────────────────────

    async def _run_via_mcp(
        self,
        process_key: str,
        initial_inputs: Dict[str, Any],
        mcp_server: Any,
    ) -> tuple:
        """
        Fetch BPMNProcess and static config via MCP, build an internal _ControlOverlay
        with MCP-backed handlers, then execute the LangGraph.

        Returns (FlowState, BPMNProcess).
        """
        async with Client(FastMCPTransport(mcp_server)) as c:
            bpmn_result = await c.call_tool("get_bpmn_process", {"process_key": process_key})
            process = BPMNProcess.from_dict(bpmn_result.data)

            config_result = await c.call_tool("get_flow_annotations", {"process_key": process_key})
            static = config_result.data

            agentic_task_ids: List[str] = static.get("agentic_task_ids", [])
            decision_gw_ids: List[str] = static.get("decision_gateway_ids", [])
            task_instructions: Dict[str, str] = static.get("task_instructions", {})
            hooks_data: List[dict] = static.get("hooks", [])
            flow_conditions: Dict[str, str] = static.get("flow_conditions", {})
            tool_tasks: Dict[str, Optional[str]] = static.get("tool_tasks", {})
            action_permissions: Dict[str, List[str]] = static.get("action_permissions", {})

            stub_hooks: List[Hook] = [
                Hook(id=h["id"], location=h["location"])
                for h in hooks_data
            ]

            # Task handlers: call execute_task MCP tool
            task_handlers: Dict[str, Callable] = {}
            for task_id in agentic_task_ids:

                async def _task_handler(ctx: ControlPointFlowKnowledge, _tid: str = task_id) -> dict:
                    res = await c.call_tool("execute_task", {"task_id": _tid, "ctx": ctx.to_dict()})
                    return res.data

                task_handlers[task_id] = _task_handler

            # Gateway handlers: call route_gateway MCP tool
            gateway_handlers: Dict[str, Callable] = {}
            for gw_id in decision_gw_ids:

                async def _gw_handler(ctx: ControlPointFlowKnowledge, _gw: str = gw_id) -> str:
                    res = await c.call_tool("route_gateway", {"gateway_id": _gw, "ctx": ctx.to_dict()})
                    return res.data

                gateway_handlers[gw_id] = _gw_handler

            # Hook evaluator: call evaluate_hook MCP tool
            async def hook_evaluator(hook: Hook, ctx: ControlPointFlowKnowledge) -> HookResult:
                res = await c.call_tool("evaluate_hook", {"hook_id": hook.id, "ctx": ctx.to_dict()})
                return HookResult.from_dict(res.data)

            overlay = _ControlOverlay(
                task_handlers=task_handlers,
                gateway_handlers=gateway_handlers,
                hook_evaluator=hook_evaluator,
                task_instructions=task_instructions,
                hooks=stub_hooks,
                flow_conditions=flow_conditions,
                tool_tasks=tool_tasks,
                tools={},
                action_permissions=action_permissions,
            )

            state = await self._run_graph(process, initial_inputs, overlay, mcp_client=c)
        return state, process

    async def _run_graph(
        self,
        process: BPMNProcess,
        initial_inputs: Dict[str, Any],
        overlay: _ControlOverlay,
        mcp_client: Any = None,
    ) -> FlowState:
        state = FlowState(
            process_id=process.id,
            process_name=process.name,
            process_variables=initial_inputs,
        )
        state.start_time = datetime.now(timezone.utc)

        # Rebuild loop: each iteration compiles and runs the graph; if a hook action
        # (ADD_NODE / REMOVE_NODE) requests a structural change, the process model and
        # overlay are updated and the graph is recompiled.  Already-executed nodes are
        # fast-forwarded on the next pass via _replay_completed_nodes.
        while True:
            compiled = self._build_graph(process, overlay)
            raw = await compiled.ainvoke(state)
            state = FlowState.model_validate(raw) if isinstance(raw, dict) else raw

            rebuild_spec = state.process_variables.pop("_graph_rebuild_spec", None)
            if not rebuild_spec:
                break

            logger.info(
                f"Graph rebuild requested: {rebuild_spec.get('type')} — recompiling process model"
            )
            process, overlay = self._apply_graph_modification(
                process, overlay, rebuild_spec, mcp_client
            )

        return state

    def _apply_graph_modification(
        self,
        process: BPMNProcess,
        overlay: _ControlOverlay,
        spec: Dict[str, Any],
        mcp_client: Any = None,
    ) -> tuple:
        """
        Apply a structural modification to the process model and overlay, returning
        updated copies.  Called between graph runs when a hook action triggers a rebuild.
        """
        import copy as _copy

        process = _copy.deepcopy(process)
        mod_type = spec.get("type")

        if mod_type == "remove_node":
            node_id = spec["node_id"]
            process.elements.pop(node_id, None)
            incoming = [f for f in process.flows if f.target_ref == node_id]
            outgoing = [f for f in process.flows if f.source_ref == node_id]
            process.flows = [
                f for f in process.flows
                if f.source_ref != node_id and f.target_ref != node_id
            ]
            # Reconnect: each predecessor → each successor, bypassing the removed node
            for inc in incoming:
                for out in outgoing:
                    bypass = _copy.deepcopy(out)
                    bypass.id = f"bypass__{inc.source_ref}__to__{out.target_ref}"
                    bypass.source_ref = inc.source_ref
                    process.flows.append(bypass)
            logger.info(f"  Removed node '{node_id}' and rewired {len(incoming)}×{len(outgoing)} flows")

        elif mod_type == "add_node":
            from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNElement, BPMNFlow

            node_id = spec["node_id"]
            instruction = spec.get("task_instruction", "")
            before_node = spec["before"]

            process.elements[node_id] = BPMNElement(
                id=node_id,
                name=spec.get("node_name", node_id),
                element_type="task",
                attributes={},
            )

            # Redirect all flows pointing to before_node → now point to new node
            redirected, to_remove = [], []
            for f in process.flows:
                if f.target_ref == before_node:
                    to_remove.append(f.id)
                    new_f = _copy.deepcopy(f)
                    new_f.id = f"{f.id}__via__{node_id}"
                    new_f.target_ref = node_id
                    redirected.append(new_f)
            process.flows = [f for f in process.flows if f.id not in to_remove]
            process.flows.extend(redirected)
            process.flows.append(
                BPMNFlow(
                    id=f"flow__{node_id}__to__{before_node}",
                    name="",
                    source_ref=node_id,
                    target_ref=before_node,
                )
            )

            # Register MCP-backed task handler for the new node
            overlay.task_handlers = dict(overlay.task_handlers)
            overlay.task_instructions = dict(overlay.task_instructions)
            overlay.task_instructions[node_id] = instruction
            if mcp_client:
                async def _dynamic_handler(ctx: ControlPointFlowKnowledge, _tid: str = node_id) -> dict:
                    res = await mcp_client.call_tool(
                        "execute_task", {"task_id": _tid, "ctx": ctx.to_dict()}
                    )
                    return res.data

                overlay.task_handlers[node_id] = _dynamic_handler
            logger.info(f"  Inserted node '{node_id}' before '{before_node}', rewired {len(to_remove)} flow(s)")

        return process, overlay

    # ──────────────────────────────────────────────────────────────
    # Graph construction
    # ──────────────────────────────────────────────────────────────

    def _build_graph(self, process: BPMNProcess, overlay: _ControlOverlay):
        graph = StateGraph(FlowState)

        hooks_by_location: Dict[str, Hook] = {h.location: h for h in overlay.hooks}

        flows_by_source: Dict[str, list] = defaultdict(list)
        flows_by_target: Dict[str, list] = defaultdict(list)
        for flow in process.flows:
            flows_by_source[flow.source_ref].append(flow)
            flows_by_target[flow.target_ref].append(flow)

        graph_node_ids: List[str] = []
        for elem_id, element in process.elements.items():
            if element.element_type in ("startEvent", "endEvent"):
                continue

            if "Gateway" in element.element_type:
                node_fn = self._create_gateway_node(elem_id, element, flows_by_source, overlay)
            elif "task" in element.element_type.lower():
                if elem_id in overlay.task_handlers:
                    node_fn = self._create_task_node(elem_id, element, process, overlay)
                else:
                    node_fn = self._create_tool_task_node(
                        elem_id, element, overlay.tool_tasks.get(elem_id), overlay
                    )
            else:
                node_fn = self._create_default_node(elem_id, element)

            graph.add_node(elem_id, node_fn)
            graph_node_ids.append(elem_id)
            logger.debug(f"  Added node: {elem_id}")

        # Build internal edges (skips start_event flows — handled by START routing below)
        self._add_edges_with_hooks(graph, process, overlay, flows_by_source, hooks_by_location)

        # Collect hook node IDs that were actually added during edge building.
        # Only non-start-event flows with a registered hook get hook nodes in the graph.
        start_event_flow_ids = {f.id for f in process.flows if f.source_ref == process.start_event}
        hook_node_ids = [
            f"hook_{flow.id}" for flow in process.flows
            if flow.id not in start_event_flow_ids
            and flow.id in hooks_by_location
        ]
        all_entry_targets = {nid: nid for nid in graph_node_ids + hook_node_ids}

        # Determine the natural first entry node (successor of the start event)
        start_flows = flows_by_source.get(process.start_event, [])
        if start_flows:
            first_target = start_flows[0].target_ref
            start_hook = hooks_by_location.get(start_flows[0].id)
            natural_entry = f"hook_{start_flows[0].id}" if start_hook else first_target
        else:
            natural_entry = next(iter(graph_node_ids), None)

        def _start_router(state: FlowState) -> str:
            resume = state.process_variables.get("_resume_from_node")
            if resume and resume in all_entry_targets:
                return resume
            return natural_entry

        graph.add_conditional_edges(START, _start_router, all_entry_targets)
        return graph.compile()

    def _add_edges_with_hooks(
        self,
        graph: StateGraph,
        process: BPMNProcess,
        overlay: _ControlOverlay,
        flows_by_source: Dict[str, list],
        hooks_by_location: Dict[str, Hook],
    ) -> None:
        for source_ref, outgoing_flows in flows_by_source.items():
            # Start-event outgoing flows are handled by the conditional START edge in _build_graph
            if source_ref == process.start_event:
                continue
            lg_source = source_ref

            if len(outgoing_flows) > 1:
                element = process.elements.get(source_ref)

                if element and element.element_type == "parallelGateway":
                    for flow in outgoing_flows:
                        lg_target = END if flow.target_ref in process.end_events else flow.target_ref
                        hook_for_flow = hooks_by_location.get(flow.id)
                        if hook_for_flow:
                            hook_node_id = f"hook_{flow.id}"
                            graph.add_node(
                                hook_node_id,
                                self._create_hook_node(flow.id, hook_for_flow, lg_target, process, overlay),
                            )
                            graph.add_edge(lg_source, hook_node_id)
                            logger.debug(
                                f"  Parallel branch {flow.id}: {lg_source}→{hook_node_id}→(cmd){lg_target}"
                            )
                        else:
                            graph.add_edge(lg_source, lg_target)
                            logger.debug(f"  Parallel branch {flow.id}: {lg_source}→{lg_target}")

                elif element and "Gateway" in element.element_type:
                    flow_to_target: Dict[str, str] = {}
                    for flow in outgoing_flows:
                        lg_target = END if flow.target_ref in process.end_events else flow.target_ref
                        hook_for_flow = hooks_by_location.get(flow.id)
                        if hook_for_flow:
                            hook_node_id = f"hook_{flow.id}"
                            graph.add_node(
                                hook_node_id,
                                self._create_hook_node(flow.id, hook_for_flow, lg_target, process, overlay),
                            )
                            flow_to_target[flow.id] = hook_node_id
                        else:
                            flow_to_target[flow.id] = lg_target
                    router = self._create_gateway_router(source_ref, flow_to_target)
                    graph.add_conditional_edges(lg_source, router, flow_to_target)
                    logger.debug(f"  Conditional edges from {source_ref} → {list(flow_to_target.values())}")

                else:
                    for flow in outgoing_flows:
                        lg_target = END if flow.target_ref in process.end_events else flow.target_ref
                        graph.add_edge(lg_source, lg_target)

            else:
                flow = outgoing_flows[0]
                lg_target = END if flow.target_ref in process.end_events else flow.target_ref
                hook_for_edge = hooks_by_location.get(flow.id)
                if hook_for_edge:
                    hook_node_id = f"hook_{flow.id}"
                    graph.add_node(
                        hook_node_id,
                        self._create_hook_node(flow.id, hook_for_edge, lg_target, process, overlay),
                    )
                    graph.add_edge(lg_source, hook_node_id)
                    logger.debug(f"  Hook node {hook_node_id}: {lg_source}→{hook_node_id}→(cmd){lg_target}")
                else:
                    graph.add_edge(lg_source, lg_target)
                    logger.debug(f"  Edge: {lg_source}→{lg_target}")

    # ──────────────────────────────────────────────────────────────
    # Router factory
    # ──────────────────────────────────────────────────────────────

    def _create_gateway_router(self, gateway_id: str, flow_to_target: Dict[str, str]) -> Callable:
        default_flow_id = next(iter(flow_to_target.keys()))

        def router(state) -> str:
            decisions = (
                state.get("gateway_decisions", {})
                if isinstance(state, dict)
                else getattr(state, "gateway_decisions", {})
            )
            chosen_flow = decisions.get(gateway_id)
            if chosen_flow in flow_to_target:
                return chosen_flow
            logger.warning(f"No routing decision for {gateway_id}, defaulting to {default_flow_id}")
            return default_flow_id

        return router

    # ──────────────────────────────────────────────────────────────
    # Node factories
    # ──────────────────────────────────────────────────────────────

    def _create_gateway_node(
        self,
        gateway_id: str,
        element: Any,
        flows_by_source: Dict[str, list],
        overlay: _ControlOverlay,
    ) -> Callable:
        async def gateway_node(state: FlowState) -> dict:
            logger.info(f"Executing gateway: {element.name} ({gateway_id})")

            # Clear _resume_from_node once we've arrived at the resume point
            if state.process_variables.get("_resume_from_node") == gateway_id:
                state.process_variables.pop("_resume_from_node", None)

            outgoing_flows = flows_by_source.get(gateway_id, [])

            if element.element_type == "parallelGateway":
                if len(outgoing_flows) > 1:
                    logger.info(f"  Parallel gateway {gateway_id}: split — {len(outgoing_flows)} branches")
                else:
                    logger.info(f"  Parallel gateway {gateway_id}: join")
                return {"execution_path": [gateway_id]}

            chosen = None
            if gateway_id in overlay.gateway_handlers:
                enriched_flows = []
                for f in outgoing_flows:
                    override = overlay.flow_conditions.get(f.id)
                    if override and not f.condition:
                        f = copy.copy(f)
                        f.condition = override
                    enriched_flows.append(f)

                ctx = ControlPointFlowKnowledge(
                    process_instance_id=state.process_id,
                    element_id=gateway_id,
                    element_name=element.name or gateway_id,
                    current_state=state,
                    execution_history=list(state.execution_path),
                    process_model_summary={},
                    available_flows=enriched_flows,
                )
                try:
                    chosen = await overlay.gateway_handlers[gateway_id](ctx)
                except Exception as e:
                    logger.error(f"  Gateway handler error for {gateway_id}: {e}")
                    chosen = outgoing_flows[0].id if outgoing_flows else None
            else:
                chosen = self._tool_route_gateway(gateway_id, outgoing_flows, state, overlay)

            if chosen:
                logger.info(f"  Gateway {gateway_id} → flow {chosen}")

            return {
                "execution_path": [gateway_id],
                "gateway_decisions": {gateway_id: chosen} if chosen else {},
            }

        return gateway_node

    def _tool_route_gateway(
        self,
        gateway_id: str,
        flows: list,
        state: FlowState,
        overlay: _ControlOverlay,
    ) -> str:
        from cuga.backend.cuga_graph.nodes.cuga_flow.decision_agent import eval_condition

        if len(flows) == 1:
            return flows[0].id

        pv = state.process_variables if hasattr(state, "process_variables") else {}
        logger.info(f"  Gateway {gateway_id} — process_variables: { {k: v for k, v in pv.items() if not k.startswith('_')} }")
        for flow in flows:
            condition = overlay.flow_conditions.get(flow.id) or flow.condition
            matched = bool(condition and eval_condition(condition, state))
            status = "✓ MATCHED" if matched else "✗"
            logger.info(f"  {flow.id}: `{condition or '(no condition)'}` → {status}")
            if matched:
                return flow.id

        logger.warning(f"  Gateway {gateway_id}: no condition matched, defaulting to first flow ({flows[0].id})")
        return flows[0].id

    def _create_task_node(
        self,
        task_id: str,
        element: Any,
        process: BPMNProcess,
        overlay: _ControlOverlay,
    ) -> Callable:
        model_summary = self.inspect_model(process)

        async def task_node(state: FlowState) -> dict:
            logger.info(f"Executing agentic task: {element.name} ({task_id})")

            # Clear _resume_from_node once we've arrived at the resume point
            if state.process_variables.get("_resume_from_node") == task_id:
                state.process_variables.pop("_resume_from_node", None)

            if state.process_variables.get("_skip_next_node"):
                logger.info("  Skipping task as requested by hook")
                return {
                    "execution_path": [task_id],
                    "process_variables": {**state.process_variables, "_skip_next_node": False},
                    "task_results": {task_id: {"status": "skipped", "reason": "hook requested skip"}},
                }

            ctx = ControlPointFlowKnowledge(
                process_instance_id=state.process_id,
                element_id=task_id,
                element_name=element.name or task_id,
                current_state=state,
                execution_history=list(state.execution_path),
                process_model_summary=model_summary,
                task_instruction=overlay.task_instructions.get(task_id),
            )
            return await overlay.task_handlers[task_id](ctx)

        return task_node

    def _create_tool_task_node(
        self,
        task_id: str,
        element: Any,
        tool_name: Optional[str],
        overlay: _ControlOverlay,
    ) -> Callable:
        def tool_task_node(state: FlowState) -> dict:
            logger.info(f"Executing tool task: {element.name} ({task_id})")

            # Clear _resume_from_node once we've arrived at the resume point
            if state.process_variables.get("_resume_from_node") == task_id:
                state.process_variables.pop("_resume_from_node", None)

            if state.process_variables.get("_skip_next_node"):
                return {
                    "execution_path": [task_id],
                    "process_variables": {**state.process_variables, "_skip_next_node": False},
                    "task_results": {task_id: {"status": "skipped", "reason": "hook requested skip"}},
                }

            resolved = overlay.tools.get(tool_name) if tool_name else None
            if resolved:
                try:
                    result = resolved(state)
                    output = str(result) if result is not None else f"'{tool_name}' executed"
                    task_result = {"status": "completed", "output": output}
                    logger.info(f"  Tool task '{task_id}' completed via tool '{tool_name}'")
                except Exception as e:
                    logger.error(f"  Error in tool task '{task_id}': {e}")
                    task_result = {"status": "failed", "error": str(e)}
            else:
                note = (
                    f"tool '{tool_name}' not found in tools dict — pass-through"
                    if tool_name
                    else "no tool bound — pass-through"
                )
                logger.info(f"  Tool task '{task_id}': {note}")
                task_result = {"status": "completed", "output": note}

            return {
                "execution_path": [task_id],
                "process_variables": state.process_variables,
                "task_results": {task_id: task_result},
            }

        return tool_task_node

    def _create_default_node(self, node_id: str, element: Any) -> Callable:
        def default_node(state: FlowState) -> dict:
            logger.info(f"Executing node: {element.name} ({node_id})")
            return {"execution_path": [node_id]}

        return default_node

    def _create_hook_node(
        self,
        edge_id: str,
        hook: Hook,
        normal_target: str,
        process: BPMNProcess,
        overlay: _ControlOverlay,
    ) -> Callable:
        permitted = overlay.action_permissions.get("permitted_actions", [])
        prohibited = overlay.action_permissions.get("prohibited_actions", [])
        model_summary = self.inspect_model(process)

        async def hook_node(state: FlowState):
            logger.info(f"Executing hook node for edge: {edge_id}")
            ctx = ControlPointFlowKnowledge(
                process_instance_id=state.process_id,
                element_id=edge_id,
                element_name=edge_id,
                current_state=state,
                execution_history=list(state.execution_path),
                process_model_summary=model_summary,
                edge_id=edge_id,
            )

            # Condition check deferred to FlowAgent when using MCP evaluator;
            # for direct/test overlays with a real condition callable, check here.
            if hook.condition and not hook.condition(state):
                logger.debug(f"  Hook {hook.id} condition not met, skipping")
                return Command(update=state.model_dump(), goto=normal_target)

            try:
                if overlay.hook_evaluator:
                    result = await overlay.hook_evaluator(hook, ctx)
                elif hook.handler:
                    result = hook.handler(state)
                else:
                    result = HookResult(action=HookAction.CONTINUE)

                action_val = result.action.value
                if permitted and action_val not in permitted:
                    logger.warning(
                        f"  Hook {hook.id} action '{action_val}' not in permitted_actions — CONTINUE"
                    )
                    result = HookResult(
                        action=HookAction.CONTINUE,
                        message=f"Action '{action_val}' not permitted",
                    )
                elif action_val in prohibited:
                    logger.warning(f"  Hook {hook.id} action '{action_val}' is prohibited — CONTINUE")
                    result = HookResult(
                        action=HookAction.CONTINUE,
                        message=f"Action '{action_val}' is prohibited",
                    )

                logger.info(f"  Hook {hook.id} → {result.action.value}: {result.message}")

                if result.state_updates:
                    state.process_variables.update(result.state_updates)

                if result.action == HookAction.TERMINATE:
                    state.halt(result.message or "Hook terminated process")
                    return Command(update=state.model_dump(), goto=END)

                elif result.action == HookAction.SKIP_NODE:
                    state.process_variables["_skip_next_node"] = True
                    return Command(update=state.model_dump(), goto=normal_target)

                elif result.action == HookAction.SKIP_TO:
                    target = result.skip_to_node
                    if target:
                        logger.info(f"  Hook issuing Command(goto={target!r})")
                        state.add_graph_modification(
                            hook_id=hook.id,
                            modification_type="skip_to",
                            details={"target": target},
                            reason=result.message or f"Hook routed to {target}",
                        )
                        return Command(update=state.model_dump(), goto=target)

                elif result.action == HookAction.SWAP_NODES:
                    pair = result.swap_nodes
                    if pair and len(pair) == 2:
                        node_a, node_b = pair
                        target = node_b if normal_target == node_a else node_a
                        logger.info(f"  SWAP_NODES: {node_a} ↔ {node_b}, routing to {target!r}")
                        state.add_graph_modification(
                            hook_id=hook.id,
                            modification_type="swap_nodes",
                            details={
                                "node_a": node_a,
                                "node_b": node_b,
                                "routed_to": target,
                            },
                            reason=result.message or f"Hook shuffled {node_a} ↔ {node_b}",
                        )
                        return Command(update=state.model_dump(), goto=target)

                elif result.action == HookAction.REMOVE_NODE:
                    target = result.remove_node
                    if target:
                        logger.info(f"  REMOVE_NODE: scheduling graph rebuild to remove node {target!r}")
                        state.add_graph_modification(
                            hook_id=hook.id,
                            modification_type="remove_node",
                            details={"removed": target},
                            reason=result.message or f"Hook removed node {target}",
                        )
                        # Determine where the rebuilt graph should resume.
                        # If the target is the immediate next node, skip past it to
                        # its successor; otherwise resume at normal_target as usual.
                        if target == normal_target:
                            successors = [
                                f.target_ref for f in process.flows
                                if f.source_ref == target
                                and f.target_ref not in (process.end_events or [])
                            ]
                            resume_from = successors[0] if successors else None
                        else:
                            resume_from = normal_target
                        state.process_variables["_graph_rebuild_spec"] = {
                            "type": "remove_node",
                            "node_id": target,
                        }
                        if resume_from:
                            state.process_variables["_resume_from_node"] = resume_from
                        return Command(update=state.model_dump(), goto=END)

                elif result.action == HookAction.ADD_NODE:
                    add_spec = result.add_node
                    if add_spec and add_spec.get("node_id"):
                        new_node_id = add_spec["node_id"]
                        instruction = add_spec.get("task_instruction", "")
                        logger.info(f"  ADD_NODE: scheduling graph rebuild to insert node {new_node_id!r} before {normal_target!r}")
                        state.add_graph_modification(
                            hook_id=hook.id,
                            modification_type="add_node",
                            details={
                                "node_id": new_node_id,
                                "before": normal_target,
                                "task_instruction": instruction,
                            },
                            reason=result.message or f"Hook inserted node {new_node_id}",
                        )
                        state.process_variables["_graph_rebuild_spec"] = {
                            "type": "add_node",
                            "node_id": new_node_id,
                            "task_instruction": instruction,
                            "before": normal_target,
                        }
                        # Resume at the newly inserted node in the rebuilt graph
                        state.process_variables["_resume_from_node"] = new_node_id
                        return Command(update=state.model_dump(), goto=END)

            except Exception as e:
                logger.error(f"  Error evaluating hook {hook.id}: {e}")

            return Command(update=state.model_dump(), goto=normal_target)

        return hook_node
