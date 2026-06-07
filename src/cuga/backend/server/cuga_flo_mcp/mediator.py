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

from copy import deepcopy
from datetime import datetime
import re
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


def _has_runtime_value(value: Any) -> bool:
    """Return True when a runtime value should override an RO input default."""
    return value is not None and value != ""


def _is_effectively_empty(value: Any) -> bool:
    """Return True when a schema value still looks like an unfilled default."""
    if isinstance(value, dict):
        return all(_is_effectively_empty(v) for v in value.values())
    if isinstance(value, list):
        return len(value) == 0
    return not _has_runtime_value(value)


def _normalize_travel_date(value: str) -> str:
    """Normalize common travel-agent demo date formats to YYYY-MM-DD when possible."""
    raw = value.strip().rstrip(".,")
    if not raw:
        return ""

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Prefer DD/MM/YYYY for ambiguous slash dates in the examples.
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if match:
        day, month, year = match.groups()
        try:
            return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
        except ValueError:
            return raw

    return raw


def _parse_month_range(start_day: str, end_day: str, month: str, year: str) -> tuple[str, str]:
    start_raw = f"{month} {start_day}, {year}"
    end_raw = f"{month} {end_day}, {year}"
    try:
        return (
            datetime.strptime(start_raw, "%B %d, %Y").strftime("%Y-%m-%d"),
            datetime.strptime(end_raw, "%B %d, %Y").strftime("%Y-%m-%d"),
        )
    except ValueError:
        try:
            return (
                datetime.strptime(start_raw, "%b %d, %Y").strftime("%Y-%m-%d"),
                datetime.strptime(end_raw, "%b %d, %Y").strftime("%Y-%m-%d"),
            )
        except ValueError:
            return start_raw, end_raw


def _extract_travel_request_from_message(message: str) -> Dict[str, str]:
    """
    Extract the travel-agent demo request fields from the user's original message.

    This intentionally supports the formats documented in
    ``supervisor_ordo_travel_agent.yaml`` and is enabled only by explicit flow
    configuration.
    """
    text = (message or "").strip()
    if not text:
        return {}

    # Documented compact format:
    # "John Doe, New York, Boston, 22/6/2026, 26/6/2026, economy"
    parts = [part.strip() for part in text.split(",")]
    if len(parts) >= 6:
        return {
            "traveler": parts[0],
            "origin": parts[1],
            "destination": parts[2],
            "start_date": _normalize_travel_date(parts[3]),
            "end_date": _normalize_travel_date(parts[4]),
            "cabin_preference": parts[5].replace(" class", "").strip().lower(),
        }

    # Actual supervisor delegation format observed in mcp_debug.log:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 6:
        return {
            "traveler": lines[0],
            "origin": lines[1],
            "destination": lines[2],
            "start_date": _normalize_travel_date(lines[3]),
            "end_date": _normalize_travel_date(lines[4]),
            "cabin_preference": lines[5].replace(" class", "").strip().lower(),
        }

    # Documented natural-ish format:
    # "Book a trip for Sarah Chen from NYC to SFO, March 15-20, 2024, economy class"
    natural = re.search(
        r"for\s+(?P<traveler>.+?)\s+from\s+(?P<origin>.+?)\s+to\s+"
        r"(?P<destination>.+?),\s+"
        r"(?P<month>[A-Za-z]+)\s+(?P<start_day>\d{1,2})\s*-\s*(?P<end_day>\d{1,2}),\s+"
        r"(?P<year>\d{4}),\s+(?P<cabin>[A-Za-z]+)(?:\s+class)?",
        text,
        flags=re.IGNORECASE,
    )
    if natural:
        start_date, end_date = _parse_month_range(
            natural.group("start_day"),
            natural.group("end_day"),
            natural.group("month"),
            natural.group("year"),
        )
        return {
            "traveler": natural.group("traveler").strip(),
            "origin": natural.group("origin").strip(),
            "destination": natural.group("destination").strip(),
            "start_date": start_date,
            "end_date": end_date,
            "cabin_preference": natural.group("cabin").strip().lower(),
        }

    return {}


def _describe_ro_input_extraction_skip(
    input_args: Dict[str, Any],
    initial_inputs: Dict[str, Any],
    extraction_config: Dict[str, Any] | None,
) -> str:
    """Explain why optional RO input extraction would not run."""
    if not extraction_config:
        return "no_extraction_config"

    target = extraction_config.get("target")
    source = extraction_config.get("source", "_user_message")
    mode = extraction_config.get("mode")

    if not target:
        return "missing_target"
    if target not in input_args:
        return f"target_not_in_input_args:{target}"
    if not isinstance(input_args[target], dict):
        return f"target_not_mapping:{target}"
    if not _is_effectively_empty(input_args[target]):
        return f"target_already_populated:{target}"

    source_value = initial_inputs.get(source)
    if not isinstance(source_value, str):
        return f"source_not_string:{source}"
    if not source_value.strip():
        return f"source_empty:{source}"

    if mode != "travel_request":
        return f"unsupported_mode:{mode}"

    extracted = _extract_travel_request_from_message(source_value)
    if not extracted:
        return "parser_returned_empty"

    return "unknown"


