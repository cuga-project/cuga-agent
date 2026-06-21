"""
FlowState - Extended state management for BPMN-based process execution.

Extends AgentState with flow-specific fields for tracking process execution,
variables, hooks, and graph modifications.
"""

from typing import Dict, List, Any, Optional, Annotated
import operator
from datetime import datetime
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.state.agent_state import AgentState


class GraphModification(BaseModel):
    """Record of a graph modification made by a hook."""

    hook_id: str = Field(description="ID of the hook that made the modification")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    modification_type: str = Field(
        description="Type of modification: add_node, remove_node, add_edge, remove_edge, etc."
    )
    details: Dict[str, Any] = Field(default_factory=dict, description="Details of the modification")
    reason: str = Field(description="Explanation for why the modification was made")



class FlowState(AgentState):
    """
    Extended state for BPMN-based flow execution.

    Adds flow-specific tracking on top of AgentState:
    - Process variables and execution path
    - Hook evaluations and graph modifications
    - BPMN element tracking
    - Flow completion status
    """

    # Override AgentState required fields with defaults for FlowAgent
    input: str = Field(default="", description="Input message (not used in FlowAgent)")
    url: str = Field(default="", description="URL (not used in FlowAgent)")

    # Process execution tracking
    process_id: str = Field(default="", description="Unique process instance ID")
    process_name: str = Field(default="", description="Name of the BPMN process")
    current_node_id: str = Field(default="", description="Current BPMN element ID")
    # operator.add merges lists from parallel branches instead of overwriting
    execution_path: Annotated[List[str], operator.add] = Field(
        default_factory=list, description="Sequence of executed node IDs"
    )

    # Process variables — dict merged across parallel branches
    process_variables: Annotated[Dict[str, Any], lambda a, b: {**a, **b}] = Field(
        default_factory=dict, description="BPMN process variables accessible to all nodes"
    )

    # Adaptation tracking — lists merged across parallel branches
    graph_modifications: Annotated[List[GraphModification], operator.add] = Field(
        default_factory=list, description="History of graph modifications made by hooks"
    )

    # Flow control
    is_complete: bool = Field(default=False, description="Whether process has reached end event")
    is_halted: bool = Field(default=False, description="Whether process was halted by a hook")
    halt_reason: str = Field(default="", description="Reason for halting if is_halted=True")

    # Gateway decisions — dict merged across parallel branches
    gateway_decisions: Annotated[Dict[str, str], lambda a, b: {**a, **b}] = Field(
        default_factory=dict, description="Map of gateway_id -> chosen_path for audit trail"
    )

    # Task execution results — dict merged across parallel branches
    task_results: Annotated[Dict[str, Any], lambda a, b: {**a, **b}] = Field(
        default_factory=dict, description="Map of task_id -> execution result"
    )

    # Metadata
    start_time: Optional[datetime] = Field(default=None, description="Process start timestamp")
    end_time: Optional[datetime] = Field(default=None, description="Process end timestamp")

    def add_graph_modification(
        self, hook_id: str, modification_type: str, details: Dict[str, Any], reason: str
    ) -> None:
        """Add a graph modification record."""
        modification = GraphModification(
            hook_id=hook_id, modification_type=modification_type, details=details, reason=reason
        )
        self.graph_modifications.append(modification)

    def record_gateway_decision(self, gateway_id: str, chosen_path: str) -> None:
        """Record a gateway routing decision."""
        self.gateway_decisions[gateway_id] = chosen_path

    def record_task_result(self, task_id: str, result: Any) -> None:
        """Record a task execution result."""
        self.task_results[task_id] = result

    def get_process_variable(self, name: str, default: Any = None) -> Any:
        """Get a process variable value."""
        return self.process_variables.get(name, default)

    def set_process_variable(self, name: str, value: Any) -> None:
        """Set a process variable value."""
        self.process_variables[name] = value

    def mark_complete(self) -> None:
        """Mark the process as complete."""
        self.is_complete = True
        self.end_time = datetime.utcnow()

    def halt(self, reason: str) -> None:
        """Halt the process execution."""
        self.is_halted = True
        self.halt_reason = reason
        self.end_time = datetime.utcnow()

    def get_execution_summary(self) -> Dict[str, Any]:
        """Get a summary of the execution for logging/debugging."""
        duration = None
        if self.start_time and self.end_time:
            duration = (self.end_time - self.start_time).total_seconds()

        return {
            "process_id": self.process_id,
            "process_name": self.process_name,
            "status": "complete" if self.is_complete else ("halted" if self.is_halted else "running"),
            "execution_path": self.execution_path,
            "duration_seconds": duration,
            "graph_modifications": len(self.graph_modifications),
            "gateway_decisions": len(self.gateway_decisions),
            "tasks_executed": len(self.task_results),
            "process_variables": list(self.process_variables.keys()),
        }


# Made with Bob
