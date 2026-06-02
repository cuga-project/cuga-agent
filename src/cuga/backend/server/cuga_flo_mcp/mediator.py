"""
MCP-2-MCP Mediator for CUGA FLO Ordo mode.

OrdoRegistryAdapter
    Implements MCPFlowBridge's registry interface (register_flow, get_flow_annotations,
    get_bpmn_process) without ProcessRegistry.  Has no direct MCPOrdo reference —
    workflow registration is delegated to MCP2MCPMediator via a notify_fn callback.

MCP2MCPMediator
    Registers on MCPFlowBridge and owns the mediation loop that bridges it to MCPOrdo.
    MCPOrdo's API is strictly based on external_mcp_engine/server.py — no extra tools
    are added to it.

    Registration on MCPFlowBridge
        • Registers OrdoRegistryAdapter as bridge._registry, adding MCP tools:
          register_flow, get_bpmn_process, get_flow_annotations.
        • Provides the run_process FastMCP tool via _MediatorEngineAdapter.
        • The mediation loop it drives:
            1. call MCPOrdo run_workflow (workflow registered during bridge.load_flow)
            2. on each agent_goal pause: call MCPFlowBridge execute_task
               via a nested in-process client → FlowAgent → TaskAgent
            3. call MCPOrdo resume_workflow with the task result
            4. repeat until RunResult.final_response is set
            5. build FlowState + stub BPMNProcess and return

Architecture:

    [FlowConfig.to_ordo_flow_agent()]
        bridge.load_flow(config_path)
        │  → OrdoRegistryAdapter.register_flow() → notify_fn
        │  → MCP2MCPMediator._register_workflow_on_ordo()
        │  → ordo.store.register_workflow()
        ▼
    [FlowAgent.__init__]
        bridge.get_flow_annotations(process_key)
        │  → OrdoRegistryAdapter.get_flow_annotations()
        │  → returns FlowConfig → config.create_task_agents()
        ▼
    [FlowAgent.invoke()]
        bridge.run_process()
        │  → _MediatorEngineAdapter._run_via_mcp()
        │  → MCP2MCPMediator._mediation_loop()
        ▼
    [MCPOrdo._mcp]
        │  run_workflow, resume_workflow, ...  ← WorkflowStubStore (server.py API only)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict

from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_flow.remote.schemas import AgentGoal, RunResult
from cuga.backend.server.cuga_flo_mcp.mcp_logger import mcp_in, mcp_out

if TYPE_CHECKING:
    from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNProcess
    from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent_state import FlowState
    from cuga.backend.server.cuga_flo_mcp.bridge import MCPFlowBridge
    from cuga.backend.server.cuga_flo_mcp.ordo import MCPOrdo


# ── OrdoRegistryAdapter ──────────────────────────────────────────────────────


class OrdoRegistryAdapter:
    """
    Registry adapter that satisfies MCPFlowBridge's registry interface without ProcessRegistry.

    Has no direct reference to MCPOrdo — all MCPOrdo interactions are handled by
    MCP2MCPMediator via the notify_fn callback (bridge → mediator → MCPOrdo).

    Responsibilities:
      • Parses and caches FlowConfig from a YAML path (register_flow)
      • Notifies MCP2MCPMediator when a flow is registered (via notify_fn)
      • Returns cached FlowConfig on get_flow_annotations (used by FlowAgent.__init__)
      • Returns a stub BPMNProcess on get_bpmn_process (no BPMN in ordo mode)
    """

    def __init__(self, process_key: str, notify_fn: Callable[[str, str], None]) -> None:
        self._process_key = process_key
        self._notify_fn = notify_fn  # MCP2MCPMediator._register_workflow_on_ordo
        self._config_cache: Dict[str, Any] = {}

    def register_flow(self, flow_config_path: str) -> str:
        """Parse YAML, cache FlowConfig, notify mediator to register with MCPOrdo."""
        from cuga.backend.cuga_graph.nodes.cuga_flow.flow_config import FlowConfig

        config = FlowConfig.from_yaml(flow_config_path)
        self._config_cache[self._process_key] = config
        self._notify_fn(self._process_key, config.get_flow_name() or self._process_key)
        logger.info(
            f"OrdoRegistryAdapter: registered flow '{self._process_key}' from {flow_config_path}"
        )
        return self._process_key

    def get_flow_annotations(self, key: str) -> Any:
        """Return the cached FlowConfig for process_key."""
        if key not in self._config_cache:
            raise KeyError(
                f"OrdoRegistryAdapter: flow '{key}' not registered. "
                "Call bridge.load_flow() before creating FlowAgent."
            )
        return self._config_cache[key]

    def get_bpmn_process(self, key: str) -> Any:
        """Return a stub BPMNProcess — no BPMN file exists in ordo mode."""
        from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNProcess

        return BPMNProcess(id=key, name=key, elements={}, flows=[])

    def list_keys(self) -> list:
        return list(self._config_cache)


# ── _MediatorEngineAdapter ────────────────────────────────────────────────────


class _MediatorEngineAdapter:
    """
    Thin adapter installed as bridge._engine so bridge.run_process() (the
    direct Python path) routes through the mediator's loop without needing
    a FastMCP round-trip.
    """

    def __init__(self, mediator: "MCP2MCPMediator") -> None:
        self._mediator = mediator

    async def _run_via_mcp(
        self,
        process_key: str,
        initial_inputs: Dict[str, Any],
        mcp_server: Any,
    ) -> tuple["FlowState", "BPMNProcess"]:
        return await self._mediator._mediation_loop(process_key, initial_inputs, mcp_server)


# ── MCP2MCPMediator ───────────────────────────────────────────────────────────


class MCP2MCPMediator:
    """
    Registers in both MCPFlowBridge and MCPOrdo and owns the mediation loop.

    Call register() once after both servers exist:

        mediator = MCP2MCPMediator(bridge, ordo, process_key)
        mediator.register()
    """

    def __init__(
        self,
        bridge: "MCPFlowBridge",
        ordo: "MCPOrdo",
        process_key: str,
    ) -> None:
        self._bridge = bridge
        self._ordo = ordo
        self._process_key = process_key

    def register(self) -> None:
        """Register mediator services on MCPFlowBridge."""
        self._register_on_bridge()
        logger.info(
            "MCP2MCPMediator: registered OrdoRegistryAdapter + run_process on MCPFlowBridge"
        )

    # ── MCPFlowBridge registration ────────────────────────────────────────────

    def _register_workflow_on_ordo(self, workflow_id: str, name: str) -> None:
        """
        Register the workflow with MCPOrdo.

        Called via the OrdoRegistryAdapter.notify_fn callback when bridge.load_flow()
        is invoked — MCP2MCPMediator is the sole owner of all MCPOrdo interactions.
        """
        mcp_in("MCP2MCPMediator", "ordo.store.register_workflow",
               workflow_id=workflow_id, name=name)
        self._ordo.store.register_workflow({
            "workflow_id": workflow_id,
            "name": name,
            "description": f"Ordo workflow: {workflow_id}",
        })
        mcp_out("MCP2MCPMediator", "ordo.store.register_workflow", workflow_id=workflow_id)
        logger.info(f"MCP2MCPMediator: registered workflow '{workflow_id}' on MCPOrdo")

    def _register_on_bridge(self) -> None:
        """
        Register the mediator in MCPFlowBridge:

        1. Register OrdoRegistryAdapter as bridge._registry so bridge.load_flow() and
           bridge.get_flow_annotations() route through MCP2MCPMediator → MCPOrdo.
           Also adds MCP tools: register_flow, get_bpmn_process, get_flow_annotations.
        2. Install _MediatorEngineAdapter as bridge._engine (direct Python call path).
           Also registers the run_process FastMCP tool on the bridge's server.
        """
        registry_adapter = OrdoRegistryAdapter(
            process_key=self._process_key,
            notify_fn=self._register_workflow_on_ordo,
        )
        self._bridge.register_registry(registry_adapter)

        engine_adapter = _MediatorEngineAdapter(self)
        self._bridge.register_engine(engine_adapter)

    # ── Mediation loop ────────────────────────────────────────────────────────

    async def _mediation_loop(
        self,
        process_key: str,
        initial_inputs: Dict[str, Any],
        mcp_server: Any,  # bridge._mcp FastMCP server — used for nested client
    ) -> tuple["FlowState", "BPMNProcess"]:
        """
        Drive the full run_workflow → [agent_goal → execute_task → resume_workflow]
        loop between MCPOrdo and MCPFlowBridge, then return a terminal FlowState
        and a stub BPMNProcess compatible with FlowAgent.invoke().
        """
        from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNProcess
        from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent_state import FlowState
        from cuga.backend.server.cuga_flo_mcp.ordo import MCPOrdoExternal

        if isinstance(self._ordo, MCPOrdoExternal):
            return await self._mediation_loop_ro(process_key, initial_inputs, mcp_server)

        workflow_id = self._process_key
        accumulated_vars: Dict[str, Any] = dict(initial_inputs)
        accumulated_task_results: Dict[str, Any] = {}

        async with self._ordo.get_client() as ordo_client:
            # Workflow was already registered during bridge.load_flow() via
            # OrdoRegistryAdapter.register_flow() → ordo.store.register_workflow().

            # Start the run — OrdoEngine either completes or pauses with an agent_goal
            mcp_in("MCP2MCPMediator", "MCPOrdo.run_workflow", workflow_id=workflow_id)
            raw = await ordo_client.call_tool("run_workflow", {"workflow_id": workflow_id})
            result = RunResult.model_validate(self._extract_dict(raw))
            mcp_out("MCP2MCPMediator", "MCPOrdo.run_workflow",
                    workflow_id=workflow_id,
                    has_agent_goal=result.agent_goal is not None,
                    agent_name=result.agent_goal.agent_name if result.agent_goal else None,
                    final_response=result.final_response)

            # Mediation loop: forward each agent_goal to FlowAgent via MCPFlowBridge
            while result.agent_goal is not None:
                goal: AgentGoal = result.agent_goal
                session_id = goal.workflow_session_id
                agent_name = goal.agent_name

                logger.info(
                    f"MCP2MCPMediator: MCPOrdo paused — forwarding '{agent_name}' "
                    f"to MCPFlowBridge (session={session_id})"
                )

                ctx = self._build_ctx(goal, process_key, accumulated_vars)

                # Call FlowAgent's execute_task via a nested MCPFlowBridge client
                mcp_in("MCP2MCPMediator", "MCPFlowBridge.execute_task",
                       task_id=agent_name, session_id=session_id,
                       var_keys=list(accumulated_vars.keys()))
                async with Client(FastMCPTransport(mcp_server)) as bridge_client:
                    task_raw = await bridge_client.call_tool(
                        "execute_task", {"task_id": agent_name, "ctx": ctx}
                    )
                agent_response: Any = self._extract_dict(task_raw)
                mcp_out("MCP2MCPMediator", "MCPFlowBridge.execute_task",
                        task_id=agent_name,
                        response_keys=list(agent_response.keys()) if isinstance(agent_response, dict) else None,
                        task_status=agent_response.get("task_results", {}).get(agent_name, {}).get("status") if isinstance(agent_response, dict) else None)

                # Merge process variables and task results back into accumulated state
                try:
                    if isinstance(agent_response, dict):
                        accumulated_vars.update(agent_response.get("process_variables", {}))
                        accumulated_task_results.update(agent_response.get("task_results", {}))
                except Exception:
                    pass

                logger.info(
                    f"MCP2MCPMediator: resuming MCPOrdo session '{session_id}'"
                )

                mcp_in("MCP2MCPMediator", "MCPOrdo.resume_workflow",
                       session_id=session_id, agent_name=agent_name)
                raw = await ordo_client.call_tool(
                    "resume_workflow",
                    {"session_id": session_id, "agent_response": agent_response},
                )
                result = RunResult.model_validate(self._extract_dict(raw))
                mcp_out("MCP2MCPMediator", "MCPOrdo.resume_workflow",
                        session_id=session_id,
                        has_agent_goal=result.agent_goal is not None,
                        final_response=result.final_response)

        # Build terminal FlowState with all accumulated variables and task results
        final_state = FlowState(
            process_id=workflow_id,
            process_name=workflow_id,
            process_variables=accumulated_vars,
            task_results=accumulated_task_results,
            is_complete=True,
        )
        final_state.messages = [
            {
                "role": "assistant",
                "content": result.final_response or "Workflow completed.",
            }
        ]
        logger.info(f"MCP2MCPMediator: workflow '{workflow_id}' finished")

        stub_bpmn = BPMNProcess(
            id=workflow_id, name=workflow_id, elements={}, flows=[]
        )
        return final_state, stub_bpmn

    async def _mediation_loop_ro(
        self,
        process_key: str,
        initial_inputs: Dict[str, Any],
        mcp_server: Any,
    ) -> tuple["FlowState", "BPMNProcess"]:
        """
        Drive a real ``ro mcp`` workflow using its workflow/session API.

        Real ro tool flow:
          1. register_workflow(json={source, input_args, force}, workflow_id)
          2. run_workflow(workflow_id, dispatch="mcp") -> external GOAL payload
          3. MCPFlowBridge.execute_task(goal.name, ctx)
          4. complete_goal(workflow_id, session_id, goal_id, result)
          5. run_workflow(workflow_id, session_id, dispatch="mcp") until completion
        """
        from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNProcess
        from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent_state import FlowState

        workflow_id = self._process_key
        flow_config = self._bridge.get_flow_annotations(process_key)
        ro_source = flow_config.get_ro_source()

        accumulated_vars: Dict[str, Any] = dict(initial_inputs)
        accumulated_task_results: Dict[str, Any] = {}
        final_response = "Workflow completed."
        session_id: str | None = None

        # Only pass explicit flow.input_args to ro register_workflow.
        #
        # Do not blindly forward FlowAgent process variables here: ordo_config
        # variables are CUGA-side working state defaults and may include partial
        # placeholders (for example `{}` for record-typed RO state fields). RO
        # validates input_args against the .ro schema during registration, so
        # forwarding those placeholders can make workflow initialization fail.
        input_args = flow_config.get_ro_input_args()
        user_message = initial_inputs.get("_user_message")
        if user_message and (not input_args.get("name") or input_args.get("name") == "world"):
            input_args["name"] = user_message

        async with self._ordo.get_client() as ordo_client:
            mcp_in("MCP2MCPMediator", "RO.register_workflow",
                   workflow_id=workflow_id, input_keys=list(input_args.keys()))
            raw = await ordo_client.call_tool(
                "register_workflow",
                {
                    "workflow_id": workflow_id,
                    "json": {
                        "source": ro_source,
                        "input_args": input_args,
                        "force": True,
                    },
                },
            )
            reg_result = self._extract_dict(raw)
            mcp_out("MCP2MCPMediator", "RO.register_workflow",
                    workflow_id=workflow_id, status=reg_result.get("status"))

            run_args: Dict[str, Any] = {"workflow_id": workflow_id, "dispatch": "mcp"}

            while True:
                mcp_in("MCP2MCPMediator", "RO.run_workflow",
                       workflow_id=workflow_id, session_id=session_id)
                raw = await ordo_client.call_tool("run_workflow", run_args)
                ro_result = self._extract_dict(raw)
                session_id = ro_result.get("session_id") or session_id
                mcp_out("MCP2MCPMediator", "RO.run_workflow",
                        workflow_id=workflow_id,
                        session_id=session_id,
                        payload_type=ro_result.get("type"),
                        goal_name=ro_result.get("name"),
                        status=ro_result.get("status"))

                # A single external GOAL payload.
                if ro_result.get("type") == "goal" and ro_result.get("goal_id"):
                    goal_payloads = [ro_result]
                # Future-proof concurrent mode: ro may return a batch of goals.
                elif ro_result.get("goal_batch"):
                    goal_payloads = ro_result.get("goal_batch") or []
                else:
                    final_response = (
                        ro_result.get("final_response")
                        or ro_result.get("response")
                        or ro_result.get("status")
                        or final_response
                    )
                    break

                for goal_payload in goal_payloads:
                    goal_id = goal_payload.get("goal_id")
                    agent_name = goal_payload.get("name")
                    if not goal_id or not agent_name:
                        continue

                    accumulated_vars.update(goal_payload.get("given") or {})
                    ctx = self._build_ctx_ro(goal_payload, process_key, accumulated_vars)

                    mcp_in("MCP2MCPMediator", "MCPFlowBridge.execute_task",
                           task_id=agent_name,
                           session_id=session_id,
                           goal_id=goal_id,
                           var_keys=list(accumulated_vars.keys()))
                    async with Client(FastMCPTransport(mcp_server)) as bridge_client:
                        task_raw = await bridge_client.call_tool(
                            "execute_task", {"task_id": agent_name, "ctx": ctx}
                        )
                    agent_response: Any = self._extract_dict(task_raw)
                    task_results = agent_response.get("task_results", {}) if isinstance(agent_response, dict) else {}
                    task_result = task_results.get(agent_name, {}) if isinstance(task_results, dict) else {}
                    goal_result = task_result.get("output", task_result.get("result", task_result))
                    mcp_out("MCP2MCPMediator", "MCPFlowBridge.execute_task",
                            task_id=agent_name,
                            task_status=task_result.get("status") if isinstance(task_result, dict) else None,
                            output_len=len(str(goal_result)))

                    if isinstance(agent_response, dict):
                        accumulated_vars.update(agent_response.get("process_variables", {}))
                        accumulated_task_results.update(task_results)

                    result_into = goal_payload.get("result_into")
                    if result_into:
                        accumulated_vars[result_into] = goal_result

                    mcp_in("MCP2MCPMediator", "RO.complete_goal",
                           workflow_id=workflow_id,
                           session_id=session_id,
                           goal_id=goal_id)
                    complete_raw = await ordo_client.call_tool(
                        "complete_goal",
                        {
                            "workflow_id": workflow_id,
                            "session_id": session_id,
                            "goal_id": goal_id,
                            "result": goal_result,
                        },
                    )
                    complete_result = self._extract_dict(complete_raw)
                    mcp_out("MCP2MCPMediator", "RO.complete_goal",
                            workflow_id=workflow_id,
                            session_id=session_id,
                            goal_id=goal_id,
                            is_error=complete_result.get("isError") if isinstance(complete_result, dict) else None)

                if session_id:
                    run_args["session_id"] = session_id

        final_state = FlowState(
            process_id=workflow_id,
            process_name=workflow_id,
            process_variables=accumulated_vars,
            task_results=accumulated_task_results,
            is_complete=True,
        )
        final_state.messages = [
            {
                "role": "assistant",
                "content": str(accumulated_vars.get("greeting") or final_response),
            }
        ]
        logger.info(f"MCP2MCPMediator: ro workflow '{workflow_id}' finished")

        stub_bpmn = BPMNProcess(id=workflow_id, name=workflow_id, elements={}, flows=[])
        return final_state, stub_bpmn

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_dict(raw: Any) -> dict:
        """
        Extract and parse a dict from a FastMCP call_tool() result.

        FastMCP changed its return type across versions:
          - Older: list of TextContent items → raw[0].text  (JSON string)
          - Newer: CallToolResult object     → raw.content[0].text  (JSON string)
        In both cases .text is a JSON string; parse it with json.loads() so
        callers can use model_validate(dict) rather than model_validate_json(str).
        """
        import json as _json

        # New API: CallToolResult with .content list
        if hasattr(raw, "content"):
            items = raw.content
            text = items[0].text if (items and hasattr(items[0], "text")) else str(raw)
        else:
            # Old API: bare list of content items
            try:
                item = raw[0]
                text = item.text if hasattr(item, "text") else str(raw)
            except (TypeError, IndexError):
                text = str(raw)

        if isinstance(text, dict):
            return text
        return _json.loads(text)

    @staticmethod
    def _build_ctx_ro(
        goal: Dict[str, Any],
        process_key: str,
        accumulated_vars: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Synthesise a ControlPointContext dict from a real ro GoalRequest."""
        agent_name = goal.get("name", "task_agent")
        goal_id = goal.get("goal_id", agent_name)
        merged_vars = {**accumulated_vars, **(goal.get("given") or {})}
        return {
            "process_instance_id": goal.get("session_id") or goal_id,
            "element_id": agent_name,
            "element_name": agent_name,
            "current_state": {
                "process_id": process_key,
                "process_name": process_key,
                "process_variables": merged_vars,
                "execution_path": [],
                "messages": [],
                "is_complete": False,
                "is_halted": False,
                "current_task": agent_name,
                "halt_reason": "",
            },
            "execution_history": [],
            "process_model_summary": {
                "process_id": process_key,
                "elements": {
                    agent_name: {"type": "task", "name": agent_name}
                },
            },
            "task_instruction": goal.get("description") or goal.get("suggested_prompt"),
            "available_flows": None,
            "edge_id": None,
        }

    @staticmethod
    def _build_ctx(
        goal: AgentGoal,
        process_key: str,
        accumulated_vars: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Synthesise a ControlPointContext dict from an AgentGoal."""
        merged_vars = {**accumulated_vars, **goal.context.vars}
        return {
            "process_instance_id": goal.workflow_session_id,
            "element_id": goal.agent_name,
            "element_name": goal.agent_name,
            "current_state": {
                "process_id": process_key,
                "process_name": process_key,
                "process_variables": merged_vars,
                "execution_path": list(goal.context.state.get("execution_path", [])),
                "messages": [],
                "is_complete": False,
                "is_halted": False,
                "current_task": goal.agent_name,
                "halt_reason": "",
            },
            "execution_history": [],
            "process_model_summary": {
                "process_id": process_key,
                "elements": {
                    goal.agent_name: {"type": "task", "name": goal.agent_name}
                },
            },
            "task_instruction": goal.context.state.get("task_instruction"),
            "available_flows": None,
            "edge_id": None,
        }
