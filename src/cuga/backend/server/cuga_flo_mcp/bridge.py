"""
MCPFlowBridge - Shared FastMCP server for CUGA FLO bi-directional messaging.

Both FlowAgent and WorkflowEngine register their services here:
  - FlowAgent registers: execute_task, route_gateway, evaluate_hook
  - WorkflowEngine registers: run_process

FlowAgent calls load_flow and get_flow_annotations via direct bridge methods.
All workflow invocations and FlowAgent callbacks (execute_task, route_gateway,
evaluate_hook, run_process) go exclusively through MCP tools on the shared
FastMCP server, enabling future remote/cross-process transport.

Replacing ControlOverlay (Python-closure bridge) with this MCP bridge makes the
contract explicit via tool schemas and enables future remote/cross-process transport
by swapping FastMCPTransport for an HTTP or SSE transport.
"""

from typing import TYPE_CHECKING

from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport
from loguru import logger

if TYPE_CHECKING:
    from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent import FlowAgent
    from cuga.backend.cuga_graph.nodes.cuga_flow.langgraph_engine import LangGraphWorkflowEngine
    from cuga.backend.cuga_graph.nodes.cuga_flow.process_registry import ProcessRegistry


class MCPFlowBridge:
    """
    Shared FastMCP server mediating bi-directional messaging between
    FlowAgent, WorkflowEngine, and ProcessRegistry.

    Usage:
        bridge = MCPFlowBridge()
        bridge.register_registry(registry)
        bridge.register_flow_agent(fa)
        bridge.register_engine(engine)
        # FlowAgent.invoke() calls the run_process MCP tool via get_client()
    """

    def __init__(self, name: str = "cuga-flo-mcp") -> None:
        self._mcp = FastMCP(name)
        self._registry: "ProcessRegistry | None" = None
        self._engine: "LangGraphWorkflowEngine | None" = None
        logger.debug(f"MCPFlowBridge created: {name!r}")

    # ──────────────────────────────────────────────────────────────
    # Registration
    # ──────────────────────────────────────────────────────────────

    def register_registry(self, registry: "ProcessRegistry") -> None:
        """
        Register ProcessRegistry services as MCP tools.

        Tools registered:
          register_flow(flow_config_path) → str   (parse YAML+BPMN, returns process key)
          get_bpmn_process(process_key)   → dict  (serialised BPMNProcess)

        The bridge also holds an internal reference so load_flow() and
        register_engine() can reach the registry without a direct dependency.
        """
        self._registry = registry

        async def register_flow(flow_config_path: str) -> str:
            """Parse YAML + BPMN and register the process. Returns the process key."""
            return registry.register_flow(flow_config_path)

        async def get_bpmn_process(process_key: str) -> dict:
            """Fetch and serialise a BPMNProcess from the registry."""
            bpmn = registry.get_bpmn_process(process_key)
            return bpmn.to_dict()

        async def get_flow_annotations(process_key: str) -> dict:
            """Fetch engine-consumable config derived from the registry's FlowConfig."""
            config = registry.get_flow_annotations(process_key)
            return config.to_engine_config()

        self._mcp.tool(name="register_flow")(register_flow)
        self._mcp.tool(name="get_bpmn_process")(get_bpmn_process)
        self._mcp.tool(name="get_flow_annotations")(get_flow_annotations)
        logger.info("MCPFlowBridge: registered ProcessRegistry tools")

    def load_flow(self, flow_config_path: str) -> str:
        """
        Parse YAML + BPMN and register the process via the registry.

        Sync bridge method — mediates the call so FlowConfig never touches
        the registry directly.  The same operation is also exposed as the
        MCP tool 'register_flow' for async callers.

        Returns the process key derived from the YAML.
        """
        return self._registry.register_flow(flow_config_path)

    def get_flow_annotations(self, process_key: str) -> "FlowConfig":
        """
        Fetch a FlowConfig from the registry.

        Sync bridge method — mediates the call so FlowAgent never touches
        the registry directly.  Also exposed as the MCP tool 'get_flow_annotations'.
        """
        return self._registry.get_flow_annotations(process_key)

    def register_flow_agent(self, fa: "FlowAgent") -> None:
        """
        Register FlowAgent's control-point handlers as MCP tools.

        Tools registered:
          execute_task(task_id, ctx)     → dict   (agentic task execution)
          route_gateway(gateway_id, ctx) → str    (gateway routing)
          evaluate_hook(hook_id, ctx)    → dict   (hook evaluation)
        """
        from cuga.backend.cuga_graph.nodes.cuga_flow.workflow_engine import ControlPointFlowKnowledge

        async def execute_task(task_id: str, ctx: dict) -> dict:
            """Execute an agentic task via FlowAgent."""
            ctx_obj = ControlPointFlowKnowledge.from_dict(ctx)
            return await fa._handle_task(task_id, ctx_obj)

        async def route_gateway(gateway_id: str, ctx: dict) -> str:
            """Route a gateway via FlowAgent's DecisionAgent."""
            ctx_obj = ControlPointFlowKnowledge.from_dict(ctx)
            return await fa._handle_gateway(gateway_id, ctx_obj)

        async def evaluate_hook(hook_id: str, ctx: dict) -> dict:
            """Evaluate a hook via FlowAgent's hook evaluator."""
            ctx_obj = ControlPointFlowKnowledge.from_dict(ctx)
            hook = next((h for h in fa.hooks if h.id == hook_id), None)
            if hook is None:
                from cuga.backend.cuga_graph.nodes.cuga_flow.hook_manager import HookAction, HookResult

                return HookResult(action=HookAction.CONTINUE, message=f"Hook {hook_id!r} not found").to_dict()
            result = await fa._handle_hook(hook, ctx_obj)
            return result.to_dict()

        for fn, tool_name in [
            (execute_task, "execute_task"),
            (route_gateway, "route_gateway"),
            (evaluate_hook, "evaluate_hook"),
        ]:
            self._mcp.tool(name=tool_name)(fn)

        logger.info(f"MCPFlowBridge: registered FlowAgent tools for process '{fa.process_key}'")

    def register_engine(self, engine: "LangGraphWorkflowEngine") -> None:
        """
        Register WorkflowEngine's run_process as an MCP tool.

        Tool registered:
          run_process(process_key, initial_inputs) → dict  (FlowState serialised)

        BPMN lookup is done via self._registry (set by register_registry).
        """
        self._engine = engine
        _mcp_server = self._mcp  # captured for closure

        async def run_process(process_key: str, initial_inputs: dict) -> dict:
            """Execute a BPMN process via the WorkflowEngine. Returns {state, bpmn}."""
            state, bpmn = await engine._run_via_mcp(process_key, initial_inputs, _mcp_server)
            return {"state": state.model_dump(mode="json"), "bpmn": bpmn.to_dict()}

        self._mcp.tool(name="run_process")(run_process)
        logger.info("MCPFlowBridge: registered WorkflowEngine run_process tool")

    # ──────────────────────────────────────────────────────────────
    # Client access
    # ──────────────────────────────────────────────────────────────

    def get_client(self) -> Client:
        """
        Return an in-process MCP client backed by FastMCPTransport.

        Use as an async context manager:
            async with bridge.get_client() as c:
                result = await c.call_tool("run_process", {...})
        """
        return Client(FastMCPTransport(self._mcp))

    @property
    def mcp(self) -> FastMCP:
        return self._mcp
