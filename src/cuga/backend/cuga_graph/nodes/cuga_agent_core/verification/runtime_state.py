"""Verifier-owned session state and captured runtime evidence records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cuga.backend.memory_graph import MemoryGraph


@dataclass(frozen=True)
class _ExecutionRecord:
    """One completed runtime tool invocation captured by the sandbox boundary.

    The record is already atomic by construction: tool identity, the exact runtime
    parameters, and the observed return/exception payload remain in one sentence.
    It is therefore never sent through semantic decomposition.
    """

    record_id: str
    tool_name: str
    parameters_json: str
    output_json: str
    content: str


@dataclass(frozen=True)
class _KnowledgeBaseRetrievalRecord:
    """One completed knowledge-base retrieval captured by the sandbox boundary.

    Unlike ordinary execution records, the retrieved content is semantically
    decomposed into a dedicated persistent knowledge-base graph. Tool/query
    provenance is retained as source metadata rather than prepended to the
    retrieved text, so the graph represents the KB content itself.
    """

    record_id: str
    tool_name: str
    parameters_json: str
    content: str


@dataclass(frozen=True)
class _ReasoningStepRecord:
    """One accepted temporary reasoning step owned by the verifier.

    Reasoning steps are intentionally kept separate from conversational STATE
    and authority evidence. They may be shown to later verifier calls as
    trajectory context, but they never become authoritative grounding facts.
    """

    step_id: str
    content: str
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]


@dataclass
class _PromptVerificationState:
    """Private verifier-owned graph state for one active CUGA session.

    Authority is initialized through two independent hooks:
    ``playbook_graph`` is snapshotted by the SDK from initially configured
    Playbooks, while ``cuga_policy_graph`` is initialized later from CugaLite's
    effective behavioral prompt path.

    ``state_graph`` is persistent conversational evidence for the session (user,
    approved assistant, and committed reasoning state only). Completed tool/execution
    observations live exclusively in ``execution_graph``. ``state_context_cursor`` is
    the number of raw model-context messages already processed for STATE ingestion.
    ``state_context_signatures`` records the processed raw prefix so we can detect
    truncation or rewriting and safely fall back to a full state rebuild.

    ``execution_graph`` is a separate append-only factual audit graph for completed
    non-KB tool invocations whose observations can matter to later verification.
    Each node contains the tool name, exact runtime parameters, and observed output
    in the same atomic statement. These nodes are not decomposed and have no edges;
    they participate only in normal candidate-conditioned retrieval. Pure filesystem
    navigation/maintenance ``shell`` operations are intentionally omitted from every
    persistent verifier evidence graph because their listings, paths, permissions,
    copy/move/delete acknowledgements, and similar workspace state are operational
    scaffolding rather than user/domain evidence.

    ``knowledge_base_graph`` is a separate persistent semantic graph containing
    content returned by dedicated knowledge-base retrieval tools (``KB_search_*``)
    plus successful ``shell`` invocations whose complete command is classified as
    read-only knowledge-base content inspection. Those outputs are decomposed,
    linked, and embedded like document evidence so large retrievals can be filtered
    through the same graph retrieval/traversal pipeline. They are deliberately
    excluded from ``execution_graph`` to avoid indexing the same retrieval output
    twice.

    ``reasoning_graph`` is a separate temporary trajectory containing only
    accepted intermediate reasoning steps for the current internal generation
    cycle. Verified reasoning propositions may bind persistent logic slots and
    participate in formal SAT checks. Once a final terminal answer is accepted,
    the reasoning graph is transferred into STATE before the temporary trace is
    cleared.
    """

    owner_session_id: str | None = None
    graph_session_id: str | None = None
    cuga_policy_initialized: bool = False
    playbook_initialized: bool = False
    cuga_policy_graph: MemoryGraph | None = None
    playbook_graph: MemoryGraph | None = None
    runtime_initialized: bool = False
    runtime_facts: dict[str, Any] = field(default_factory=dict)
    # Mutable CUGA VariablesManager registered at runtime initialization. It is
    # deliberately kept outside STATE/evidence: it is used only to resolve the
    # Python meaning of names in pre-execution tool-call candidates.
    runtime_variables_manager: Any | None = None
    state_graph: MemoryGraph | None = None
    state_context_cursor: int = 0
    state_context_signatures: list[str] = field(default_factory=list)
    execution_graph: MemoryGraph | None = None
    execution_records: list[_ExecutionRecord] = field(default_factory=list)
    execution_graph_cursor: int = 0
    knowledge_base_graph: MemoryGraph | None = None
    knowledge_base_records: list[_KnowledgeBaseRetrievalRecord] = field(default_factory=list)
    knowledge_base_graph_cursor: int = 0
    reasoning_graph: MemoryGraph | None = None
    reasoning_steps: list[_ReasoningStepRecord] = field(default_factory=list)
    committed_reasoning_graph: MemoryGraph | None = None


_VERIFICATION_STATE = _PromptVerificationState()
