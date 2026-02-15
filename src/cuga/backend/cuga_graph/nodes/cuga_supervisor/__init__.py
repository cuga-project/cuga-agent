"""
CugaSupervisor - Supervisor subgraph for orchestrating multiple CugaAgent instances
"""

from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_graph import (
    create_cuga_supervisor_graph,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import (
    CugaSupervisorState,
    AgentInfo,
)

__all__ = [
    "create_cuga_supervisor_graph",
    "CugaSupervisorState",
    "AgentInfo",
]
