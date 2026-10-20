"""Checkpoint serializer with an explicit msgpack allowlist (issue #774, W16.1).

``langgraph-checkpoint`` 4.x reconstructs non-builtin types from checkpoints
only if they are allowlisted. Without a list it allows everything and warns
(default) or blocks everything outside ``SAFE_MSGPACK_TYPES``
(``LANGGRAPH_STRICT_MSGPACK=true``). A blocked type does **not** raise: it comes
back as a plain dict (or the raw enum value), so a gap here shows up as subtle
state corruption, not as an error.

``CUGA_ALLOWED_MSGPACK_TYPES`` must therefore be complete on its own:

- An explicit ``allowed_msgpack_modules`` list blocks unlisted types even when
  strict mode is off.
- ``StateGraph.compile()`` only merges its schema-derived allowlist into the
  saver when strict mode is on, and only for that graph's own schemas. A parent
  graph reading a subgraph's checkpoint (or a reader with no graph at all)
  gets no merge.

Entries are ``(module, ClassName)`` pairs, the form ``JsonPlusSerializer``
matches on. Bare module names (or 1-tuples) are accepted silently but match
nothing. The pairs are strings, so building them imports nothing.

The list covers every pydantic model, dataclass and Enum reachable from the
graph state schemas (``AgentState``, ``CugaSupervisorState``, ``CugaLiteState``).
``tests/unit/storage/test_checkpoint_serde.py`` walks those schemas and the
compiled graphs and fails when a reachable type is missing here. Types that
only appear inside ``Any``-typed fields (for example tool results in
``variables_storage``) are not covered.

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


def build_checkpoint_serde(*, encrypt: bool | None = None) -> SerializerProtocol:
    """Return the serializer every CUGA checkpointer should use.

    ``encrypt`` is the seam for W16.2 (optional ``EncryptedSerializer``).
    ``None`` will mean "follow ``storage.checkpoint_encryption``" once that
    setting exists; until then ``None`` and ``False`` both return the plain
    serializer, and ``True`` raises instead of silently storing plaintext.
    """
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    serde = JsonPlusSerializer(allowed_msgpack_modules=CUGA_ALLOWED_MSGPACK_TYPES)
    return _maybe_encrypt(serde, encrypt)


def _maybe_encrypt(serde: SerializerProtocol, encrypt: bool | None) -> SerializerProtocol:
    if encrypt:
        raise NotImplementedError("Checkpoint encryption is not implemented yet (issue #774, W16.2).")
    return serde
