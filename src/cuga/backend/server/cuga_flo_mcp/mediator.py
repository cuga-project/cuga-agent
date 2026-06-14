"""
MCP-2-MCP Mediator for CUGA FLO Ordo mode.

MCP2MCPMediator
    Registers in BOTH MCP servers and owns the mediation loop that bridges them.

    Registration in MCPFlowBridge (bridge side)
        • Provides the run_process FastMCP tool — the entry point FlowAgent
          reaches through bridge.run_process().
        • Also installs a _MediatorEngineAdapter as bridge._engine so the
          direct Python call bridge.run_process() → bridge._engine._run_via_mcp()
          is handled without going through the FastMCP transport layer.
        • The mediation loop it drives:
            1. call MCPOrdo register_workflow + run_workflow
            2. on each agent_goal pause: call MCPFlowBridge execute_task
               via a nested in-process client → FlowAgent → TaskAgent
            3. call MCPOrdo resume_workflow with the task result
            4. repeat until RunResult.final_response is set
            5. build FlowState + stub BPMNProcess and return

    Registration in MCPOrdo (engine side)
        • Adds execute_task_proxy, route_gateway_proxy, evaluate_hook_proxy
          as FastMCP tools on MCPOrdo's server.
        • These proxy tools forward calls from MCPOrdo's namespace into
          MCPFlowBridge, enabling future external engines that support direct
          MCP callbacks to bypass the pause-resume round-trip entirely.

Architecture:

    [FlowAgent]
        │  bridge.run_process()  (direct Python call)
        ▼
    [MCPFlowBridge._mcp]
        │  run_process tool  ← MCP2MCPMediator._register_on_bridge()
        │  execute_task, route_gateway, evaluate_hook  ← FlowAgent
        ▲
    [MCP2MCPMediator._mediation_loop]
        │  calls MCPOrdo as client (run_workflow / resume_workflow)
        │  calls MCPFlowBridge as nested client (execute_task)
        ▼
    [MCPOrdo._mcp]
        │  run_workflow, resume_workflow, ...  ← OrdoEngine (served by MCPOrdo)
        │  execute_task_proxy, route_gateway_proxy, evaluate_hook_proxy
        │  ← MCP2MCPMediator._register_on_ordo()
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_flow.remote.schemas import AgentGoal, RunResult

if TYPE_CHECKING:
    from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNProcess
    from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent_state import FlowState
    from cuga.backend.server.cuga_flo_mcp.bridge import MCPFlowBridge
    from cuga.backend.server.cuga_flo_mcp.ordo import MCPOrdo


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

        mediator = MCP2MCPMediator(bridge, ordo, ordo_workflow_id)
        mediator.register()
    """

    def __init__(
        self,
        bridge: "MCPFlowBridge",
        ordo: "MCPOrdo",
        ordo_workflow_id: str,
    ) -> None:
        self._bridge = bridge
        self._ordo = ordo
        self._ordo_workflow_id = ordo_workflow_id

    def register(self) -> None:
        """Register mediator tools in both MCP servers."""
        self._register_on_bridge()
        self._register_on_ordo()
        logger.info(
            "MCP2MCPMediator: registered run_process on MCPFlowBridge and "
            "execute_task_proxy / route_gateway_proxy / evaluate_hook_proxy on MCPOrdo"
        )

    # ── MCPFlowBridge registration ────────────────────────────────────────────

    def _register_on_bridge(self) -> None:
        """
        Register the mediator in MCPFlowBridge:

        1. Install _MediatorEngineAdapter as bridge._engine (direct Python call path).
        2. Call bridge.register_engine(adapter) which also registers the run_process
           FastMCP tool on the bridge's server (MCP protocol path).
        """
        adapter = _MediatorEngineAdapter(self)
        self._bridge.register_engine(adapter)  # sets bridge._engine + registers FastMCP tool

    # ── MCPOrdo registration ──────────────────────────────────────────────────

    def _register_on_ordo(self) -> None:
        """
        Register FlowAgent control-point proxies in MCPOrdo.

        These tools forward calls from MCPOrdo's namespace to MCPFlowBridge,
        enabling future external engines that can make direct MCP callbacks.
        """
        bridge_mcp = self._bridge.mcp

        @self._ordo.mcp.tool(name="execute_task_proxy")
        async def execute_task_proxy(task_id: str, ctx: dict) -> dict:
            """Proxy: forward execute_task from MCPOrdo to MCPFlowBridge."""
            async with Client(FastMCPTransport(bridge_mcp)) as c:
                raw = await c.call_tool("execute_task", {"task_id": task_id, "ctx": ctx})
                return raw[0].text if hasattr(raw[0], "text") else raw

        @self._ordo.mcp.tool(name="route_gateway_proxy")
        async def route_gateway_proxy(gateway_id: str, ctx: dict) -> str:
            """Proxy: forward route_gateway from MCPOrdo to MCPFlowBridge."""
            async with Client(FastMCPTransport(bridge_mcp)) as c:
                raw = await c.call_tool("route_gateway", {"gateway_id": gateway_id, "ctx": ctx})
                return raw[0].text if hasattr(raw[0], "text") else str(raw)

        @self._ordo.mcp.tool(name="evaluate_hook_proxy")
        async def evaluate_hook_proxy(hook_id: str, ctx: dict) -> dict:
            """Proxy: forward evaluate_hook from MCPOrdo to MCPFlowBridge."""
            async with Client(FastMCPTransport(bridge_mcp)) as c:
                raw = await c.call_tool("evaluate_hook", {"hook_id": hook_id, "ctx": ctx})
                return raw[0].text if hasattr(raw[0], "text") else raw

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

        workflow_id = self._ordo_workflow_id
        accumulated_vars: Dict[str, Any] = dict(initial_inputs)

        async with self._ordo.get_client() as ordo_client:

            # Register the workflow with OrdoEngine via MCPOrdo
            await ordo_client.call_tool(
                "register_workflow",
                {
                    "workflow_json": {
                        "workflow_id": workflow_id,
                        "name": workflow_id,
                        "description": f"Ordo workflow: {workflow_id}",
                    }
                },
            )
            logger.info(
                f"MCP2MCPMediator: registered workflow '{workflow_id}' on MCPOrdo"
            )

            # Start the run — OrdoEngine either completes or pauses with an agent_goal
            raw = await ordo_client.call_tool("run_workflow", {"workflow_id": workflow_id})
            result = RunResult.model_validate(
                raw[0].text if hasattr(raw[0], "text") else raw
            )

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
                async with Client(FastMCPTransport(mcp_server)) as bridge_client:
                    task_raw = await bridge_client.call_tool(
                        "execute_task", {"task_id": agent_name, "ctx": ctx}
                    )

                agent_response: Any = (
                    task_raw[0].text
                    if hasattr(task_raw[0], "text")
                    else str(task_raw)
                )

                # Merge any output variables back into accumulated state
                try:
                    import json as _json
                    task_dict = (
                        _json.loads(agent_response)
                        if isinstance(agent_response, str)
                        else agent_response
                    )
                    if isinstance(task_dict, dict):
                        accumulated_vars.update(task_dict.get("output_variables", {}))
                except Exception:
                    pass

                logger.info(
                    f"MCP2MCPMediator: resuming MCPOrdo session '{session_id}'"
                )

                raw = await ordo_client.call_tool(
                    "resume_workflow",
                    {"session_id": session_id, "agent_response": agent_response},
                )
                result = RunResult.model_validate(
                    raw[0].text if hasattr(raw[0], "text") else raw
                )

        # Build terminal FlowState
        final_state = FlowState(
            process_id=workflow_id,
            process_name=workflow_id,
            process_variables=accumulated_vars,
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

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_ctx(
        goal: AgentGoal,
        process_key: str,
        accumulated_vars: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Synthesise a ControlPointFlowKnowledge dict from an AgentGoal."""
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
                "halt_reason": None,
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
