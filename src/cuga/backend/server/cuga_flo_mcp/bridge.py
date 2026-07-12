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

import asyncio
from typing import TYPE_CHECKING

from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport
from loguru import logger

if TYPE_CHECKING:
    from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent import FlowAgent
    from cuga.backend.cuga_graph.nodes.cuga_flow.langgraph_engine import LangGraphWorkflowEngine
    from cuga.backend.cuga_graph.nodes.cuga_flow.process_registry import ProcessRegistry
    from cuga.backend.server.flowable.flowable_proxy import FlowableProxy


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
        self._proxy: "FlowableProxy | None" = None
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
            """Evaluate a hook via FlowAgent's hook evaluator, then realize the action via Flowable REST."""
            from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
            from cuga.backend.cuga_graph.nodes.cuga_flow.hook_manager import HookAction, HookResult

            ctx_obj = ControlPointFlowKnowledge.from_dict(ctx)
            hook = next((h for h in fa.hooks if h.id == hook_id), None)
            if hook is None:
                result = HookResult(action=HookAction.CONTINUE, message=f"Hook {hook_id!r} not found")
                ActivityTracker().collect_step(Step(
                    name=f"Hook: {hook_id}",
                    data=f"{result.action.value} — {result.message}",
                ))
            else:
                result = await fa._handle_hook(hook, ctx_obj)

            await self._realize_hook_action(result, ctx)
            return result.to_dict()

        async def complete_process(process_key: str, state: dict, bpmn: "dict | None" = None) -> dict:
            """Called by engine when process reaches terminal state."""
            if bpmn is None:
                bpmn = self._registry.get_bpmn_process(process_key).to_dict()
            return await fa._handle_complete(process_key, state, bpmn)

        for fn, tool_name in [
            (execute_task, "execute_task"),
            (route_gateway, "route_gateway"),
            (evaluate_hook, "evaluate_hook"),
            (complete_process, "complete_process"),
        ]:
            self._mcp.tool(name=tool_name)(fn)

        logger.info(f"MCPFlowBridge: registered FlowAgent tools for process '{fa.process_key}'")

    async def _realize_hook_action(self, result: "HookResult", ctx: dict) -> None:
        """Forward a HookResult to FlowableProxy for REST realisation (Flowable-engine mode only)."""
        if self._proxy is None:
            return  # LangGraph engine mode — hook realisation handled by the graph

        process_instance_id = ctx.get("process_instance_id")
        if not process_instance_id:
            logger.warning("_realize_hook_action: no process_instance_id in ctx")
            return

        current_activity_id = ctx.get("element_id")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._proxy.realize_hook_action(
                result, process_instance_id, current_activity_id=current_activity_id
            ),
        )

    def register_flowable_engine(
        self,
        proxy: "FlowableProxy",
        deploy: bool = False,
        process_definition_key: "str | None" = None,
        callback_port: int = 8090,
    ) -> None:
        """
        Register a FlowableProxy as the run_process MCP tool and expose the MCP
        server over HTTP so Flowable can call complete_process (and other tools)
        via a REST callback when the process ends.

        Args:
            proxy: FlowableProxy client.
            deploy: If True, deploy the BPMN to Flowable before the first run.
            process_definition_key: Flowable process definition key to invoke.
            callback_port: Port on which the MCP HTTP server listens for Flowable callbacks.

        Tool registered:
          run_process(process_key, initial_inputs) → dict
        """
        self._proxy = proxy
        _deployed: set = set()
        _http_started = False

        async def run_process(process_key: str, initial_inputs: dict) -> dict:
            """Start Flowable process; Flowable calls complete_process via MCP HTTP on completion."""
            nonlocal _http_started
            if not _http_started:
                _http_started = True
                import uvicorn
                _server = uvicorn.Server(
                    uvicorn.Config(
                        self._mcp.http_app(stateless_http=True),
                        host="0.0.0.0",
                        port=callback_port,
                        log_level="warning",
                    )
                )
                asyncio.create_task(_server.serve())
                while not _server.started:
                    await asyncio.sleep(0.05)
                logger.info(f"MCPFlowBridge: MCP HTTP server listening on port {callback_port}")

            bpmn = self._registry.get_bpmn_process(process_key)
            loop = asyncio.get_running_loop()
            flowable_key = process_definition_key or bpmn.id

            if deploy and process_key not in _deployed:
                bpmn_path = self._registry._definitions[process_key].bpmn_path
                await loop.run_in_executor(None, lambda: proxy.deploy(bpmn_path))
                _deployed.add(process_key)

            start_vars = {
                **initial_inputs,
                "cugaProcessKey": process_key,
            }
            await loop.run_in_executor(
                None, lambda: proxy.start_process(flowable_key, variables=start_vars)
            )
            return {}

        self._mcp.tool(name="run_process")(run_process)
        logger.info(
            f"MCPFlowBridge: registered FlowableProxy run_process tool "
            f"(MCP HTTP callback on port {callback_port})"
        )

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
            """Start process execution; completion signalled via complete_process tool."""
            asyncio.create_task(engine._run_via_mcp(process_key, initial_inputs, _mcp_server))
            return {}

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