def _apply_ro_input_extraction(
    input_args: Dict[str, Any],
    initial_inputs: Dict[str, Any],
    extraction_config: Dict[str, Any] | None,
) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    """Apply optional config-driven extraction from user text into RO input args."""
    if not extraction_config:
        return input_args, []

    target = extraction_config.get("target")
    source = extraction_config.get("source", "_user_message")
    mode = extraction_config.get("mode")

    if not target or target not in input_args or not isinstance(input_args[target], dict):
        return input_args, []
    if not _is_effectively_empty(input_args[target]):
        return input_args, []

    source_value = initial_inputs.get(source)
    if not isinstance(source_value, str) or not source_value.strip():
        return input_args, []

    if mode != "travel_request":
        return input_args, []

    extracted = _extract_travel_request_from_message(source_value)
    if not extracted:
        return input_args, []

    merged = deepcopy(input_args)
    applied_keys = []
    for key in merged[target]:
        value = extracted.get(key)
        if _has_runtime_value(value):
            merged[target][key] = value
            applied_keys.append(key)

    if not applied_keys:
        return input_args, []

    return merged, [
        {
            "type": "input_extraction",
            "key": target,
            "source": source,
            "mode": mode,
            "merged_keys": applied_keys,
        }
    ]


def _merge_ro_input_args(
    input_args: Dict[str, Any],
    initial_inputs: Dict[str, Any],
) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    """
    Merge CUGA runtime process variables into the RO ``input_args`` schema.

    ``flow.input_args`` defines the shape expected by RO. Runtime values can
    arrive either in the same shape, for example ``{"request": {"origin": ...}}``,
    or as top-level process variables, for example ``{"origin": ...}``. The
    latter form is lifted into matching nested input-arg mappings so callers do
    not have to duplicate RO's exact nesting.
    """
    merged = deepcopy(input_args)
    merge_events: list[Dict[str, Any]] = []

    # First, merge exact schema keys. This preserves the existing behaviour and
    # gives an explicitly provided nested object (e.g. "request") precedence.
    for key, value in initial_inputs.items():
        if key.startswith("_") or key not in merged:
            continue

        if isinstance(merged[key], dict) and isinstance(value, dict):
            merged_keys = []
            for nested_key, nested_value in value.items():
                if _has_runtime_value(nested_value):
                    merged[key][nested_key] = nested_value
                    merged_keys.append(nested_key)
            if merged_keys:
                merge_events.append(
                    {"type": "deep_merge", "key": key, "merged_keys": merged_keys}
                )
        elif _has_runtime_value(value):
            merged[key] = value
            merge_events.append(
                {"type": "simple_assign", "key": key, "value_type": type(value).__name__}
            )

    # Then, lift top-level runtime variables into nested RO input schemas when
    # the nested slot is still empty/defaulted. This handles inputs such as
    # {"origin": "NYC"} for input_args {"request": {"origin": ""}}.
    for parent_key, parent_value in merged.items():
        if not isinstance(parent_value, dict):
            continue

        lifted_keys = []
        for key, value in initial_inputs.items():
            if key.startswith("_") or key in merged or key not in parent_value:
                continue
            if _has_runtime_value(value) and not _has_runtime_value(parent_value.get(key)):
                parent_value[key] = value
                lifted_keys.append(key)

        if lifted_keys:
            merge_events.append(
                {"type": "top_level_lift", "key": parent_key, "merged_keys": lifted_keys}
            )

    return merged, merge_events


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

        # Merge runtime inputs from FlowAgent into RO input_args.
        #
        # Start with flow.input_args from config (schema/defaults), then override
        # with runtime values from initial_inputs (provided by supervisor/user).
        # Runtime values may arrive either in the exact RO schema shape or as
        # top-level process variables that match nested schema fields.
        input_args = flow_config.get_ro_input_args()

        mcp_in("MCP2MCPMediator", "merge_input_args",
               input_args_keys=list(input_args.keys()),
               initial_inputs_keys=list(initial_inputs.keys()),
               request_in_initial=("request" in initial_inputs),
               request_value=str(initial_inputs.get("request", "NOT_FOUND"))[:100])

        input_args, merge_events = _merge_ro_input_args(input_args, initial_inputs)
        extraction_config = flow_config.flow_config.get("input_extraction")
        extraction_source = (
            extraction_config.get("source", "_user_message")
            if isinstance(extraction_config, dict)
            else "_user_message"
        )
        extraction_target = (
            extraction_config.get("target")
            if isinstance(extraction_config, dict)
            else None
        )
        mcp_in("MCP2MCPMediator", "input_extraction_check",
               extraction_config=extraction_config,
               source=extraction_source,
               source_preview=initial_inputs.get(extraction_source),
               target=extraction_target,
               target_before=input_args.get(extraction_target) if extraction_target else None,
               target_empty=_is_effectively_empty(input_args.get(extraction_target))
               if extraction_target in input_args
               else None)
        input_args, extraction_events = _apply_ro_input_extraction(
            input_args,
            initial_inputs,
            extraction_config,
        )
        if extraction_events:
            mcp_out("MCP2MCPMediator", "input_extraction_check",
                    applied=True,
                    events=extraction_events,
                    target_after=input_args.get(extraction_target) if extraction_target else None)
        else:
            mcp_out("MCP2MCPMediator", "input_extraction_check",
                    applied=False,
                    reason=_describe_ro_input_extraction_skip(
                        input_args,
                        initial_inputs,
                        extraction_config,
                    ))
        merge_events.extend(extraction_events)
        if extraction_events:
            accumulated_vars.update(input_args)
        for event in merge_events:
            if event["type"] == "input_extraction":
                mcp_out("MCP2MCPMediator", "merge_input_args.input_extraction",
                        key=event["key"],
                        source=event["source"],
                        mode=event["mode"],
                        merged_keys=event["merged_keys"])
                continue
            if event["type"] == "deep_merge":
                mcp_out("MCP2MCPMediator", "merge_input_args.deep_merge",
                        key=event["key"], merged_keys=event["merged_keys"])
            elif event["type"] == "simple_assign":
                mcp_out("MCP2MCPMediator", "merge_input_args.simple_assign",
                        key=event["key"], value_type=event["value_type"])
            elif event["type"] == "top_level_lift":
                mcp_out("MCP2MCPMediator", "merge_input_args.top_level_lift",
                        key=event["key"], merged_keys=event["merged_keys"])
        mcp_out("MCP2MCPMediator", "merge_input_args",
               merged_count=len(merge_events),
               final_input_args_keys=list(input_args.keys()),
               final_request=str(input_args.get("request", {}))[:100])
        
        # Legacy: handle _user_message -> name mapping for simple workflows
        user_message = initial_inputs.get("_user_message")
        if user_message and "name" in input_args and (not input_args.get("name") or input_args.get("name") == "world"):
            input_args["name"] = user_message

        async with self._ordo.get_client() as ordo_client:
            mcp_in("MCP2MCPMediator", "RO.register_workflow",
                   workflow_id=workflow_id,
                   input_keys=list(input_args.keys()),
                   request_payload=input_args.get("request"),
                   input_args_payload=input_args)
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
                        status=ro_result.get("status"),
                        ro_result_keys=list(ro_result.keys()),
                        final_response=ro_result.get("final_response"),
                        response=ro_result.get("response"))

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
                    
                    # Extract final state variables from RO result
                    ro_state = ro_result.get("state", {})
                    if isinstance(ro_state, dict) and ro_state.get("value"):
                        state_value = ro_state.get("value", {})
                        if isinstance(state_value, dict):
                            # Update accumulated_vars with final state from RO
                            accumulated_vars.update(state_value)
                            
                            # Extract approval_result details for logging
                            approval_result = state_value.get("approval_result", {})
                            reasoning = approval_result.get("reasoning", "") if isinstance(approval_result, dict) else ""
                            
                            mcp_out("MCP2MCPMediator", "RO.state_extracted",
                                    workflow_id=workflow_id,
                                    state_keys=list(state_value.keys()),
                                    approval_decision=state_value.get("approval_decision"),
                                    reasoning=reasoning,
                                    terminal_status=state_value.get("terminal_status"),
                                    terminal_reason=state_value.get("terminal_reason"))
                    
                    mcp_out("MCP2MCPMediator", "RO.workflow_completed",
                            workflow_id=workflow_id,
                            final_response=final_response,
                            ro_result_full=ro_result)
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
        mcp_out("MCP2MCPMediator", "final_state_created",
                workflow_id=workflow_id,
                process_variables=accumulated_vars,
                final_response=final_response,
                terminal_status=accumulated_vars.get("terminal_status"),
                terminal_reason=accumulated_vars.get("terminal_reason"),
                approval_decision=accumulated_vars.get("approval_decision"))
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
