"""
WorkflowEngine - Abstract API for BPMN process execution.

Defines ControlPointContext and the WorkflowEngine ABC.
The engine holds process model, instance state, and execution history.
It calls back to the FlowAgent harness at annotated control points via MCP,
embedding full context in each ControlPointContext.

ControlOverlay has moved to langgraph_engine.py as a private _ControlOverlay —
it is an internal LangGraph construct, no longer part of the public API.
"""

import dataclasses
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNFlow, BPMNProcess
from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent_state import FlowState


@dataclass
class ControlPointContext:
    """
    Context the engine embeds in every callback to the FlowAgent harness.

    FlowAgent MUST NOT store these fields between callbacks — it reads them
    fresh from the engine-provided snapshot each time.

    Fields the ENGINE provides:
      current_state, execution_history, process_model_summary, task_instruction

    Fields specific to the control-point type:
      available_flows  — gateways: outgoing paths available for routing
      edge_id          — hooks: the intercepted flow edge ID
    """

    process_instance_id: str
    element_id: str
    element_name: str
    current_state: FlowState
    execution_history: List[str]
    process_model_summary: Dict[str, Any]
    # Engine discloses this from the YAML annotation; FlowAgent does not re-derive it
    task_instruction: Optional[str] = None
    # Gateways only
    available_flows: Optional[List[BPMNFlow]] = None
    # Hooks only
    edge_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "process_instance_id": self.process_instance_id,
            "element_id": self.element_id,
            "element_name": self.element_name,
            "current_state": self.current_state.model_dump(mode="json"),
            "execution_history": list(self.execution_history),
            "process_model_summary": self.process_model_summary,
            "task_instruction": self.task_instruction,
            "available_flows": [dataclasses.asdict(f) for f in (self.available_flows or [])],
            "edge_id": self.edge_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ControlPointContext":
        flows = [BPMNFlow(**f) for f in (d.get("available_flows") or [])]
        return cls(
            process_instance_id=d["process_instance_id"],
            element_id=d["element_id"],
            element_name=d["element_name"],
            current_state=FlowState.model_validate(d["current_state"]),
            execution_history=d.get("execution_history", []),
            process_model_summary=d.get("process_model_summary", {}),
            task_instruction=d.get("task_instruction"),
            available_flows=flows or None,
            edge_id=d.get("edge_id"),
        )


class WorkflowEngine(ABC):
    """
    Abstract execution backend for BPMN processes.

    Holds process model, instance state, and execution history during a run.
    Communicates with the FlowAgent harness exclusively via the MCP bridge —
    calling execute_task / route_gateway / evaluate_hook tools at control points.
    """

    @abstractmethod
    async def _run_via_mcp(
        self,
        process: BPMNProcess,
        initial_inputs: Dict[str, Any],
        mcp_server: Any,
    ) -> FlowState:
        """
        Execute the process using the given FastMCP server for harness callbacks.
        The engine fetches static config and calls FlowAgent tools via MCP.
        Returns terminal FlowState when the process completes or halts.
        """

    def inspect_model(self, process: BPMNProcess) -> Dict[str, Any]:
        return {
            "process_id": process.id,
            "process_name": process.name,
            "elements": {
                eid: {"type": e.element_type, "name": e.name} for eid, e in process.elements.items()
            },
            "flows": [
                {
                    "id": f.id,
                    "source": f.source_ref,
                    "target": f.target_ref,
                    "condition": f.condition,
                }
                for f in process.flows
            ],
            "start_event": process.start_event,
            "end_events": list(process.end_events),
        }

    def inspect_instance(self, state: FlowState) -> Dict[str, Any]:
        return {
            "process_id": state.process_id,
            "process_name": state.process_name,
            "is_complete": state.is_complete,
            "is_halted": state.is_halted,
            "execution_path": list(state.execution_path),
        }

    def get_execution_history(self, state: FlowState) -> List[str]:
        return list(state.execution_path)
