"""
CugaSupervisor State - State schema for supervisor subgraph
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage

from cuga.backend.cuga_graph.state.agent_state import AgentState


class AgentInfo(BaseModel):
    """Information about an available agent."""

    name: str
    description: Optional[str] = None
    type: str = "internal"  # internal or external
    capabilities: List[str] = Field(default_factory=list)
    status: str = "available"  # available, busy, error


class CugaSupervisorState(AgentState):
    """
    State for CugaSupervisor subgraph.

    Extends AgentState to maintain compatibility while adding supervisor-specific fields.
    """

    # Supervisor's own conversation history (separate from sub-agents)
    supervisor_chat_messages: Optional[List[BaseMessage]] = Field(default_factory=list)

    # Agent registry and selection
    available_agents: Dict[str, AgentInfo] = Field(default_factory=dict)
    selected_agents: List[str] = Field(default_factory=list)

    # Results and variables from sub-agents
    agent_results: Dict[str, Any] = Field(default_factory=dict)
    agent_chat_messages: Dict[str, List[BaseMessage]] = Field(default_factory=dict)
    agent_variables: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    # Supervisor's aggregated variables collected from sub-agents
    supervisor_variables: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    # Conversational mode fields (similar to CugaLiteState)
    tools_prepared: bool = False
    prepared_prompt: Optional[str] = None
    script: Optional[str] = None
    execution_complete: bool = False
    error: Optional[str] = None
    step_count: int = 0

    # Metadata for tracking
    supervisor_metadata: Dict[str, Any] = Field(default_factory=dict)

    cuga_lite_max_steps: Optional[int] = None

    class Config:
        arbitrary_types_allowed = True

    @property
    def supervisor_variables_manager(self):
        """Get a variables manager for supervisor variables."""
        from cuga.backend.cuga_graph.state.agent_state import StateVariablesManager

        # Create a temporary state-like object that uses supervisor_variables
        class SupervisorVariablesState:
            def __init__(self, supervisor_state):
                self._supervisor_state = supervisor_state

            @property
            def variables_storage(self):
                return self._supervisor_state.supervisor_variables

            @variables_storage.setter
            def variables_storage(self, value):
                self._supervisor_state.supervisor_variables = value

            @property
            def variable_counter_state(self):
                return self._supervisor_state.variable_counter_state

            @variable_counter_state.setter
            def variable_counter_state(self, value):
                self._supervisor_state.variable_counter_state = value

            @property
            def variable_creation_order(self):
                return self._supervisor_state.variable_creation_order

            @variable_creation_order.setter
            def variable_creation_order(self, value):
                self._supervisor_state.variable_creation_order = value

        temp_state = SupervisorVariablesState(self)
        return StateVariablesManager(temp_state)
