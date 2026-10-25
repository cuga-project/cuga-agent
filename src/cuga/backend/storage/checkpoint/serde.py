"""Checkpoint serializer with an explicit msgpack allowlist (issue #774).

``langgraph-checkpoint`` 4.x rebuilds non-builtin types from checkpoints only if they
are allowlisted. A blocked type does **not** raise: it comes back as a plain dict (or
an Enum's raw value). An explicit list also blocks unlisted types when strict mode is
off, so ``CUGA_ALLOWED_MSGPACK_TYPES`` must cover every pydantic model, dataclass and
Enum reachable from the graph state schemas; ``tests/unit/storage/test_checkpoint_serde.py``
fails when one is missing. Entries are ``(module, ClassName)`` pairs; bare module names
match nothing.

Keep this module import-light: ``langgraph`` is imported inside the function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.checkpoint.serde.base import SerializerProtocol

_AGENT_STATE = "cuga.backend.cuga_graph.state.agent_state"
_API_PLANNER_HISTORY = "cuga.backend.cuga_graph.state.api_planner_history"
_SCHEMAS = "cuga.backend.cuga_graph.nodes.cuga_agent_core.schemas"
_FOLLOWUP = "cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model"
_SUPERVISOR_STATE = "cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state"
_CUGA_LITE_GRAPH = "cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph"

# (module, ClassName) pairs. langchain message classes, datetime, set, etc. are
# already in langgraph's SAFE_MSGPACK_TYPES and are not repeated here.
CUGA_ALLOWED_MSGPACK_TYPES: tuple[tuple[str, str], ...] = (
    # Graph state schemas
    (_AGENT_STATE, "AgentState"),
    (_SUPERVISOR_STATE, "CugaSupervisorState"),
    (_CUGA_LITE_GRAPH, "CugaLiteState"),
    # AgentState sub-models
    (_AGENT_STATE, "AnalyzeTaskAppsOutput"),
    (_AGENT_STATE, "SubTaskHistory"),
    (_API_PLANNER_HISTORY, "HistoricalAction"),
    (f"{_SCHEMAS}.api_models", "ApiDescription"),
    (f"{_SCHEMAS}.browser_models", "NextAgentPlan"),
    (f"{_SCHEMAS}.task_models", "AnalyzeTaskOutput"),
    (f"{_SCHEMAS}.task_models", "DecomposedTask"),
    (f"{_SCHEMAS}.task_models", "TaskDecompositionPlan"),
    # Human-in-the-loop follow-ups (AgentState.hitl_action / hitl_response)
    (_FOLLOWUP, "ActionResponse"),
    (_FOLLOWUP, "ActionType"),
    (_FOLLOWUP, "AdditionalData"),
    (_FOLLOWUP, "FollowUpAction"),
    (_FOLLOWUP, "SelectOption"),
    # CugaSupervisorState sub-models
    (_SUPERVISOR_STATE, "AgentInfo"),
)


def build_checkpoint_serde() -> SerializerProtocol:
    """Return the serializer every durable CUGA checkpointer uses."""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer(allowed_msgpack_modules=CUGA_ALLOWED_MSGPACK_TYPES)
