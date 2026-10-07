from __future__ import annotations

import asyncio
import datetime as datetime_module
import hashlib
import json
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from cuga.backend.memory_graph import (
    GraphBuildRequest,
    GraphBuilder,
    MemoryGraph,
    NodeKind,
    SourceType,
    analyze_entailment,
    link_new_nodes,
    link_new_nodes_to_logic_slots,
    match_nodes_to_logic_slots,
)
from cuga.backend.memory_graph.graph_serialization import (
    compute_prompt_hash,
    load_graph_for_prompt,
    save_graph,
)
from cuga.backend.memory_graph.atomic_payload_spacy import extract_atomic_payloads_spacy
from cuga.backend.memory_graph.retrieval import (
    build_retrieval_text,
    retrieval_text_hash,
)
from cuga.backend.memory_graph.retrieval_embedding_qwen import (
    embed_retrieval_texts_qwen,
    embedding_model_name as qwen_embedding_model_name,
    retrieval_embeddings_enabled,
)
from cuga.backend.memory_graph.schemas import (
    MemoryNode,
    RetrievalEmbedding,
    SourceReference,
    SourceSpan,
)
from cuga.config import settings

from .candidate_calls import (
    _PYTHON_BLOCK_RE,
    _ResolvedExpression,
    _extract_candidate_calls_dry_run,
    _extract_candidate_calls_static,
)
from .candidate_partition import (
    _build_candidate_verification_bulks,
)
from .errors import PromptVerificationError
from .model_client import invoke_verifier_decision
from .runtime_state import (
    _ExecutionRecord,
    _KnowledgeBaseRetrievalRecord,
    _ReasoningStepRecord,
    _VERIFICATION_STATE,
)
from .retrieval_context import (
    _CandidateQueryContextEntry,
    _append_previous_verifier_rejection_context,
    _build_candidate_query_context,
    _candidate_atom_order_key,
    _one_line,
    _render_candidate_query_context,
)
from .system_prompts import system_prompt_for_mode


CandidateKind = Literal["reasoning", "terminal", "tool_execution"]


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    reason: str = ""


@dataclass(frozen=True)
class AuthoritySource:
    """One initially configured playbook source supplied by CUGA.

    The verifier deliberately does not discover playbooks from chat history.
    CUGA passes the initially configured playbooks directly through the
    authority-initialization hook before the first user message is processed.
    """

    source_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class CandidateAtomDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_atom_id: str = Field(min_length=1)
    verdict: Literal[
        "supported",
        "contradicted",
        "insufficient",
        "not_applicable",
    ]
    reason: str = Field(min_length=1)


class VerificationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    atoms: list[CandidateAtomDecision] = Field(min_length=1)


class CandidateContextDecision(BaseModel):
    """Final raw-candidate decision against reconstructed source context."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["approved", "rejected"]
    reason: str = ""
    violated_context_ids: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class _EvidenceSource:
    source_id: str
    source_type: SourceType
    content: str
    metadata: dict[str, Any]


_REASONING_PREFIX_RE = re.compile(r"^\s*reasoning\s*:\s*", flags=re.IGNORECASE)
# Dedicated KB retrieval tools always route into the semantic KB graph. ``shell``
# is classified per invocation:
#   1. successful read-only KB-content inspection -> knowledge_base_graph;
#   2. pure filesystem navigation/maintenance -> omitted from persistent evidence;
#   3. everything else -> ordinary execution evidence.
_KNOWLEDGE_BASE_RETRIEVAL_TOOL_PREFIXES = ("KB_search_",)
_SHELL_KB_READ_ONLY_EXECUTABLES = frozenset(
    {
        "cat",
        "tac",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ripgrep",
        "less",
        "more",
        "head",
        "tail",
        "cut",
        "sort",
        "uniq",
        "tr",
        "paste",
        "join",
        "comm",
        "wc",
        "strings",
        "nl",
        "fold",
        "fmt",
        "column",
    }
)
_SHELL_FILESYSTEM_ONLY_EXECUTABLES = frozenset(
    {
        # Navigation / discovery of workspace structure. These help the agent find
        # files but do not establish user or domain facts.
        "ls",
        "la",
        "ll",
        "dir",
        "tree",
        "pwd",
        "cd",
        "dirs",
        "pushd",
        "popd",
        "find",
        "locate",
        "stat",
        "file",
        "du",
        "df",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "which",
        "whereis",
        # Filesystem maintenance / mutation. Their outputs are operational state,
        # not semantic evidence for this verifier.
        "cp",
        "mv",
        "rm",
        "rmdir",
        "mkdir",
        "touch",
        "ln",
        "chmod",
        "chown",
        "chgrp",
        "install",
        "truncate",
        "sync",
    }
)
_SHELL_COMMAND_SEPARATORS = frozenset({"|", "&&", "||", ";"})
_SHELL_FORBIDDEN_CONTROL_TOKENS = frozenset({"&", ">", ">>", "<", "<<", "<<<", "<>"})
# Bound concurrent source builds to limit model-call fanout.
_GRAPH_BUILD_CONCURRENCY = 4

# Split non-code candidates into contiguous verification units when enabled.
# Tool-execution candidates remain whole so call ordering and dataflow are visible.
PROMPT_VERIFIER_BULK_VERIFICATION_ENABLED = (
    os.environ.get("PROMPT_VERIFIER_BULK_VERIFICATION_ENABLED", "0")
    .strip()
    .casefold()
    in {"1", "true", "yes", "on"}
)

# Persist expensive, session-independent authority graphs. The
# graph index and graph JSON files live together so indexed relative paths remain
# stable regardless of the process working directory used during later loads.
_GRAPH_CACHE_DIR = Path(".memory_graph_cache")
_GRAPH_CACHE_INDEX = _GRAPH_CACHE_DIR / "memory_graphs.json"
# Version verifier authority-graph cache identities whenever reconstruction
# depends on new construction-time metadata. This avoids globally invalidating
# unrelated serialized MemoryGraphs.
_AUTHORITY_GRAPH_CACHE_SCHEMA = "semantic_context_dependency_closure_v1"

# Cache the context-independent semantic fragment produced for each ordinary
# evidence source before that fragment is linked laterally into the session graph.
# This lets repeated document/file retrievals and repeated conversational/candidate
# decompositions reuse the expensive GraphBuilder result while still recomputing
# context-dependent cross-source relations on every merge.
_SEMANTIC_SUBGRAPH_CACHE_SCHEMA = "semantic_source_fragment_v1"
_SEMANTIC_SUBGRAPH_CACHE_GRAPH_TYPE = "semantic_subgraph"

# These metadata fields identify one runtime occurrence/provenance record but do
# not change the semantic text that GraphBuilder decomposes. Excluding them from
# the cache identity lets the same retrieved content reuse one cached fragment
# across sessions. All other metadata remains part of the identity,
# so construction flags such as candidate/retrieval-only/logic-enrichment modes
# cannot accidentally share incompatible cached graphs.
_SEMANTIC_SUBGRAPH_OCCURRENCE_METADATA_KEYS = frozenset(
    {
        "context_index",
        "retrieval_tool_name",
        "retrieval_parameters_json",
        "retrieval_record_id",
        "reasoning_step_id",
    }
)

# Per-process duplicate-miss suppression. Two identical sources can be requested
# concurrently by the same verifier call; only the first should pay the model
# decomposition cost and populate the persistent cache.
_SEMANTIC_SUBGRAPH_CACHE_LOCKS: dict[
    str, tuple[asyncio.AbstractEventLoop, asyncio.Lock]
] = {}

# Retrieve independently from the two evidence spaces. The CUGA-policy and
# Playbook graphs are combined into one logical ADHERENCE graph; STATE remains
# separate so normative and factual evidence cannot silently substitute for one
# another.


def _strip_reasoning_prefix(candidate: str) -> str:
    """Return reasoning content without the external REASONING: protocol marker."""
    return _REASONING_PREFIX_RE.sub("", candidate, count=1).strip()


def _reasoning_source_type() -> SourceType:
    """Use the reasoning source type, retaining a fallback for older schemas."""
    return getattr(SourceType, "REASONING", SourceType.ASSISTANT_MESSAGE)


def _next_reasoning_step_id() -> str:
    return f"R{len(_VERIFICATION_STATE.reasoning_steps) + 1}"


def _reasoning_history_lines() -> list[str]:
    return [
        f"{step.step_id}: {_one_line(step.content)}"
        for step in _VERIFICATION_STATE.reasoning_steps
    ]


def _reset_reasoning_trace(
    *,
    reason: str,
    preserve_logic_bindings: bool = False,
) -> None:
    reasoning_graph = _VERIFICATION_STATE.reasoning_graph
    reasoning_node_ids = set(reasoning_graph.nodes) if reasoning_graph is not None else set()

    if reasoning_node_ids and not preserve_logic_bindings:
        for graph in (
            _VERIFICATION_STATE.cuga_policy_graph,
            _VERIFICATION_STATE.playbook_graph,
            _VERIFICATION_STATE.state_graph,
        ):
            if graph is not None:
                graph.remove_logic_bindings(reasoning_node_ids)

    _VERIFICATION_STATE.reasoning_graph = None
    _VERIFICATION_STATE.reasoning_steps = []


def _logic_target_graphs(*, include_reasoning: bool = True) -> list[MemoryGraph]:
    graphs: list[MemoryGraph] = []
    for graph in (
        _VERIFICATION_STATE.cuga_policy_graph,
        _VERIFICATION_STATE.playbook_graph,
        _VERIFICATION_STATE.knowledge_base_graph,
        _VERIFICATION_STATE.state_graph,
        _VERIFICATION_STATE.reasoning_graph if include_reasoning else None,
    ):
        if graph is not None and graph not in graphs:
            graphs.append(graph)
    return graphs


async def _commit_reasoning_candidate_graph(
    *,
    candidate_graph: MemoryGraph,
    content: str,
    step_id: str,
) -> None:
    """Commit an already-verified reasoning graph and augment logic slots.

    Accepted reasoning remains separate from external STATE while the trajectory
    is active, but its verified atomic propositions are allowed to bind persistent
    logic slots and therefore participate in deterministic SAT checks.
    """
    reasoning_graph = _VERIFICATION_STATE.reasoning_graph
    if reasoning_graph is None:
        reasoning_graph = MemoryGraph()
        _VERIFICATION_STATE.reasoning_graph = reasoning_graph

    new_node_ids: set[str] = set()
    for node in candidate_graph.nodes.values():
        if node.id in reasoning_graph.nodes:
            raise PromptVerificationError(
                f"Duplicate reasoning node ID while committing {step_id}: {node.id}"
            )
        reasoning_graph.add_node(node)
        new_node_ids.add(node.id)

    for edge in candidate_graph.edges.values():
        if edge.id in reasoning_graph.edges:
            raise PromptVerificationError(
                f"Duplicate reasoning edge ID while committing {step_id}: {edge.id}"
            )
        reasoning_graph.add_edge(edge)

    reasoning_graph.merge_logic_layer(candidate_graph.logic_layer)

    edge_ids_before_linking = set(reasoning_graph.edges)
    if new_node_ids:
        await asyncio.to_thread(
            link_new_nodes,
            reasoning_graph,
            new_node_ids,
        )
        await asyncio.to_thread(
            link_new_nodes_to_logic_slots,
            source_graph=reasoning_graph,
            new_node_ids=new_node_ids,
            target_graphs=_logic_target_graphs(include_reasoning=True),
        )

    step_edge_ids = {
        edge_id
        for edge_id, edge in reasoning_graph.edges.items()
        if edge.source_id in new_node_ids or edge.target_id in new_node_ids
    }
    step_edge_ids.update(set(reasoning_graph.edges) - edge_ids_before_linking)

    _VERIFICATION_STATE.reasoning_steps.append(
        _ReasoningStepRecord(
            step_id=step_id,
            content=content,
            node_ids=tuple(sorted(new_node_ids)),
            edge_ids=tuple(sorted(step_edge_ids)),
        )
    )



def _transfer_reasoning_to_state(*, reason: str) -> None:
    """Transfer verified reasoning into persistent STATE before trace deletion."""
    reasoning_graph = _VERIFICATION_STATE.reasoning_graph
    if reasoning_graph is None or not reasoning_graph.nodes:
        _reset_reasoning_trace(reason=reason)
        return

    state_graph = _VERIFICATION_STATE.state_graph
    if state_graph is None:
        raise PromptVerificationError(
            "Cannot commit verified reasoning before the STATE graph exists"
        )

    state_graph.merge_graph(reasoning_graph)

    committed = _VERIFICATION_STATE.committed_reasoning_graph
    if committed is None:
        committed = MemoryGraph()
        _VERIFICATION_STATE.committed_reasoning_graph = committed
    committed.merge_graph(reasoning_graph)

    _reset_reasoning_trace(reason=reason, preserve_logic_bindings=True)


def commit_reasoning_trace_to_state() -> None:
    """Public finalization hook for an accepted terminal trajectory."""
    _transfer_reasoning_to_state(reason="accepted_terminal")


def reset_reasoning_trace() -> None:
    """Public hook for orchestration code to abandon the current reasoning cycle."""
    _reset_reasoning_trace(reason="external_reset")


async def _extract_candidate_calls(
    candidate: str,
    *,
    runtime_variables: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Use restricted dry-run, falling back to static parsing when needed."""
    blocks = _PYTHON_BLOCK_RE.findall(candidate)
    if not blocks:
        return []

    tool_names = {
        str(name).strip()
        for name in (
            _VERIFICATION_STATE.runtime_facts.get("execution_context_tool_names", [])
            if isinstance(_VERIFICATION_STATE.runtime_facts, dict)
            else []
        )
        if str(name).strip()
    }

    try:
        extracted = await _extract_candidate_calls_dry_run(
            candidate,
            runtime_variables=runtime_variables,
            tool_names=tool_names,
        )
        logger.debug(
            "Prompt verifier candidate dry-run succeeded: calls={} runtime_variables={}",
            len(extracted),
            sorted((runtime_variables or {}).keys()),
        )
        return extracted
    except Exception as exc:
        logger.debug(
            "Prompt verifier candidate dry-run fell back to static extraction: "
            "{}: {}",
            type(exc).__name__,
            exc,
        )
        return _extract_candidate_calls_static(
            candidate,
            runtime_variables=runtime_variables,
        )


def _message_role(message: dict[str, Any] | BaseMessage) -> str:
    if isinstance(message, dict):
        role = str(message.get("role") or message.get("type") or "").lower()
    else:
        role = str(getattr(message, "type", "") or "").lower()

    return {
        "human": "user",
        "ai": "assistant",
        "reasoning": "assistant",
        "function": "tool",
        "developer": "system",
    }.get(role, role)


def _message_text(message: dict[str, Any] | BaseMessage) -> str:
    content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text is not None:
                    parts.append(str(text))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)

    return str(content or "")


_INTERNAL_STATE_MESSAGE_FLAGS = (
    "cuga_internal_control",
    "cuga_internal_tool_execution",
)


def _message_additional_kwargs(
    message: dict[str, Any] | BaseMessage,
) -> dict[str, Any]:
    """Return message metadata used to distinguish CUGA control/rendering events."""
    if isinstance(message, dict):
        value = message.get("additional_kwargs")
    else:
        value = getattr(message, "additional_kwargs", None)
    return value if isinstance(value, dict) else {}


def _is_internal_state_message(
    message: dict[str, Any] | BaseMessage,
) -> bool:
    """Whether a committed chat message is orchestration-only, not evidence.

    Internal CodeAct responses and auto-continue prompts still belong in CUGA's
    conversational execution history, but they must not become semantic STATE
    evidence for later verifier decisions.  The surrounding orchestration code
    tags those messages explicitly rather than relying on brittle text markers.
    """
    additional_kwargs = _message_additional_kwargs(message)
    return any(
        bool(additional_kwargs.get(flag))
        for flag in _INTERNAL_STATE_MESSAGE_FLAGS
    )


def _message_signature(
    message: dict[str, Any] | BaseMessage,
) -> str:
    """Return a stable signature for the raw context representation we consume."""
    payload = {
        "role": _message_role(message),
        "content": _message_text(message),
        # Internal/external status affects whether the message is semantic STATE,
        # so include it in the append-only prefix signature.
        "internal_state_message": _is_internal_state_message(message),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _context_signatures(
    current_context: list[dict[str, Any] | BaseMessage],
) -> list[str]:
    return [
        _message_signature(message)
        for message in current_context
    ]


def _looks_like_tool_result(text: str) -> bool:
    stripped = text.lstrip().lower()
    return (
        stripped.startswith("execution output:")
        or stripped.startswith("tool output:")
        or stripped.startswith("tool result:")
    )


def _json_snapshot(value: Any) -> str:
    """Serialize runtime execution evidence without mutating the original value."""

    def default(item: Any) -> Any:
        if hasattr(item, "model_dump"):
            try:
                return item.model_dump()
            except Exception:
                pass
        if hasattr(item, "dict"):
            try:
                return item.dict()
            except Exception:
                pass
        if isinstance(item, (set, frozenset)):
            return sorted(str(value) for value in item)
        if isinstance(item, bytes):
            return item.decode("utf-8", errors="replace")
        return str(item)

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=default,
        )
    except Exception:
        return json.dumps(str(value), ensure_ascii=False)


def _is_knowledge_base_retrieval_tool(tool_name: str) -> bool:
    normalized = str(tool_name or "").strip()
    return normalized.startswith(_KNOWLEDGE_BASE_RETRIEVAL_TOOL_PREFIXES)


def _extract_shell_command(parameters: Any) -> str | None:
    """Extract the concrete shell command from the runtime parameter snapshot."""
    if isinstance(parameters, str):
        command = parameters.strip()
        return command or None

    if isinstance(parameters, dict):
        direct = parameters.get("command")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()

        positional = parameters.get("_args")
        if isinstance(positional, (list, tuple)) and positional:
            first = positional[0]
            if isinstance(first, str) and first.strip():
                return first.strip()

    if isinstance(parameters, (list, tuple)) and parameters:
        first = parameters[0]
        if isinstance(first, str) and first.strip():
            return first.strip()

    return None


def _shell_output_indicates_failure(output: Any) -> bool:
    """Recognize the Tau shell wrapper's explicit failed-command observation."""
    if not isinstance(output, str):
        return False
    normalized = output.lstrip()
    return bool(
        re.match(r"^Error\s*\(exit code\s+\d+\):", normalized, flags=re.IGNORECASE)
    )


def _is_filesystem_only_shell_command(command: str) -> bool:
    """Return True for a conservative pure filesystem/navigation shell command.

    These commands may help the agent navigate or maintain its KB workspace, but
    their observations do not establish user/domain facts. Only simple commands
    composed entirely of explicitly whitelisted filesystem executables qualify.
    Mixed or opaque shell constructs stay eligible for ordinary execution evidence.
    """
    raw = str(command or "").strip()
    if not raw:
        return False

    # Do not classify nested/opaque shell constructs as disposable evidence.
    if "\n" in raw or "`" in raw or "$(" in raw or "${" in raw:
        return False
    if "(" in raw or ")" in raw or "{" in raw or "}" in raw:
        return False

    try:
        lexer = shlex.shlex(raw, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False

    if not tokens:
        return False

    segments: list[list[str]] = [[]]
    for token in tokens:
        # Redirection can turn an otherwise navigational command into a write/read
        # dependency whose exact effect we should not silently discard.
        if token in _SHELL_FORBIDDEN_CONTROL_TOKENS:
            return False
        if token in _SHELL_COMMAND_SEPARATORS:
            if not segments[-1]:
                return False
            segments.append([])
            continue
        if token and all(ch in "|&;<>" for ch in token):
            return False
        segments[-1].append(token)

    if not segments[-1]:
        return False

    return all(
        Path(segment[0]).name.lower() in _SHELL_FILESYSTEM_ONLY_EXECUTABLES
        for segment in segments
    )


def _should_omit_execution_from_evidence(
    *,
    tool_name: str,
    parameters: Any,
) -> tuple[bool, str | None]:
    """Classify completed executions that should not persist in any graph."""
    normalized = str(tool_name or "").strip()
    if normalized != "shell":
        return False, None

    command = _extract_shell_command(parameters)
    if command is None:
        return False, None
    if not _is_filesystem_only_shell_command(command):
        return False, None
    return True, "filesystem_navigation_or_maintenance"


def _is_read_only_kb_shell_command(command: str) -> bool:
    """Return True only for a conservative read-only KB-inspection shell command.

    The classifier evaluates the complete command/pipeline rather than only its first
    executable. Every command segment must start with a whitelisted read-only text
    inspection utility. Write/background redirection, command substitution, grouping,
    and other shell constructs stay in the ordinary execution graph.
    """
    raw = str(command or "").strip()
    if not raw:
        return False

    # Keep classification intentionally conservative. These constructs can execute
    # arbitrary nested commands or obscure side effects.
    if "\n" in raw or "`" in raw or "$(" in raw or "${" in raw:
        return False
    if "(" in raw or ")" in raw or "{" in raw or "}" in raw:
        return False

    try:
        lexer = shlex.shlex(raw, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False

    if not tokens:
        return False

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _SHELL_FORBIDDEN_CONTROL_TOKENS:
            return False
        if token in _SHELL_COMMAND_SEPARATORS:
            if not segments[-1]:
                return False
            segments.append([])
            continue
        # shlex can coalesce punctuation, so reject any unrecognized punctuation
        # token rather than trying to reason through it.
        if token and all(ch in "|&;<>" for ch in token):
            return False
        segments[-1].append(token)

    if not segments[-1]:
        return False

    for segment in segments:
        executable = Path(segment[0]).name.lower()
        if executable not in _SHELL_KB_READ_ONLY_EXECUTABLES:
            return False

        # ``sort -o/--output`` writes a file even without shell redirection.
        if executable == "sort":
            if "-o" in segment or "--output" in segment:
                return False
            if any(token.startswith("--output=") for token in segment):
                return False

    return True


def _should_route_execution_to_knowledge_base(
    *,
    tool_name: str,
    parameters: Any,
    output: Any,
) -> tuple[bool, str | None]:
    """Classify one completed execution for semantic KB-graph routing."""
    normalized = str(tool_name or "").strip()
    if _is_knowledge_base_retrieval_tool(normalized):
        return True, "dedicated_kb_tool"

    if normalized != "shell":
        return False, None

    command = _extract_shell_command(parameters)
    if command is None:
        return False, None
    if _shell_output_indicates_failure(output):
        return False, None
    if not _is_read_only_kb_shell_command(command):
        return False, None
    return True, "read_only_shell_kb_inspection"


def _knowledge_base_output_text(output: Any) -> str:
    """Preserve retrieved KB text without wrapping it as an execution sentence."""
    if isinstance(output, str):
        return output
    return _json_snapshot(output)


def record_tool_execution(
    *,
    tool_name: str,
    parameters: Any,
    output: Any,
) -> None:
    """Record one completed runtime tool invocation for later graph materialization.

    This hook is intentionally synchronous and cheap because it is called directly
    from the sandbox-local tool wrapper after the real tool has returned or raised.
    No decomposition, spaCy parsing, embedding generation, graph traversal, or LLM
    call happens on the tool's execution path.
    """
    if _VERIFICATION_STATE.graph_session_id is None:
        logger.warning(
            "Prompt verifier execution record ignored because no verifier session "
            "is initialized: tool_name={}",
            tool_name,
        )
        return

    omit_from_evidence, _ = _should_omit_execution_from_evidence(
        tool_name=tool_name,
        parameters=parameters,
    )
    if omit_from_evidence:
        return

    parameters_json = _json_snapshot(parameters)
    route_to_kb, _ = _should_route_execution_to_knowledge_base(
        tool_name=tool_name,
        parameters=parameters,
        output=output,
    )
    if route_to_kb:
        record_index = len(_VERIFICATION_STATE.knowledge_base_records) + 1
        record_id = f"kb-retrieval-{record_index}"
        content = _knowledge_base_output_text(output)
        _VERIFICATION_STATE.knowledge_base_records.append(
            _KnowledgeBaseRetrievalRecord(
                record_id=record_id,
                tool_name=str(tool_name),
                parameters_json=parameters_json,
                content=content,
            )
        )
        return

    output_json = _json_snapshot(output)
    record_index = len(_VERIFICATION_STATE.execution_records) + 1
    record_id = f"execution-{record_index}"
    content = (
        f"Tool {tool_name} was executed with parameters {parameters_json} "
        f"and gave output {output_json}."
    )
    _VERIFICATION_STATE.execution_records.append(
        _ExecutionRecord(
            record_id=record_id,
            tool_name=str(tool_name),
            parameters_json=parameters_json,
            output_json=output_json,
            content=content,
        )
    )


def _materialize_execution_records_sync(
    *,
    records: list[_ExecutionRecord],
    session_id: str,
) -> list[MemoryNode]:
    """Create flat atomic retrieval nodes for completed execution records.

    The execution sentence is already atomic by schema, so this deliberately skips
    GraphBuilder/decomposition/relation linking. We still compute the same local
    S/P/O payload and optional Qwen retrieval embedding used by normal atomic nodes
    so ranking behaves like the other verifier evidence graphs.
    """
    if not records:
        return []

    batch_source_id = f"execution-batch-{records[0].record_id}-{records[-1].record_id}"
    request = GraphBuildRequest(
        session_id=session_id,
        source_id=batch_source_id,
        source_type=SourceType.TOOL_RESULT,
        content="\n".join(record.content for record in records),
        metadata={
            "execution_graph": True,
            "skip_decomposition": True,
        },
    )
    leaves = [
        {
            "temporary_id": record.record_id,
            "content": record.content,
            "semantic_role": "observation",
        }
        for record in records
    ]
    payloads = extract_atomic_payloads_spacy(request, leaves=leaves)

    nodes: list[MemoryNode] = []
    for record in records:
        node_id = f"{session_id}:{record.record_id}"
        source_ref = SourceReference(
            source_id=record.record_id,
            source_type=SourceType.TOOL_RESULT,
            span=SourceSpan(start=0, end=len(record.content)),
            tool_call_id=record.record_id,
        )
        nodes.append(
            MemoryNode(
                id=node_id,
                session_id=session_id,
                source_root_id=node_id,
                kind=NodeKind.ATOMIC_FACT,
                depth=0,
                content=record.content,
                routing_text=record.content,
                proposition=payloads.get(record.record_id),
                source_refs=[source_ref],
                metadata={
                    "execution_graph": True,
                    "atomic_by_construction": True,
                    "tool_name": record.tool_name,
                    "parameters_json": record.parameters_json,
                    "output_json": record.output_json,
                },
            )
        )

    if retrieval_embeddings_enabled():
        retrieval_texts = [build_retrieval_text(node) for node in nodes]
        vectors = embed_retrieval_texts_qwen(retrieval_texts)
        if len(vectors) != len(nodes):
            raise PromptVerificationError(
                "Execution-graph embedding count mismatch: "
                f"expected {len(nodes)}, got {len(vectors)}"
            )
        model_name = qwen_embedding_model_name()
        enriched: list[MemoryNode] = []
        for node, retrieval_text, vector in zip(
            nodes,
            retrieval_texts,
            vectors,
            strict=True,
        ):
            numeric_vector = [float(value) for value in vector]
            if not numeric_vector:
                raise PromptVerificationError(
                    "Execution-graph embedding adapter returned an empty vector"
                )
            embedding = RetrievalEmbedding(
                model=model_name,
                vector=numeric_vector,
                dimensions=len(numeric_vector),
                text_hash=retrieval_text_hash(retrieval_text),
            )
            enriched.append(
                node.model_copy(update={"retrieval_embedding": embedding})
            )
        nodes = enriched

    return nodes


def _materialize_tool_candidate_graph_sync(
    *,
    candidate: str,
    session_id: str,
) -> MemoryGraph:
    """Create one flat atomic retrieval node for a tool-execution candidate.

    Tool-execution candidates are raw generated Python programs. Decomposing that
    code as natural language creates retrieval fragments that can lose call/dataflow
    semantics. Keep the entire untouched generated candidate together as exactly one
    ATOMIC_FACT retrieval query instead. The node still receives the normal local
    S/P/O payload and optional Qwen retrieval embedding so ranking against evidence
    graphs uses the same retrieval machinery as other candidate atoms.
    """
    content = str(candidate or "").strip()
    if not content:
        raise PromptVerificationError(
            "Cannot materialize an empty tool-execution candidate"
        )

    node_id = f"{session_id}:candidate-tool-execution"
    request = GraphBuildRequest(
        session_id=session_id,
        source_id="candidate-tool-execution",
        source_type=SourceType.ASSISTANT_MESSAGE,
        content=content,
        metadata={
            "candidate": True,
            "candidate_kind": "tool_execution",
            "candidate_retrieval_only": True,
            "skip_decomposition": True,
            "atomic_by_construction": True,
        },
    )
    payloads = extract_atomic_payloads_spacy(
        request,
        leaves=[
            {
                "temporary_id": "candidate-tool-execution",
                "content": content,
                "semantic_role": "action",
            }
        ],
    )
    source_ref = SourceReference(
        source_id="candidate-tool-execution",
        source_type=SourceType.ASSISTANT_MESSAGE,
        span=SourceSpan(start=0, end=len(content)),
        turn_id="candidate-tool-execution",
    )
    node = MemoryNode(
        id=node_id,
        session_id=session_id,
        source_root_id=node_id,
        kind=NodeKind.ATOMIC_FACT,
        depth=0,
        content=content,
        routing_text=content,
        proposition=payloads.get("candidate-tool-execution"),
        source_refs=[source_ref],
        metadata={
            "candidate": True,
            "candidate_kind": "tool_execution",
            "candidate_retrieval_only": True,
            "skip_decomposition": True,
            "atomic_by_construction": True,
        },
    )

    if retrieval_embeddings_enabled():
        retrieval_text = build_retrieval_text(node)
        vectors = embed_retrieval_texts_qwen([retrieval_text])
        if len(vectors) != 1:
            raise PromptVerificationError(
                "Tool-candidate embedding count mismatch: "
                f"expected 1, got {len(vectors)}"
            )
        numeric_vector = [float(value) for value in vectors[0]]
        if not numeric_vector:
            raise PromptVerificationError(
                "Tool-candidate embedding adapter returned an empty vector"
            )
        node = node.model_copy(
            update={
                "retrieval_embedding": RetrievalEmbedding(
                    model=qwen_embedding_model_name(),
                    vector=numeric_vector,
                    dimensions=len(numeric_vector),
                    text_hash=retrieval_text_hash(retrieval_text),
                )
            }
        )

    graph = MemoryGraph()
    graph.add_node(node)
    return graph


async def _build_tool_candidate_graph(
    *,
    candidate: str,
    session_id: str,
) -> MemoryGraph:
    """Build the one-node tool candidate graph without semantic decomposition."""
    return await asyncio.to_thread(
        _materialize_tool_candidate_graph_sync,
        candidate=candidate,
        session_id=session_id,
    )


async def _update_knowledge_base_graph(
    *,
    session_id: str,
    semaphore: asyncio.Semaphore,
) -> tuple[MemoryGraph, int]:
    """Materialize newly captured KB retrievals into the persistent semantic graph."""
    if _VERIFICATION_STATE.knowledge_base_graph is None:
        _VERIFICATION_STATE.knowledge_base_graph = MemoryGraph()

    graph = _VERIFICATION_STATE.knowledge_base_graph
    cursor = _VERIFICATION_STATE.knowledge_base_graph_cursor
    pending = _VERIFICATION_STATE.knowledge_base_records[cursor:]
    if not pending:
        return graph, 0

    sources = [
        _EvidenceSource(
            source_id=record.record_id,
            source_type=SourceType.DOCUMENT,
            content=record.content,
            metadata={
                "knowledge_base_graph": True,
                "retrieval_tool_name": record.tool_name,
                "retrieval_parameters_json": record.parameters_json,
                "retrieval_record_id": record.record_id,
            },
        )
        for record in pending
        if record.content.strip()
    ]

    if sources:
        new_node_ids = await _append_sources_to_graph(
            graph,
            sources,
            session_id=session_id,
            semaphore=semaphore,
        )
    else:
        new_node_ids = set()

    # Advance over every captured record, including empty retrieval outputs, so an
    # empty result is not repeatedly reconsidered on every verifier call.
    _VERIFICATION_STATE.knowledge_base_graph_cursor += len(pending)

    return graph, len(new_node_ids)


async def _update_execution_graph(*, session_id: str) -> tuple[MemoryGraph, int]:
    """Materialize newly captured executions into the persistent flat graph."""
    if _VERIFICATION_STATE.execution_graph is None:
        _VERIFICATION_STATE.execution_graph = MemoryGraph()

    graph = _VERIFICATION_STATE.execution_graph
    cursor = _VERIFICATION_STATE.execution_graph_cursor
    pending = _VERIFICATION_STATE.execution_records[cursor:]
    if not pending:
        return graph, 0

    new_nodes = await asyncio.to_thread(
        _materialize_execution_records_sync,
        records=list(pending),
        session_id=session_id,
    )
    for node in new_nodes:
        graph.add_node(node)

    _VERIFICATION_STATE.execution_graph_cursor += len(pending)
    return graph, len(new_nodes)


def _extract_state_sources(
    current_context: list[dict[str, Any] | BaseMessage],
    *,
    start_index: int = 0,
) -> list[_EvidenceSource]:
    """Extract persistent conversational evidence from a raw context slice.

    Completed tool observations are intentionally excluded from STATE because the
    verifier already records each completed invocation in ``execution_graph`` with
    exact tool identity, parameters, and output kept together. Keeping the sandbox
    rendering here as well would duplicate evidence and re-decompose an execution
    fact into semantically weaker fragments.

    ``start_index`` is the position of the first supplied message in the original
    full context. Keeping absolute positions in source IDs makes incremental appends
    stable. System messages and explicitly tagged CUGA orchestration messages are
    also ignored. The raw cursor still advances over every message through the
    separate signature bookkeeping.
    """
    state_sources: list[_EvidenceSource] = []

    for index, message in enumerate(current_context, start=start_index):
        role = _message_role(message)
        text = _message_text(message).strip()
        if not text or role == "system":
            continue

        if _is_internal_state_message(message):
            continue

        # Tool-role messages and CUGA sandbox execution-output messages are
        # represented exclusively by execution_graph or knowledge_base_graph. Do
        # not decompose/copy them into STATE.
        if role == "tool" or (role == "user" and _looks_like_tool_result(text)):
            continue

        source_prefix = f"context-{index}"
        base_metadata = {
            "message_role": role,
            "context_index": index,
        }

        if role == "assistant":
            state_sources.append(
                _EvidenceSource(
                    source_id=f"{source_prefix}-assistant",
                    source_type=SourceType.ASSISTANT_MESSAGE,
                    content=text,
                    metadata=base_metadata,
                )
            )
            continue

        if role == "user":
            state_sources.append(
                _EvidenceSource(
                    source_id=f"{source_prefix}-user",
                    source_type=SourceType.USER_MESSAGE,
                    content=text,
                    metadata=base_metadata,
                )
            )
            continue

        # Unknown non-system roles are retained only as weak assistant history
        # rather than being accidentally promoted to authoritative user input.
        state_sources.append(
            _EvidenceSource(
                source_id=f"{source_prefix}-{role or 'unknown'}",
                source_type=SourceType.ASSISTANT_MESSAGE,
                content=text,
                metadata=base_metadata,
            )
        )

    return state_sources

def _semantic_subgraph_cache_metadata(source: _EvidenceSource) -> dict[str, Any]:
    """Return only build-relevant metadata for a semantic-fragment cache key.

    Source occurrence/provenance fields are rebound after a cache hit, so they do
    not belong in the decomposition identity. Unknown/future metadata is retained
    by default, which makes the cache conservative when GraphBuilder gains a new
    construction-time flag.
    """
    return {
        key: value
        for key, value in source.metadata.items()
        if key not in _SEMANTIC_SUBGRAPH_OCCURRENCE_METADATA_KEYS
    }


def _semantic_subgraph_cache_prompt(source: _EvidenceSource) -> str:
    """Return a deterministic identity for one context-independent source build."""
    return json.dumps(
        {
            "cache_schema": _SEMANTIC_SUBGRAPH_CACHE_SCHEMA,
            "source_type": source.source_type.value,
            # Preserve exact source text because source spans are root-relative.
            "content": source.content,
            "build_metadata": _semantic_subgraph_cache_metadata(source),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _semantic_subgraph_cache_enabled(source: _EvidenceSource) -> bool:
    """Whether this source should use the generic semantic-fragment cache.

    CUGA policy and Playbook graphs already have whole-graph caches with their own
    stable identities, so avoid creating a redundant second cache entry for those
    authority sources. Every other GraphBuilder-backed source is eligible.
    """
    return not bool(source.metadata.get("authority_group"))


def _semantic_subgraph_rebound_id(
    *,
    namespace: str,
    session_id: str,
    source_id: str,
    cache_hash: str,
    old_id: str,
) -> str:
    """Create a stable current-occurrence ID for one cached graph object."""
    digest = hashlib.sha256(
        "\x1f".join(
            [namespace, session_id, source_id, cache_hash, old_id]
        ).encode("utf-8")
    ).hexdigest()
    return f"cached-{namespace}-{digest}"


def _rebind_cached_source_ref(
    source_ref: SourceReference,
    *,
    source: _EvidenceSource,
) -> SourceReference:
    """Rebind cached semantic provenance to the current source occurrence."""
    return source_ref.model_copy(
        update={
            "source_id": source.source_id,
            "source_type": source.source_type,
            "turn_id": source.source_id,
        }
    )


def _rebind_logic_expression_slots(
    expression: Any,
    *,
    slot_id_map: dict[str, str],
) -> Any:
    """Recursively remap persistent logic-slot IDs in one cached expression."""
    slot_id = getattr(expression, "slot_id", None)
    operands = list(getattr(expression, "operands", []) or [])
    update: dict[str, Any] = {}
    if slot_id is not None:
        update["slot_id"] = slot_id_map.get(slot_id, slot_id)
    if operands:
        update["operands"] = [
            _rebind_logic_expression_slots(
                operand,
                slot_id_map=slot_id_map,
            )
            for operand in operands
        ]
    if not update:
        return expression
    return expression.model_copy(update=update)


def _rebind_cached_semantic_subgraph(
    cached_graph: MemoryGraph,
    *,
    source: _EvidenceSource,
    session_id: str,
    cache_hash: str,
) -> MemoryGraph:
    """Clone one cached fragment into the current source/session namespace.

    Cached fragments are deliberately stored before cross-source lateral linking.
    Their object IDs and source references still belong to whichever runtime first
    populated the cache. Re-keying every semantic/edge/logic object prevents two
    equal-text occurrences from collapsing when both are merged into the same
    session graph, while preserving the expensive decomposition, S/P/O payloads,
    embeddings, hierarchy, local relations, and logic structure.
    """
    node_id_map = {
        old_id: _semantic_subgraph_rebound_id(
            namespace="node",
            session_id=session_id,
            source_id=source.source_id,
            cache_hash=cache_hash,
            old_id=old_id,
        )
        for old_id in cached_graph.nodes
    }
    edge_id_map = {
        old_id: _semantic_subgraph_rebound_id(
            namespace="edge",
            session_id=session_id,
            source_id=source.source_id,
            cache_hash=cache_hash,
            old_id=old_id,
        )
        for old_id in cached_graph.edges
    }

    logic_layer = cached_graph.logic_layer
    slot_id_map = {
        slot.id: _semantic_subgraph_rebound_id(
            namespace="logic-slot",
            session_id=session_id,
            source_id=source.source_id,
            cache_hash=cache_hash,
            old_id=slot.id,
        )
        for slot in logic_layer.slots
    }

    rebound = MemoryGraph()
    rebound_timestamp = datetime_module.datetime.now(datetime_module.timezone.utc)

    for node in cached_graph.nodes.values():
        source_root_id = node_id_map.get(node.source_root_id, node.source_root_id)
        rebound.add_node(
            node.model_copy(
                update={
                    "id": node_id_map[node.id],
                    "session_id": session_id,
                    "source_root_id": source_root_id,
                    "source_refs": [
                        _rebind_cached_source_ref(ref, source=source)
                        for ref in node.source_refs
                    ],
                    "support_node_ids": [
                        node_id_map.get(node_id, node_id)
                        for node_id in node.support_node_ids
                    ],
                    "created_at": rebound_timestamp,
                    "updated_at": rebound_timestamp,
                    # Only RAW_SOURCE owns request-level occurrence metadata.
                    # Remove occurrence fields from the cached fragment
                    # before overlaying the current source provenance. Child-node
                    # metadata remains decomposition-specific.
                    "metadata": (
                        {
                            **{
                                key: value
                                for key, value in node.metadata.items()
                                if key not in _SEMANTIC_SUBGRAPH_OCCURRENCE_METADATA_KEYS
                            },
                            **source.metadata,
                            "semantic_subgraph_cache_rebound": True,
                        }
                        if node.kind == NodeKind.RAW_SOURCE
                        else {
                            **node.metadata,
                            "semantic_subgraph_cache_rebound": True,
                        }
                    ),
                }
            )
        )

    for edge in cached_graph.edges.values():
        rebound.add_edge(
            edge.model_copy(
                update={
                    "id": edge_id_map[edge.id],
                    "source_id": node_id_map.get(edge.source_id, edge.source_id),
                    "target_id": node_id_map.get(edge.target_id, edge.target_id),
                    "evidence_node_ids": [
                        node_id_map.get(node_id, node_id)
                        for node_id in edge.evidence_node_ids
                    ],
                    "created_at": rebound_timestamp,
                }
            )
        )

    rebound_slots = []
    for slot in logic_layer.slots:
        rebound_slots.append(
            slot.model_copy(
                update={
                    "id": slot_id_map[slot.id],
                    "bindings": [
                        binding.model_copy(
                            update={
                                "node_id": node_id_map.get(
                                    binding.node_id,
                                    binding.node_id,
                                )
                            }
                        )
                        for binding in slot.bindings
                    ],
                    "source_refs": [
                        _rebind_cached_source_ref(ref, source=source)
                        for ref in slot.source_refs
                    ],
                    "metadata": {
                        **slot.metadata,
                        **(
                            {
                                "logic_parent_node_id": node_id_map.get(
                                    str(slot.metadata.get("logic_parent_node_id")),
                                    str(slot.metadata.get("logic_parent_node_id")),
                                )
                            }
                            if slot.metadata.get("logic_parent_node_id") is not None
                            else {}
                        ),
                    },
                }
            )
        )

    rebound_literal_assertions = []
    for assertion in logic_layer.literal_assertions:
        rebound_literal_assertions.append(
            assertion.model_copy(
                update={
                    "id": _semantic_subgraph_rebound_id(
                        namespace="logic-literal-assertion",
                        session_id=session_id,
                        source_id=source.source_id,
                        cache_hash=cache_hash,
                        old_id=assertion.id,
                    ),
                    "literal": assertion.literal.model_copy(
                        update={
                            "slot_id": slot_id_map.get(
                                assertion.literal.slot_id,
                                assertion.literal.slot_id,
                            )
                        }
                    ),
                    "parent_node_id": node_id_map.get(
                        assertion.parent_node_id,
                        assertion.parent_node_id,
                    ),
                    "source_refs": [
                        _rebind_cached_source_ref(ref, source=source)
                        for ref in assertion.source_refs
                    ],
                }
            )
        )

    rebound_relations = []
    for relation in logic_layer.relations:
        rebound_relations.append(
            relation.model_copy(
                update={
                    "id": _semantic_subgraph_rebound_id(
                        namespace="logic-relation",
                        session_id=session_id,
                        source_id=source.source_id,
                        cache_hash=cache_hash,
                        old_id=relation.id,
                    ),
                    "antecedent": relation.antecedent.model_copy(
                        update={
                            "slot_id": slot_id_map.get(
                                relation.antecedent.slot_id,
                                relation.antecedent.slot_id,
                            )
                        }
                    ),
                    "consequent": relation.consequent.model_copy(
                        update={
                            "slot_id": slot_id_map.get(
                                relation.consequent.slot_id,
                                relation.consequent.slot_id,
                            )
                        }
                    ),
                    "parent_node_id": node_id_map.get(
                        relation.parent_node_id,
                        relation.parent_node_id,
                    ),
                    "source_refs": [
                        _rebind_cached_source_ref(ref, source=source)
                        for ref in relation.source_refs
                    ],
                }
            )
        )

    rebound_compound_assertions = []
    for assertion in logic_layer.compound_assertions:
        rebound_compound_assertions.append(
            assertion.model_copy(
                update={
                    "id": _semantic_subgraph_rebound_id(
                        namespace="logic-compound-assertion",
                        session_id=session_id,
                        source_id=source.source_id,
                        cache_hash=cache_hash,
                        old_id=assertion.id,
                    ),
                    "root": _rebind_logic_expression_slots(
                        assertion.root,
                        slot_id_map=slot_id_map,
                    ),
                    "parent_node_id": node_id_map.get(
                        assertion.parent_node_id,
                        assertion.parent_node_id,
                    ),
                    "source_refs": [
                        _rebind_cached_source_ref(ref, source=source)
                        for ref in assertion.source_refs
                    ],
                }
            )
        )

    rebound_compound_rules = []
    for rule in logic_layer.compound_rules:
        rebound_compound_rules.append(
            rule.model_copy(
                update={
                    "id": _semantic_subgraph_rebound_id(
                        namespace="logic-compound-rule",
                        session_id=session_id,
                        source_id=source.source_id,
                        cache_hash=cache_hash,
                        old_id=rule.id,
                    ),
                    "condition": _rebind_logic_expression_slots(
                        rule.condition,
                        slot_id_map=slot_id_map,
                    ),
                    "effect": _rebind_logic_expression_slots(
                        rule.effect,
                        slot_id_map=slot_id_map,
                    ),
                    "parent_node_id": node_id_map.get(
                        rule.parent_node_id,
                        rule.parent_node_id,
                    ),
                    "source_refs": [
                        _rebind_cached_source_ref(ref, source=source)
                        for ref in rule.source_refs
                    ],
                }
            )
        )

    rebound.logic_layer = logic_layer.model_copy(
        update={
            "slots": rebound_slots,
            "literal_assertions": rebound_literal_assertions,
            "relations": rebound_relations,
            "compound_assertions": rebound_compound_assertions,
            "compound_rules": rebound_compound_rules,
        }
    )
    return rebound


def _build_source_graph_sync(
    *,
    source: _EvidenceSource,
    session_id: str,
) -> MemoryGraph:
    """Run GraphBuilder once and materialize its result as an isolated fragment."""
    request = GraphBuildRequest(
        session_id=session_id,
        source_id=source.source_id,
        source_type=source.source_type,
        content=source.content,
        metadata=source.metadata,
    )
    build_result = GraphBuilder().build(request)
    fragment = MemoryGraph()
    fragment.apply_build_result(build_result)
    return fragment


async def _load_or_build_source_graph(
    source: _EvidenceSource,
    *,
    session_id: str,
    semaphore: asyncio.Semaphore,
) -> MemoryGraph:
    """Load one semantic source fragment from cache or build/cache it on a miss."""
    if not _semantic_subgraph_cache_enabled(source):
        async with semaphore:
            return await asyncio.to_thread(
                _build_source_graph_sync,
                source=source,
                session_id=session_id,
            )

    cache_prompt = _semantic_subgraph_cache_prompt(source)
    prompt_hash = compute_prompt_hash(cache_prompt)
    loop = asyncio.get_running_loop()
    lock_entry = _SEMANTIC_SUBGRAPH_CACHE_LOCKS.get(prompt_hash)
    if lock_entry is None or lock_entry[0] is not loop:
        lock = asyncio.Lock()
        _SEMANTIC_SUBGRAPH_CACHE_LOCKS[prompt_hash] = (loop, lock)
    else:
        lock = lock_entry[1]

    async with lock:
        load_start = time.perf_counter()
        cached_graph = load_graph_for_prompt(
            cache_prompt,
            graph_type=_SEMANTIC_SUBGRAPH_CACHE_GRAPH_TYPE,
            memory_graphs_file=_GRAPH_CACHE_INDEX,
        )
        if cached_graph is not None:
            rebound = _rebind_cached_semantic_subgraph(
                cached_graph,
                source=source,
                session_id=session_id,
                cache_hash=prompt_hash,
            )
            logger.debug(
                "Prompt verifier semantic subgraph cache HIT: hash={} "
                "source_type={} source_id={} nodes={} edges={} load_time={:.3f}s",
                prompt_hash,
                source.source_type.value,
                source.source_id,
                len(rebound.nodes),
                len(rebound.edges),
                time.perf_counter() - load_start,
            )
            return rebound

        logger.debug(
            "Prompt verifier semantic subgraph cache MISS: hash={} "
            "source_type={} source_id={}; building fragment",
            prompt_hash,
            source.source_type.value,
            source.source_id,
        )

        async with semaphore:
            fragment = await asyncio.to_thread(
                _build_source_graph_sync,
                source=source,
                session_id=session_id,
            )

        graph_file = _GRAPH_CACHE_DIR / f"semantic_subgraph_{prompt_hash}.json"
        save_graph(
            fragment,
            graph_file,
            raw_prompt=cache_prompt,
            graph_type=_SEMANTIC_SUBGRAPH_CACHE_GRAPH_TYPE,
            memory_graphs_file=_GRAPH_CACHE_INDEX,
        )
        logger.debug(
            "Prompt verifier semantic subgraph cached: hash={} file={} "
            "source_type={} source_id={} nodes={} edges={}",
            prompt_hash,
            graph_file,
            source.source_type.value,
            source.source_id,
            len(fragment.nodes),
            len(fragment.edges),
        )

        # Return the same current-occurrence representation used on future hits.
        # This keeps IDs stable between a first build and a later cached rebuild.
        return _rebind_cached_semantic_subgraph(
            fragment,
            source=source,
            session_id=session_id,
            cache_hash=prompt_hash,
        )


async def _build_source_graphs(
    sources: list[_EvidenceSource],
    *,
    session_id: str,
    semaphore: asyncio.Semaphore,
) -> list[MemoryGraph]:
    """Load/build isolated semantic fragments for all supplied evidence sources."""
    if not sources:
        return []
    return list(
        await asyncio.gather(
            *(
                _load_or_build_source_graph(
                    source,
                    session_id=session_id,
                    semaphore=semaphore,
                )
                for source in sources
            )
        )
    )


async def _apply_source_graphs(
    graph: MemoryGraph,
    source_graphs: list[MemoryGraph],
    *,
    link_relations: bool = True,
) -> set[str]:
    """Merge source fragments, then infer only context-dependent lateral links."""
    new_node_ids: set[str] = set()
    for source_graph in source_graphs:
        new_node_ids.update(graph.merge_graph(source_graph))

    if new_node_ids and link_relations:
        # Cached fragments intentionally stop before this stage. Relation linking
        # may compare new atoms with atoms already present in the destination graph,
        # so it must be recomputed for the current session/context on every merge.
        await asyncio.to_thread(
            link_new_nodes,
            graph,
            new_node_ids,
        )
    return new_node_ids


async def _build_graph(
    sources: list[_EvidenceSource],
    *,
    session_id: str,
    semaphore: asyncio.Semaphore,
    link_relations: bool = True,
) -> MemoryGraph:
    """Build a new memory graph, reusing cached per-source semantic fragments."""
    graph = MemoryGraph()
    source_graphs = await _build_source_graphs(
        sources,
        session_id=session_id,
        semaphore=semaphore,
    )
    await _apply_source_graphs(
        graph,
        source_graphs,
        link_relations=link_relations,
    )
    return graph


async def _append_sources_to_graph(
    graph: MemoryGraph,
    sources: list[_EvidenceSource],
    *,
    session_id: str,
    semaphore: asyncio.Semaphore,
) -> set[str]:
    """Append new evidence sources, reusing cached semantic fragments when possible."""
    source_graphs = await _build_source_graphs(
        sources,
        session_id=session_id,
        semaphore=semaphore,
    )
    return await _apply_source_graphs(
        graph,
        source_graphs,
    )


def _state_prefix_matches(
    current_signatures: list[str],
) -> bool:
    """Whether the raw context prefix already represented by state is unchanged."""
    cursor = _VERIFICATION_STATE.state_context_cursor
    stored = _VERIFICATION_STATE.state_context_signatures

    if cursor < 0:
        return False
    if cursor > len(current_signatures):
        return False
    if len(stored) != cursor:
        return False

    return stored == current_signatures[:cursor]


async def _update_state_graph(
    current_context: list[dict[str, Any] | BaseMessage],
    *,
    session_id: str,
    semaphore: asyncio.Semaphore,
) -> tuple[MemoryGraph, str, set[str]]:
    """Bring the persistent state graph up to date with ``current_context``.

    Returns:
        ``(graph, update_mode, new_node_ids)`` where update_mode is one
        of ``initial_build``, ``append``, ``reuse``, ``cursor_advance``, or
        ``fallback_rebuild``.
    """
    current_signatures = _context_signatures(current_context)

    if _VERIFICATION_STATE.state_graph is None:
        sources = _extract_state_sources(
            current_context,
            start_index=0,
        )
        graph = await _build_graph(
            sources,
            session_id=session_id,
            semaphore=semaphore,
        )

        new_node_ids = set(graph.nodes)
        committed = _VERIFICATION_STATE.committed_reasoning_graph
        if committed is not None:
            graph.merge_graph(committed)

        _VERIFICATION_STATE.state_graph = graph
        _VERIFICATION_STATE.state_context_cursor = len(current_context)
        _VERIFICATION_STATE.state_context_signatures = current_signatures

        return graph, "initial_build", new_node_ids

    if not _state_prefix_matches(current_signatures):
        logger.warning(
            "Prompt verifier state context is not append-only; rebuilding state "
            "graph from current context (old_cursor={} current_messages={})",
            _VERIFICATION_STATE.state_context_cursor,
            len(current_context),
        )

        sources = _extract_state_sources(
            current_context,
            start_index=0,
        )
        graph = await _build_graph(
            sources,
            session_id=session_id,
            semaphore=semaphore,
        )

        new_node_ids = set(graph.nodes)
        committed = _VERIFICATION_STATE.committed_reasoning_graph
        if committed is not None:
            graph.merge_graph(committed)

        _VERIFICATION_STATE.state_graph = graph
        _VERIFICATION_STATE.state_context_cursor = len(current_context)
        _VERIFICATION_STATE.state_context_signatures = current_signatures

        return graph, "fallback_rebuild", new_node_ids

    graph = _VERIFICATION_STATE.state_graph
    cursor = _VERIFICATION_STATE.state_context_cursor

    if cursor == len(current_context):
        return graph, "reuse", set()

    new_messages = current_context[cursor:]
    new_sources = _extract_state_sources(
        new_messages,
        start_index=cursor,
    )

    # Even if the raw delta contains only ignored system/empty messages, advance
    # the raw cursor after confirming the prefix was unchanged.
    if new_sources:
        new_node_ids = await _append_sources_to_graph(
            graph,
            new_sources,
            session_id=session_id,
            semaphore=semaphore,
        )
        update_mode = "append"
    else:
        new_node_ids = set()
        update_mode = "cursor_advance"

    _VERIFICATION_STATE.state_context_cursor = len(current_context)
    _VERIFICATION_STATE.state_context_signatures = current_signatures

    return graph, update_mode, new_node_ids


def _playbook_graph_cache_prompt(
    playbooks: list[AuthoritySource],
) -> str:
    """Return a deterministic cache identity from schema + Playbook text.

    Runtime source IDs and metadata are intentionally excluded because they may
    change between otherwise identical Tau attempts/runs. The construction-cache
    schema is included because verifier reconstruction now depends on semantic-
    context closure metadata produced by GraphBuilder.
    """
    contents = [
        playbook.content.strip()
        for playbook in playbooks
        if playbook.content.strip()
    ]

    return json.dumps(
        {
            "cache_schema": _AUTHORITY_GRAPH_CACHE_SCHEMA,
            "contents": contents,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _cuga_policy_graph_cache_prompt(content: str) -> str:
    """Return the versioned authority-cache identity for CUGA policy text."""
    return json.dumps(
        {
            "cache_schema": _AUTHORITY_GRAPH_CACHE_SCHEMA,
            "content": content.strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


async def create_cuga_policy_graph(
    cuga_policy: str,
    *,
    session_id: str,
    semaphore: asyncio.Semaphore | None = None,
) -> MemoryGraph:
    """Load or build the authority graph for CUGA's base policy/instructions."""
    content = cuga_policy.strip()
    if not content:
        return MemoryGraph()

    cache_prompt = _cuga_policy_graph_cache_prompt(content)
    prompt_hash = compute_prompt_hash(cache_prompt)
    load_start = time.perf_counter()
    cached_graph = load_graph_for_prompt(
        cache_prompt,
        graph_type="cuga_policy",
        memory_graphs_file=_GRAPH_CACHE_INDEX,
    )

    if cached_graph is not None:
        logger.debug(
            "Prompt verifier CUGA-policy graph cache HIT: hash={} "
            "nodes={} edges={} load_time={:.3f}s",
            prompt_hash,
            len(cached_graph.nodes),
            len(cached_graph.edges),
            time.perf_counter() - load_start,
        )
        return cached_graph

    logger.debug(
        "Prompt verifier CUGA-policy graph cache MISS: hash={}; building graph",
        prompt_hash,
    )

    build_semaphore = semaphore or asyncio.Semaphore(_GRAPH_BUILD_CONCURRENCY)
    source = _EvidenceSource(
        source_id="cuga-policy",
        source_type=SourceType.POLICY,
        content=content,
        metadata={"authority_group": "cuga_policy"},
    )
    graph = await _build_graph(
        [source],
        session_id=session_id,
        semaphore=build_semaphore,
    )

    graph_file = _GRAPH_CACHE_DIR / f"cuga_policy_{prompt_hash}.json"
    save_graph(
        graph,
        graph_file,
        raw_prompt=cache_prompt,
        graph_type="cuga_policy",
        memory_graphs_file=_GRAPH_CACHE_INDEX,
    )

    logger.debug(
        "Prompt verifier CUGA-policy graph cached: hash={} file={} "
        "nodes={} edges={}",
        prompt_hash,
        graph_file,
        len(graph.nodes),
        len(graph.edges),
    )
    return graph


async def create_playbook_graph(
    playbooks: list[AuthoritySource],
    *,
    session_id: str,
    semaphore: asyncio.Semaphore | None = None,
) -> MemoryGraph:
    """Load or build one authority graph from configured CUGA Playbooks."""
    active_playbooks = [
        playbook
        for playbook in playbooks
        if playbook.content.strip()
    ]
    if not active_playbooks:
        return MemoryGraph()

    cache_prompt = _playbook_graph_cache_prompt(active_playbooks)
    prompt_hash = compute_prompt_hash(cache_prompt)
    load_start = time.perf_counter()
    cached_graph = load_graph_for_prompt(
        cache_prompt,
        graph_type="playbook",
        memory_graphs_file=_GRAPH_CACHE_INDEX,
    )

    if cached_graph is not None:
        logger.debug(
            "Prompt verifier Playbook graph cache HIT: hash={} playbooks={} "
            "nodes={} edges={} load_time={:.3f}s",
            prompt_hash,
            len(active_playbooks),
            len(cached_graph.nodes),
            len(cached_graph.edges),
            time.perf_counter() - load_start,
        )
        return cached_graph

    logger.debug(
        "Prompt verifier Playbook graph cache MISS: hash={} playbooks={}; "
        "building graph",
        prompt_hash,
        len(active_playbooks),
    )

    sources = [
        _EvidenceSource(
            source_id=playbook.source_id,
            source_type=SourceType.POLICY,
            content=playbook.content.strip(),
            metadata={
                **playbook.metadata,
                "authority_group": "playbook",
            },
        )
        for playbook in active_playbooks
    ]

    build_semaphore = semaphore or asyncio.Semaphore(_GRAPH_BUILD_CONCURRENCY)
    graph = await _build_graph(
        sources,
        session_id=session_id,
        semaphore=build_semaphore,
    )

    graph_file = _GRAPH_CACHE_DIR / f"playbook_{prompt_hash}.json"
    save_graph(
        graph,
        graph_file,
        raw_prompt=cache_prompt,
        graph_type="playbook",
        memory_graphs_file=_GRAPH_CACHE_INDEX,
    )

    logger.debug(
        "Prompt verifier Playbook graph cached: hash={} file={} playbooks={} "
        "nodes={} edges={}",
        prompt_hash,
        graph_file,
        len(active_playbooks),
        len(graph.nodes),
        len(graph.edges),
    )
    return graph


def _prepare_verification_session(session_id: str) -> str:
    """Prepare verifier-owned state for ``session_id`` without clobbering same-session graphs.

    The verifier currently supports one active CUGA session at a time. The first
    authority hook for a new session resets all graph state. Subsequent authority
    hooks for that same session reuse the existing graph namespace and preserve
    whichever authority graph was already initialized.
    """
    if _VERIFICATION_STATE.owner_session_id == session_id:
        if _VERIFICATION_STATE.graph_session_id is None:
            _VERIFICATION_STATE.graph_session_id = (
                f"prompt-verification-{session_id}"
            )
        return _VERIFICATION_STATE.graph_session_id

    if _VERIFICATION_STATE.owner_session_id is not None:
        logger.debug(
            "Prompt verifier starting new session: old_session={} new_session={}",
            _VERIFICATION_STATE.owner_session_id,
            session_id,
        )

    graph_session_id = f"prompt-verification-{session_id}"

    _VERIFICATION_STATE.owner_session_id = session_id
    _VERIFICATION_STATE.graph_session_id = graph_session_id

    _VERIFICATION_STATE.cuga_policy_initialized = False
    _VERIFICATION_STATE.playbook_initialized = False
    _VERIFICATION_STATE.cuga_policy_graph = None
    _VERIFICATION_STATE.playbook_graph = None
    _VERIFICATION_STATE.runtime_initialized = False
    _VERIFICATION_STATE.runtime_facts = {}
    _VERIFICATION_STATE.runtime_variables_manager = None

    _VERIFICATION_STATE.state_graph = None
    _VERIFICATION_STATE.state_context_cursor = 0
    _VERIFICATION_STATE.state_context_signatures = []
    _VERIFICATION_STATE.execution_graph = None
    _VERIFICATION_STATE.execution_records = []
    _VERIFICATION_STATE.execution_graph_cursor = 0
    _VERIFICATION_STATE.knowledge_base_graph = None
    _VERIFICATION_STATE.knowledge_base_records = []
    _VERIFICATION_STATE.knowledge_base_graph_cursor = 0
    _VERIFICATION_STATE.reasoning_graph = None
    _VERIFICATION_STATE.reasoning_steps = []
    _VERIFICATION_STATE.committed_reasoning_graph = None

    return graph_session_id


async def initialize_playbook_graph(
    *,
    session_id: str,
    playbooks: list[AuthoritySource],
) -> None:
    """Initialize the initially configured Playbook authority graph.

    This hook is intended for ``CugaAgent``. It snapshots enabled Playbooks before
    the incoming user message is processed. It does not construct or modify the
    CUGA-policy graph.
    """
    graph_session_id = _prepare_verification_session(session_id)

    if _VERIFICATION_STATE.playbook_initialized:
        logger.debug(
            "Prompt verifier Playbook graph already initialized for session {}",
            session_id,
        )
        return

    start = time.perf_counter()
    semaphore = asyncio.Semaphore(_GRAPH_BUILD_CONCURRENCY)

    playbook_graph = await create_playbook_graph(
        playbooks,
        session_id=graph_session_id,
        semaphore=semaphore,
    )

    _VERIFICATION_STATE.playbook_graph = playbook_graph
    _VERIFICATION_STATE.playbook_initialized = True

    logger.debug(
        "Prompt verifier Playbook graph initialized: session={} playbooks={} "
        "playbook_nodes={} wall_time={:.3f}s",
        session_id,
        len(playbooks),
        len(playbook_graph.nodes),
        time.perf_counter() - start,
    )


async def initialize_cuga_policy_graph(
    *,
    session_id: str,
    cuga_policy: str,
) -> None:
    """Initialize CugaLite's effective behavioral authority graph.

    This hook is intentionally separate from Playbook initialization. It should
    be called from the CugaLite prompt-preparation path once the effective CUGA
    behavioral policy for the current execution mode has been resolved.
    """
    graph_session_id = _prepare_verification_session(session_id)

    if _VERIFICATION_STATE.cuga_policy_initialized:
        logger.debug(
            "Prompt verifier CUGA-policy graph already initialized for session {}",
            session_id,
        )
        return

    start = time.perf_counter()
    semaphore = asyncio.Semaphore(_GRAPH_BUILD_CONCURRENCY)

    cuga_policy_graph = await create_cuga_policy_graph(
        cuga_policy,
        session_id=graph_session_id,
        semaphore=semaphore,
    )

    _VERIFICATION_STATE.cuga_policy_graph = cuga_policy_graph
    _VERIFICATION_STATE.cuga_policy_initialized = True

    logger.debug(
        "Prompt verifier CUGA-policy graph initialized: session={} "
        "cuga_policy_nodes={} wall_time={:.3f}s",
        session_id,
        len(cuga_policy_graph.nodes),
        time.perf_counter() - start,
    )


def _tool_name(tool: Any) -> str | None:
    """Recover a stable tool name from a prepared LangChain/CUGA tool object."""
    if isinstance(tool, str):
        name = tool
    elif isinstance(tool, dict):
        name = tool.get("name")
    else:
        name = getattr(tool, "name", None)

    if name is None:
        return None

    normalized = str(name).strip()
    return normalized or None


def _tool_description(tool: Any) -> str | None:
    if isinstance(tool, dict):
        description = tool.get("description")
    else:
        description = getattr(tool, "description", None)

    if description is None:
        return None

    normalized = str(description).strip()
    return normalized or None


def _tool_input_schema(tool: Any) -> dict[str, Any] | None:
    """Best-effort deterministic schema extraction for prompt-visible tools."""
    if isinstance(tool, dict):
        for key in ("args_schema", "input_schema", "parameters"):
            value = tool.get(key)
            if isinstance(value, dict):
                return value
        return None

    args_schema = getattr(tool, "args_schema", None)
    if args_schema is None:
        return None

    if isinstance(args_schema, dict):
        return args_schema

    model_json_schema = getattr(args_schema, "model_json_schema", None)
    if callable(model_json_schema):
        try:
            schema = model_json_schema()
        except Exception:
            return None
        return schema if isinstance(schema, dict) else None

    schema_method = getattr(args_schema, "schema", None)
    if callable(schema_method):
        try:
            schema = schema_method()
        except Exception:
            return None
        return schema if isinstance(schema, dict) else None

    return None


def _normalize_prompt_tools(
    prompt_tools: list[Any],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    for tool in prompt_tools:
        name = _tool_name(tool)
        if name is None or name in seen:
            continue
        seen.add(name)

        normalized.append(
            {
                "name": name,
                "description": _tool_description(tool),
                "input_schema": _tool_input_schema(tool),
            }
        )

    normalized.sort(key=lambda item: item["name"])
    return normalized


def initialize_verification_runtime(
    *,
    session_id: str,
    prompt_tools: list[Any],
    execution_tool_names: list[str],
    find_tools_enabled: bool,
    variables_manager: Any | None = None,
) -> None:
    """Snapshot deterministic runtime tooling facts for the verifier.

    This is intentionally a verifier-owned normalization seam. CugaLite passes
    the already-prepared runtime objects/names; it does not interpret them or
    construct verifier-specific policy facts.

    ``prompt_tools`` describes tools explicitly exposed to the model.
    ``execution_tool_names`` describes callable names actually present in the
    execution context. These sets may differ when find_tools shortlisting is
    active. ``variables_manager`` is CUGA's mutable persistent VariablesManager;
    it is retained only as a deterministic Python-name resolver for future
    tool-execution candidates and is never ingested into conversational STATE.
    """
    _prepare_verification_session(session_id)

    normalized_execution_names = sorted(
        {
            str(name).strip()
            for name in execution_tool_names
            if str(name).strip()
        }
    )

    normalized_prompt_tools = _normalize_prompt_tools(
        list(prompt_tools or [])
    )
    prompt_tool_names = [
        item["name"]
        for item in normalized_prompt_tools
    ]

    _VERIFICATION_STATE.runtime_variables_manager = variables_manager

    _VERIFICATION_STATE.runtime_facts = {
        "prompt_visible_tools": normalized_prompt_tools,
        "prompt_visible_tool_names": prompt_tool_names,
        "execution_context_tool_names": normalized_execution_names,
        "find_tools_enabled": bool(find_tools_enabled),
        "semantics": {
            "prompt_visible_tools": (
                "Tools explicitly exposed to the generation model in the "
                "prepared prompt/tool binding."
            ),
            "execution_context_tool_names": (
                "Callable tool names actually available in CUGA's execution "
                "context for this prepared graph."
            ),
            "find_tools_enabled": (
                "Whether CUGA is using find_tools shortlisting/discovery for "
                "the prepared prompt."
            ),
        },
    }
    _VERIFICATION_STATE.runtime_initialized = True

    logger.debug(
        "Prompt verifier runtime initialized: session={} prompt_tools={} "
        "execution_tools={} find_tools_enabled={} variables_manager_registered={} "
        "bulk_verification_enabled={}",
        session_id,
        prompt_tool_names,
        normalized_execution_names,
        bool(find_tools_enabled),
        variables_manager is not None,
        PROMPT_VERIFIER_BULK_VERIFICATION_ENABLED,
    )


def _snapshot_runtime_variables() -> dict[str, Any]:
    """Read the current CUGA variable namespace without turning it into evidence.

    The snapshot is taken immediately before candidate parsing so variables created
    by earlier sandbox executions are available. Values are used only to determine
    what Python expressions in the proposed tool call evaluate to.
    """
    manager = _VERIFICATION_STATE.runtime_variables_manager
    if manager is None:
        return {}

    try:
        get_names = getattr(manager, "get_variable_names", None)
        if callable(get_names):
            names = list(get_names() or [])
        else:
            raw_variables = getattr(manager, "variables", {})
            names = list(raw_variables.keys()) if isinstance(raw_variables, dict) else []
    except Exception as exc:
        logger.warning(
            "Prompt verifier could not enumerate CUGA runtime variables: {}: {}",
            type(exc).__name__,
            exc,
        )
        return {}

    get_variable = getattr(manager, "get_variable", None)
    if not callable(get_variable):
        logger.warning(
            "Prompt verifier VariablesManager has no callable get_variable(); "
            "persistent names will remain unresolved"
        )
        return {}

    snapshot: dict[str, Any] = {}
    for raw_name in names:
        name = str(raw_name)
        if not name.isidentifier():
            continue
        try:
            snapshot[name] = get_variable(name)
        except Exception as exc:
            logger.warning(
                "Prompt verifier could not read runtime variable {}: {}: {}",
                name,
                type(exc).__name__,
                exc,
            )

    logger.debug(
        "Prompt verifier runtime-variable snapshot: names={}",
        sorted(snapshot),
    )
    return snapshot


def _graph_node_source_type(graph: MemoryGraph, node_id: str) -> SourceType | None:
    node = graph.nodes[node_id]

    if node.source_refs:
        return node.source_refs[0].source_type

    root = graph.nodes.get(node.source_root_id)
    if root and root.source_refs:
        return root.source_refs[0].source_type

    return None


_SOURCE_TAG: dict[SourceType, tuple[str, str]] = {
    SourceType.POLICY: ("P", "policy"),
    SourceType.DOCUMENT: ("D", "document"),
    SourceType.TOOL_RESULT: ("T", "tool"),
    SourceType.USER_MESSAGE: ("U", "user"),
    SourceType.ASSISTANT_MESSAGE: ("A", "assistant"),
}


def _local_id_sort_key(local_id: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Za-z]+)(\d+)", local_id)
    if match is None:
        return local_id, 0
    return match.group(1), int(match.group(2))


def _build_local_evidence_ids(
    *,
    adherence_graph: MemoryGraph,
    adherence_node_ids: set[str],
    state_graph: MemoryGraph,
    state_node_ids: set[str],
) -> dict[tuple[str, str], str]:
    """Assign short verifier-local IDs while preserving source type."""
    counters: dict[str, int] = {}
    local_ids: dict[tuple[str, str], str] = {}

    def assign(
        space: str,
        graph: MemoryGraph,
        node_ids: set[str],
    ) -> None:
        ordered = sorted(
            node_ids,
            key=lambda node_id: (
                (_graph_node_source_type(graph, node_id).value
                 if _graph_node_source_type(graph, node_id) is not None
                 else "unknown"),
                _one_line(graph.nodes[node_id].content),
                node_id,
            ),
        )

        for node_id in ordered:
            source_type = _graph_node_source_type(graph, node_id)
            prefix, _ = _SOURCE_TAG.get(source_type, ("E", "unknown"))
            counters[prefix] = counters.get(prefix, 0) + 1
            local_ids[(space, node_id)] = f"{prefix}{counters[prefix]}"

    assign("adherence", adherence_graph, adherence_node_ids)
    assign("state", state_graph, state_node_ids)
    return local_ids


def _render_evidence_lines(
    *,
    adherence_graph: MemoryGraph,
    adherence_node_ids: set[str],
    state_graph: MemoryGraph,
    state_node_ids: set[str],
    local_ids: dict[tuple[str, str], str],
) -> list[str]:
    rows: list[tuple[str, str]] = []

    for space, graph, node_ids in (
        ("adherence", adherence_graph, adherence_node_ids),
        ("state", state_graph, state_node_ids),
    ):
        for node_id in node_ids:
            local_id = local_ids[(space, node_id)]
            source_type = _graph_node_source_type(graph, node_id)
            _, type_label = _SOURCE_TAG.get(source_type, ("E", "unknown"))
            rows.append(
                (
                    local_id,
                    f"{local_id} [{type_label}]: "
                    f"{_one_line(graph.nodes[node_id].content)}",
                )
            )

    return [
        line
        for _, line in sorted(
            rows,
            key=lambda item: _local_id_sort_key(item[0]),
        )
    ]


def _render_relation_lines(
    *,
    adherence_graph: MemoryGraph,
    adherence_edge_ids: set[str],
    state_graph: MemoryGraph,
    state_edge_ids: set[str],
    local_ids: dict[tuple[str, str], str],
) -> list[str]:
    """Render relations only when both selected endpoints are present."""
    rendered: set[str] = set()

    for space, graph, edge_ids in (
        ("adherence", adherence_graph, adherence_edge_ids),
        ("state", state_graph, state_edge_ids),
    ):
        for edge_id in edge_ids:
            edge = graph.edges[edge_id]
            source_local = local_ids.get((space, edge.source_id))
            target_local = local_ids.get((space, edge.target_id))

            if source_local is None or target_local is None:
                logger.debug(
                    "Skipping verifier relation with unselected endpoint: "
                    "space={} edge_id={} source={} target={}",
                    space,
                    edge_id,
                    edge.source_id,
                    edge.target_id,
                )
                continue

            if edge.directed:
                rendered.add(
                    f"{source_local} --{edge.relation.value}--> {target_local}"
                )
            else:
                rendered.add(
                    f"{source_local} --{edge.relation.value}-- {target_local}"
                )

    return sorted(rendered)


def _schema_argument_names(
    schema: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(schema, dict):
        return []

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []

    required = set(schema.get("required") or [])
    result: list[str] = []

    for name in properties:
        label = str(name)
        if name not in required:
            label += "?"
        result.append(label)

    return result


def _runtime_tool_signatures(
    runtime_facts: dict[str, Any] | None,
) -> tuple[list[str], list[str], bool]:
    """Project rich runtime tool metadata to compact callable signatures."""
    facts = dict(runtime_facts or {})
    prompt_tools = facts.get("prompt_visible_tools") or []

    schema_by_name: dict[str, dict[str, Any] | None] = {}
    for tool in prompt_tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        schema = tool.get("input_schema")
        schema_by_name[name] = schema if isinstance(schema, dict) else None

    def signature(name: str) -> str:
        args = _schema_argument_names(schema_by_name.get(name))
        if name in schema_by_name:
            return f"{name}({', '.join(args)})"
        return f"{name}(...)"

    directly_callable = sorted(
        {
            signature(str(name).strip())
            for name in facts.get("execution_context_tool_names", [])
            if str(name).strip()
        }
    )
    prompt_visible = sorted(
        {
            signature(str(name).strip())
            for name in facts.get("prompt_visible_tool_names", [])
            if str(name).strip()
        }
    )

    return (
        directly_callable,
        prompt_visible,
        bool(facts.get("find_tools_enabled", False)),
    )


def _render_resolved_expression(value: Any) -> str:
    if not isinstance(value, _ResolvedExpression):
        return str(value)

    if value.provenance == "prior_call_result":
        deps = ",".join(value.dependency_call_ids) or "?"
        return f"result_of({deps})"

    if value.provenance == "derived_from_prior_call_result":
        deps = ",".join(value.dependency_call_ids) or "?"
        return f"derived_from_result_of({deps})"

    if value.provenance == "unresolved":
        return f"unresolved({value.rendered})"

    return value.rendered


def _render_candidate_call(call: dict[str, Any]) -> str:
    args = [
        _render_resolved_expression(value)
        for value in call.get("positional_args", [])
    ]
    args.extend(
        f"{name}={_render_resolved_expression(value)}"
        for name, value in (call.get("keyword_args") or {}).items()
    )
    rendered = f"await {call.get('call', '<unknown>')}({', '.join(args)})"
    assigned_to = str(call.get("assigned_to") or "").strip()
    if assigned_to:
        return f"{assigned_to} = {rendered}"
    return rendered


def _argument_provenance_lines(
    calls: list[dict[str, Any]],
) -> list[str]:
    lines: list[str] = []

    for index, call in enumerate(calls, start=1):
        call_id = str(call.get("call_id") or f"C{index}")

        for arg_index, value in enumerate(call.get("positional_args", []), start=1):
            if not isinstance(value, _ResolvedExpression):
                continue
            label = f"{call_id}.arg{arg_index}"
            lines.append(f"{label}: {_provenance_text(value)}")

        for name, value in (call.get("keyword_args") or {}).items():
            if not isinstance(value, _ResolvedExpression):
                continue
            lines.append(f"{call_id}.{name}: {_provenance_text(value)}")

    return lines


def _provenance_text(value: _ResolvedExpression) -> str:
    if value.provenance == "literal":
        return "literal"

    if value.provenance == "local_static":
        names = [name for name in value.source_names if name]
        if names:
            return f"local_static({names[0]})"
        return "local_static"

    if value.provenance == "runtime_variable":
        names = [name for name in value.source_names if name]
        if names:
            return f"runtime_variable({', '.join(names)})"
        return "runtime_variable"

    if value.provenance == "prior_call_result":
        deps = ",".join(value.dependency_call_ids) or "?"
        names = [name for name in value.source_names if name]
        if names:
            return (
                f"prior_call_result({deps} via {names[0]}); "
                "grounding=exempt_same_candidate_tool_dependency"
            )
        return (
            f"prior_call_result({deps}); "
            "grounding=exempt_same_candidate_tool_dependency"
        )

    if value.provenance == "derived_from_prior_call_result":
        deps = ",".join(value.dependency_call_ids) or "?"
        names = [name for name in value.source_names if name]
        if names:
            return (
                f"derived_from_prior_call_result({deps} via {names[0]}); "
                "grounding=exempt_same_candidate_tool_dependency"
            )
        return (
            f"derived_from_prior_call_result({deps}); "
            "grounding=exempt_same_candidate_tool_dependency"
        )

    return f"unresolved({value.rendered})"


def _called_tool_retrieval_lines(
    calls: list[dict[str, Any]],
    runtime_facts: dict[str, Any] | None,
) -> list[str]:
    """Render deterministic retrieval text for each proposed runtime tool call.

    Each call contributes, in execution order:
    1. the exact function name;
    2. the first paragraph of its prompt-visible runtime description, when present;
    3. every proposed parameter as ``name: resolved_value``.

    Parameter values come from the already-resolved verifier-side call extraction,
    so literals, local deterministic values, and CUGA runtime variables use their
    concrete values. Same-candidate dependencies remain explicit as
    ``result_of(Cn)`` / ``derived_from_result_of(Cn)``, and unresolved expressions
    remain visibly unresolved. Positional arguments are mapped deterministically to
    runtime-schema property names when available, with ``argN`` as a fallback.

    We intentionally preserve repeated calls to the same function: two calls with
    different parameters must produce different retrieval text. Raw Python syntax,
    assignment targets, ``await``, examples, return-value contracts, and later
    description paragraphs remain excluded.
    """
    facts = dict(runtime_facts or {})
    prompt_tools = facts.get("prompt_visible_tools") or []

    tool_by_name: dict[str, dict[str, Any]] = {}
    for tool in prompt_tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if name:
            tool_by_name[name] = tool

    lines: list[str] = []
    for call in calls:
        name = str(call.get("call") or "").strip()
        if not name:
            continue

        # Preserve the exact callable identity as its own retrieval signal, even
        # if no runtime description is available. Policies may mention a tool by
        # its exact function name.
        lines.append(name)

        tool = tool_by_name.get(name)
        schema_parameter_names: list[str] = []
        if tool is not None:
            description = str(tool.get("description") or "").strip()
            if description:
                purpose = re.split(r"\n\s*\n", description, maxsplit=1)[0].strip()
                if purpose:
                    lines.append(purpose)

            schema = tool.get("input_schema")
            if isinstance(schema, dict):
                properties = schema.get("properties")
                if isinstance(properties, dict):
                    schema_parameter_names = [str(param) for param in properties]

        # Candidate extraction has already deterministically resolved every value
        # it safely can. Reuse that representation instead of re-evaluating code.
        positional_args = list(call.get("positional_args") or [])
        for index, value in enumerate(positional_args):
            parameter_name = (
                schema_parameter_names[index]
                if index < len(schema_parameter_names)
                else f"arg{index + 1}"
            )
            lines.append(
                f"{parameter_name}: {_render_resolved_expression(value)}"
            )

        for parameter_name, value in (call.get("keyword_args") or {}).items():
            lines.append(
                f"{parameter_name}: {_render_resolved_expression(value)}"
            )

    return lines

def _called_tool_spec_lines(
    calls: list[dict[str, Any]],
    runtime_facts: dict[str, Any] | None,
) -> list[str]:
    """Render compact deterministic specs only for tools actually called."""
    facts = dict(runtime_facts or {})
    prompt_tools = facts.get("prompt_visible_tools") or []

    tool_by_name: dict[str, dict[str, Any]] = {}
    for tool in prompt_tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if name:
            tool_by_name[name] = tool

    lines: list[str] = []
    seen: set[str] = set()

    for call in calls:
        name = str(call.get("call") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)

        tool = tool_by_name.get(name)
        if tool is None:
            lines.append(f"{name}(...): no prompt-visible schema/description supplied")
            continue

        args = _schema_argument_names(tool.get("input_schema"))
        signature = f"{name}({', '.join(args)})"
        description = _one_line(str(tool.get("description") or ""))
        if description:
            lines.append(f"{signature}: {description}")
        else:
            lines.append(signature)

    return lines


def _restore_candidate_atom_ids(
    decision: VerificationDecision,
    *,
    local_to_real: dict[str, str],
) -> VerificationDecision:
    """Validate C1/C2/... IDs, then restore graph IDs for downstream logging."""
    _validate_atom_decisions(
        decision,
        list(local_to_real),
    )

    return VerificationDecision(
        atoms=[
            CandidateAtomDecision(
                candidate_atom_id=local_to_real[item.candidate_atom_id],
                verdict=item.verdict,
                reason=item.reason,
            )
            for item in decision.atoms
        ]
    )


def _validate_context_decision(
    decision: CandidateContextDecision,
    context_entries: list[_CandidateQueryContextEntry],
) -> None:
    valid_ids = {entry.context_id for entry in context_entries}

    # Some structured-output models occasionally return a bare numeric Q ID
    # (for example, "38" instead of "Q38"). Treat that as an unambiguous
    # formatting variation only when the corresponding Q-prefixed ID actually
    # exists in the current candidate-query context. Unknown IDs remain errors.
    normalized_ids: list[str] = []
    for raw_id in decision.violated_context_ids:
        context_id = str(raw_id).strip()
        if context_id not in valid_ids and context_id.isdigit():
            prefixed_id = f"Q{context_id}"
            if prefixed_id in valid_ids:
                context_id = prefixed_id
        normalized_ids.append(context_id)

    decision.violated_context_ids = normalized_ids

    invalid = sorted(set(decision.violated_context_ids) - valid_ids)
    if invalid:
        raise PromptVerificationError(
            "Verifier returned unknown candidate-query-context IDs: " + ", ".join(invalid)
        )
    if decision.verdict == "approved" and decision.violated_context_ids:
        raise PromptVerificationError(
            "Verifier returned violated_context_ids for an approved candidate"
        )


def _validate_atom_decisions(
    decision: VerificationDecision,
    candidate_atom_ids: list[str],
) -> None:
    expected = set(candidate_atom_ids)
    returned_ids = [
        atom_decision.candidate_atom_id
        for atom_decision in decision.atoms
    ]
    returned = set(returned_ids)

    if len(returned_ids) != len(returned):
        raise PromptVerificationError(
            "Verifier returned duplicate candidate_atom_id values"
        )

    if returned != expected:
        missing = sorted(expected - returned)
        unexpected = sorted(returned - expected)
        raise PromptVerificationError(
            "Verifier returned the wrong candidate atom IDs. "
            f"missing={missing} unexpected={unexpected}"
        )


def _aggregate_atom_decisions(
    decision: VerificationDecision,
) -> tuple[bool, str, str]:
    """Aggregate per-atom verdicts deterministically.

    Precedence:
        contradicted > insufficient > supported/not_applicable
    """
    contradicted = [
        item
        for item in decision.atoms
        if item.verdict == "contradicted"
    ]
    insufficient = [
        item
        for item in decision.atoms
        if item.verdict == "insufficient"
    ]

    blockers = [*contradicted, *insufficient]

    if contradicted:
        global_verdict = "contradicted"
    elif insufficient:
        global_verdict = "insufficient"
    else:
        global_verdict = "supported"

    if not blockers:
        return True, "", global_verdict

    reasons: list[str] = []
    seen: set[str] = set()

    for item in blockers:
        reason = item.reason.strip()
        if not reason or reason in seen:
            continue
        seen.add(reason)
        reasons.append(reason)

    return False, " ".join(reasons), global_verdict


def _logic_literal_text(literal: Any, slot_by_id: dict[str, Any]) -> str:
    slot_id = getattr(literal, "slot_id", None)
    slot = slot_by_id.get(slot_id)
    text = _one_line(slot.source_text) if slot is not None else f"slot:{slot_id}"
    return text if bool(getattr(literal, "value", True)) else f"NOT({text})"


def _logic_expression_text(expression: Any, slot_by_id: dict[str, Any]) -> str:
    slot_id = getattr(expression, "slot_id", None)
    if slot_id is not None:
        slot = slot_by_id.get(slot_id)
        return _one_line(slot.source_text) if slot is not None else f"slot:{slot_id}"

    operator = getattr(expression, "operator", None)
    operands = [
        _logic_expression_text(operand, slot_by_id)
        for operand in getattr(expression, "operands", [])
    ]
    op_value = getattr(operator, "value", str(operator))
    threshold = getattr(expression, "threshold", None)
    if op_value == "not" and operands:
        return f"NOT({operands[0]})"
    if op_value in {"at_least", "at_most", "exactly"}:
        return f"{op_value.upper()}({threshold}; {', '.join(operands)})"
    return f"{op_value.upper()}({', '.join(operands)})"


def _logic_rule_text(rule: Any, slot_by_id: dict[str, Any]) -> str:
    antecedent = getattr(rule, "antecedent", None)
    consequent = getattr(rule, "consequent", None)
    if antecedent is not None and consequent is not None:
        return (
            f"IF {_logic_literal_text(antecedent, slot_by_id)} "
            f"THEN {_logic_literal_text(consequent, slot_by_id)}"
        )

    return (
        f"IF {_logic_expression_text(rule.condition, slot_by_id)} "
        f"THEN {_logic_expression_text(rule.effect, slot_by_id)}"
    )


async def _candidate_logic_results(
    *,
    candidate_graph: MemoryGraph,
    candidate_atoms: list[Any],
    logic_graphs: list[MemoryGraph],
) -> dict[str, dict[str, Any]]:
    """Match candidate atoms to logic slots and run deterministic entailment."""
    if not logic_graphs or not any(graph.logic_layer.slots for graph in logic_graphs):
        return {}

    matches = await asyncio.to_thread(
        match_nodes_to_logic_slots,
        source_graph=candidate_graph,
        node_ids={node.id for node in candidate_atoms},
        target_graphs=logic_graphs,
        bind=False,
    )
    if not matches:
        return {}

    slot_by_id = {
        slot.id: slot
        for graph in logic_graphs
        for slot in graph.logic_layer.slots
    }
    rule_by_id = {
        rule.id: rule
        for graph in logic_graphs
        for rule in [
            *graph.logic_layer.relations,
            *graph.logic_layer.compound_rules,
        ]
    }

    results: dict[str, dict[str, Any]] = {}
    for candidate_atom in candidate_atoms:
        bindings = matches.get(candidate_atom.id, [])
        if not bindings:
            continue
        result = analyze_entailment(
            graphs=logic_graphs,
            queried_slot_values=[
                (binding.slot_id, binding.value)
                for binding in bindings
            ],
        )
        relevant_rules = [
            rule_by_id[rule_id]
            for rule_id in result.relevant_rule_ids
            if rule_id in rule_by_id
        ]
        results[candidate_atom.id] = {
            "result": result,
            "candidate_slots": [
                (
                    _one_line(slot_by_id[binding.slot_id].source_text)
                    if binding.value
                    else "NOT " + _one_line(slot_by_id[binding.slot_id].source_text)
                )
                for binding in bindings
                if binding.slot_id in slot_by_id
            ],
            "established_slots": [
                _one_line(slot_by_id[slot_id].source_text)
                for slot_id in result.established_slot_ids
                if slot_id in slot_by_id
            ],
            "unresolved_slots": [
                _one_line(slot_by_id[slot_id].source_text)
                for slot_id in result.unresolved_slot_ids
                if slot_id in slot_by_id
            ],
            "rules": [
                _logic_rule_text(rule, slot_by_id)
                for rule in relevant_rules
            ],
        }
    return results


def _render_candidate_logic_line(
    *,
    candidate_local_id: str,
    payload: dict[str, Any] | None,
) -> str:
    if payload is None:
        return f"{candidate_local_id}: no_matching_logic"
    result = payload["result"]
    parts = [
        f"{candidate_local_id}: status={result.status}",
        "candidate_slots=" + (" | ".join(payload["candidate_slots"]) or "-"),
        "established=" + (" | ".join(payload["established_slots"]) or "-"),
        "unresolved=" + (" | ".join(payload["unresolved_slots"]) or "-"),
        "rules=" + (" | ".join(payload["rules"]) or "-"),
    ]
    return "; ".join(parts)


async def _verify_with_graphs(
    *,
    candidate: str,
    candidate_kind: CandidateKind,
    candidate_graph: MemoryGraph,
    candidate_atoms_override: list[Any] | None = None,
    state_graph: MemoryGraph,
    execution_graph: MemoryGraph,
    knowledge_base_graph: MemoryGraph,
    cuga_policy_graph: MemoryGraph,
    playbook_graph: MemoryGraph,
    reasoning_graph: MemoryGraph | None = None,
    runtime_facts: dict[str, Any] | None = None,
    runtime_variables: dict[str, Any] | None = None,
    previous_rejection: tuple[str, str] | None = None,
) -> CandidateContextDecision:
    """Verify raw candidate against reconstructed source-level query context.

    Candidate atoms are retrieval queries only. They are never serialized into
    the final verifier prompt and therefore cannot become claims merely because
    decomposition lost conditional/reference/temporal scope. Tool-execution
    candidates are the exception to semantic decomposition: the complete raw code
    generation is represented as one atomic retrieval query.
    """
    all_candidate_atoms = sorted(
        (
            list(candidate_atoms_override)
            if candidate_atoms_override is not None
            else candidate_graph.atomic_nodes(active_only=True)
        ),
        key=_candidate_atom_order_key,
    )

    # Verifier-added structural labels help frame the candidate during graph
    # construction, but they are not semantic claims/actions from the agent and
    # must not initiate evidence retrieval. The actual candidate remains present
    # in [RAW_CANDIDATE], and candidate_kind is provided separately to the LLM.
    verifier_scaffolding_labels = {
        "proposed tool execution",
    }
    candidate_atoms = [
        atom
        for atom in all_candidate_atoms
        if str(getattr(atom, "content", "")).strip().rstrip(":").casefold()
        not in verifier_scaffolding_labels
    ]
    omitted_scaffolding_atoms = len(all_candidate_atoms) - len(candidate_atoms)
    if omitted_scaffolding_atoms:
        logger.debug(
            "Prompt verifier excluded verifier-generated scaffolding from "
            "retrieval: omitted_atoms={} remaining_retrieval_atoms={}",
            omitted_scaffolding_atoms,
            len(candidate_atoms),
        )

    if not candidate_atoms:
        raise PromptVerificationError(
            "Candidate decomposition produced no atomic retrieval propositions "
            "after excluding verifier-generated scaffolding"
        )

    candidate_calls = await _extract_candidate_calls(
        candidate,
        runtime_variables=runtime_variables,
    )
    is_tool_execution = candidate_kind == "tool_execution"
    if is_tool_execution and not candidate_calls:
        raise PromptVerificationError(
            "candidate_kind='tool_execution' but no awaited tool calls were found"
        )
    if candidate_kind == "reasoning" and candidate_calls:
        raise PromptVerificationError(
            "A reasoning candidate cannot also contain executable awaited tool calls"
        )

    context_entries = _build_candidate_query_context(
        candidate_atoms=candidate_atoms,
        cuga_policy_graph=cuga_policy_graph,
        playbook_graph=playbook_graph,
        knowledge_base_graph=knowledge_base_graph,
        state_graph=state_graph,
        execution_graph=execution_graph,
    )
    _append_previous_verifier_rejection_context(
        context_entries,
        previous_rejection,
    )
    context_lines = _render_candidate_query_context(context_entries)

    reasoning_history_lines = _reasoning_history_lines()
    execution_lines: list[str] = []
    if is_tool_execution:
        call_lines = [
            f"{str(call.get('call_id') or f'C{index}')}: {_render_candidate_call(call)}"
            for index, call in enumerate(candidate_calls, start=1)
        ]
        argument_provenance_lines = _argument_provenance_lines(candidate_calls)
        tool_spec_lines = _called_tool_spec_lines(candidate_calls, runtime_facts)
        directly_callable, prompt_visible, find_tools_enabled = _runtime_tool_signatures(
            runtime_facts
        )
        execution_lines = [
            "CALLS:",
            *(call_lines or ["(none)"]),
            "ARGUMENT_PROVENANCE:",
            *(argument_provenance_lines or ["(none)"]),
            "CALLED_TOOL_SPECS:",
            *(tool_spec_lines or ["(none)"]),
            "RUNTIME:",
            "directly_callable: " + (", ".join(directly_callable) if directly_callable else "(none)"),
            "prompt_visible: " + (", ".join(prompt_visible) if prompt_visible else "(none)"),
            f"find_tools_enabled: {str(find_tools_enabled).lower()}",
        ]

    verifier_text = "\n".join(
        [
            "[CANDIDATE_KIND]",
            candidate_kind,
            "",
            "[RAW_CANDIDATE]",
            candidate.strip(),
            "",
            "[CANDIDATE_QUERY_CONTEXT]",
            *(context_lines or ["(none)"]),
            "",
            "[EXECUTION_DETAILS]",
            *(execution_lines or ["(none)"]),
            "",
            "[REASONING_HISTORY]",
            *(reasoning_history_lines or ["(none)"]),
        ]
    )

    rejection_only = bool(
        getattr(settings.advanced_features, "prompt_verification_rejection_only", False)
    )
    system_prompt = system_prompt_for_mode(rejection_only=rejection_only)
    messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=verifier_text),
    ]

    candidate_hash = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:12]
    verification_id = f"{candidate_kind}:{candidate_hash}:{time.time_ns()}"
    decision = await invoke_verifier_decision(
        messages=messages,
        schema=CandidateContextDecision,
        verification_id=verification_id,
        candidate_kind=candidate_kind,
        candidate_hash=candidate_hash,
    )
    _validate_context_decision(decision, context_entries)
    return decision


def _assistant_message_signature_from_text(content: str) -> str:
    return _message_signature({"role": "assistant", "content": content})


async def _build_full_candidate_graph_for_reasoning(
    *,
    source: _EvidenceSource,
    session_id: str,
    semaphore: asyncio.Semaphore,
) -> MemoryGraph:
    """Rebuild an accepted retrieval-only reasoning candidate with full logic."""
    full_source = _EvidenceSource(
        source_id=source.source_id,
        source_type=source.source_type,
        content=source.content,
        metadata={
            **source.metadata,
            "candidate": False,
            "post_approval_full_build": True,
            "skip_logic_enrichment": False,
        },
    )
    return await _build_graph(
        [full_source],
        session_id=session_id,
        semaphore=semaphore,
        link_relations=False,
    )


async def _promote_approved_terminal_to_state(
    *,
    candidate_content: str,
    session_id: str,
    semaphore: asyncio.Semaphore,
) -> set[str]:
    """Fully materialize an approved terminal response into persistent STATE.

    Verification uses only a retrieval-only candidate view. After approval we run
    the normal graph build (logic normalization/audits/repairs, S-P-O, embeddings)
    and append that result to STATE, including lateral linking and logic-slot
    augmentation. The raw-context cursor is advanced virtually so the same
    assistant message is not inserted again when it appears in the next model
    history prefix.
    """
    state_graph = _VERIFICATION_STATE.state_graph
    if state_graph is None:
        raise PromptVerificationError(
            "Cannot promote an approved terminal candidate before STATE exists"
        )

    virtual_index = _VERIFICATION_STATE.state_context_cursor
    source = _EvidenceSource(
        source_id=f"context-{virtual_index}-assistant",
        source_type=SourceType.ASSISTANT_MESSAGE,
        content=candidate_content,
        metadata={
            "message_role": "assistant",
            "context_index": virtual_index,
            "approved_candidate": True,
            "post_approval_full_build": True,
        },
    )

    start = time.perf_counter()
    new_node_ids = await _append_sources_to_graph(
        state_graph,
        [source],
        session_id=session_id,
        semaphore=semaphore,
    )
    if new_node_ids:
        await asyncio.to_thread(
            link_new_nodes_to_logic_slots,
            source_graph=state_graph,
            new_node_ids=new_node_ids,
            target_graphs=_logic_target_graphs(include_reasoning=True),
        )

    _VERIFICATION_STATE.state_context_signatures.append(
        _assistant_message_signature_from_text(candidate_content)
    )
    _VERIFICATION_STATE.state_context_cursor += 1

    logger.debug(
        "Prompt verifier promoted approved terminal candidate to STATE: "
        "source_id={} new_nodes={} state_nodes={} state_cursor={} wall_time={:.3f}s",
        source.source_id,
        len(new_node_ids),
        len(state_graph.nodes),
        _VERIFICATION_STATE.state_context_cursor,
        time.perf_counter() - start,
    )
    return new_node_ids


async def verify_candidate(
    current_context: list[dict[str, Any] | BaseMessage],
    candidate: str,
    *,
    candidate_kind: CandidateKind | None = None,
    runtime_variables: dict[str, Any] | None = None,
    previous_rejection: tuple[str, str] | None = None,
) -> VerificationResult:
    """Verify one reasoning/terminal/tool candidate against current evidence.

    ``current_context`` must contain only CUGA's committed/base model history. The
    temporary reasoning trajectory is owned separately by this module and must not
    be appended to ``current_context``; otherwise reasoning could leak into the
    persistent STATE graph and become self-grounding evidence.

    Candidate-kind behavior:
    - ``reasoning``: use a retrieval-only candidate view for verification. If
      accepted, rebuild it through the full graph/logic pipeline before committing
      it to the temporary reasoning graph.
    - ``terminal``: verify the untouched user-facing response against reconstructed
      source context. If accepted, rebuild it through the full graph/logic pipeline
      and append it to persistent STATE immediately. Previously verified reasoning
      remains separate until finalization.
    - ``tool_execution``: verify a pre-execution tool plan. Acceptance keeps the
      temporary reasoning trace alive. After the sandbox actually invokes tools,
      completed invocations are captured separately in the flat execution graph.
      Completed tool/execution observations are intentionally excluded from STATE.
    - ``None``: backwards-compatible inference; awaited calls imply
      ``tool_execution``, otherwise ``terminal``.

    When ``PROMPT_VERIFIER_BULK_VERIFICATION_ENABLED`` is true, terminal and
    reasoning candidates reuse their already-built Stanza hierarchy to form
    contiguous composite verification bulks. Each bulk retrieves and verifies
    only from its own candidate leaves, in original order, and verification stops
    on the first rejection. No bulk is committed independently; the original
    whole candidate is committed only if every bulk is approved. Tool-execution
    candidates always bypass this feature and are verified as one code unit.

    Rejected candidates never enter the reasoning graph. Previously accepted
    reasoning steps are retained across rejections so orchestration code can pass
    only the latest rejected candidate + verifier feedback on the next attempt.

    ``runtime_variables`` is a per-call snapshot of CUGA's current Python
    variable namespace supplied by the orchestration layer. The values are used
    only to resolve generated Python expressions/tool arguments; they are not
    inserted into STATE or any verifier graph. When omitted, the legacy registered
    VariablesManager path remains as a compatibility fallback.

    ``previous_rejection`` is the immediately preceding verifier-rejected
    ``(candidate, result)`` pair for the current correction chain. It is rendered
    as exactly one temporary Q statement for this verifier call and is never
    persisted into any graph or conversational STATE.
    """
    raw_candidate = candidate or ""
    if not raw_candidate.strip():
        return VerificationResult(
            valid=False,
            reason="The proposed output is empty.",
        )

    total_start = time.perf_counter()

    try:
        if runtime_variables is None:
            # Backwards-compatible fallback for older callers. Current CUGA
            # orchestration passes a fresh value snapshot on every verifier call.
            runtime_variables = _snapshot_runtime_variables()
        else:
            # Treat even an explicitly empty mapping as authoritative: do not
            # fall back to a previously registered/stale VariablesManager.
            runtime_variables = {
                str(name): value
                for name, value in runtime_variables.items()
                if isinstance(name, str) and name.isidentifier()
            }
            logger.debug(
                "Prompt verifier received per-call runtime-variable snapshot: names={}",
                sorted(runtime_variables),
            )

        candidate_calls = await _extract_candidate_calls(
            raw_candidate,
            runtime_variables=runtime_variables,
        )

        if candidate_kind is None:
            resolved_candidate_kind: CandidateKind = (
                "tool_execution" if candidate_calls else "terminal"
            )
        else:
            resolved_candidate_kind = candidate_kind

        # Preserve backwards compatibility with callers that classify every
        # non-reasoning output as terminal while still letting executable code
        # take the existing pre-execution verification branch.
        if resolved_candidate_kind == "terminal" and candidate_calls:
            resolved_candidate_kind = "tool_execution"

        if resolved_candidate_kind == "reasoning" and candidate_calls:
            return VerificationResult(
                valid=False,
                reason=(
                    "A reasoning step must contain only one intermediate reasoning "
                    "message and cannot also contain an executable tool call."
                ),
            )

        candidate_content = (
            _strip_reasoning_prefix(raw_candidate)
            if resolved_candidate_kind == "reasoning"
            else raw_candidate.strip()
        )
        if not candidate_content:
            return VerificationResult(
                valid=False,
                reason="The proposed reasoning step is empty.",
            )

        missing_authority: list[str] = []

        # Playbooks are optional. When no Playbooks are enabled, the SDK may skip
        # the Playbook-initialization hook entirely. Normalize that valid state to
        # an initialized empty graph so verification can proceed using CUGA policy,
        # STATE, execution, and knowledge-base evidence only.
        if _VERIFICATION_STATE.playbook_graph is None:
            _VERIFICATION_STATE.playbook_graph = MemoryGraph()
            logger.debug(
                "Prompt verifier Playbook graph defaulted to empty: session={}",
                _VERIFICATION_STATE.owner_session_id,
            )
        _VERIFICATION_STATE.playbook_initialized = True

        if (
            not _VERIFICATION_STATE.cuga_policy_initialized
            or _VERIFICATION_STATE.cuga_policy_graph is None
        ):
            missing_authority.append("cuga_policy_graph")

        if _VERIFICATION_STATE.graph_session_id is None:
            missing_authority.append("graph_session_id")

        if not _VERIFICATION_STATE.runtime_initialized:
            missing_authority.append("runtime_facts")

        if missing_authority:
            raise PromptVerificationError(
                "Verifier authority state is incomplete before candidate "
                "verification. Missing: "
                + ", ".join(missing_authority)
                + ". The CugaLite prompt path must initialize the CUGA-policy "
                "graph and verification runtime before verification begins."
            )

        cuga_policy_graph = _VERIFICATION_STATE.cuga_policy_graph
        playbook_graph = _VERIFICATION_STATE.playbook_graph
        session_id = _VERIFICATION_STATE.graph_session_id

        assert cuga_policy_graph is not None
        assert playbook_graph is not None
        assert session_id is not None

        semaphore = asyncio.Semaphore(_GRAPH_BUILD_CONCURRENCY)

        candidate_graph_content = candidate_content
        if resolved_candidate_kind == "tool_execution":
            logger.debug(
                "Prompt verifier tool-execution retrieval uses one atomic raw-code "
                "candidate node: called_tools={} candidate_chars={}",
                [str(call.get("call") or "") for call in candidate_calls],
                len(candidate_content),
            )

        prospective_reasoning_step_id = (
            _next_reasoning_step_id()
            if resolved_candidate_kind == "reasoning"
            else None
        )
        candidate_source = _EvidenceSource(
            source_id=(
                f"reasoning-{prospective_reasoning_step_id}"
                if prospective_reasoning_step_id is not None
                else "candidate"
            ),
            source_type=(
                _reasoning_source_type()
                if resolved_candidate_kind == "reasoning"
                else SourceType.ASSISTANT_MESSAGE
            ),
            content=candidate_graph_content,
            metadata={
                "candidate": True,
                "candidate_kind": resolved_candidate_kind,
                # Candidate semantics are used only to retrieve source context.
                # Full logic normalization/audits are deferred until approval.
                "skip_logic_enrichment": True,
                "candidate_retrieval_only": True,
                **(
                    {"reasoning_step_id": prospective_reasoning_step_id}
                    if prospective_reasoning_step_id is not None
                    else {}
                ),
            },
        )


        state_task = asyncio.create_task(
            _update_state_graph(
                current_context,
                session_id=session_id,
                semaphore=semaphore,
            )
        )
        execution_task = asyncio.create_task(
            _update_execution_graph(session_id=session_id)
        )
        knowledge_base_task = asyncio.create_task(
            _update_knowledge_base_graph(
                session_id=session_id,
                semaphore=semaphore,
            )
        )
        if resolved_candidate_kind == "tool_execution":
            candidate_task = asyncio.create_task(
                _build_tool_candidate_graph(
                    candidate=candidate_content,
                    session_id=session_id,
                )
            )
        else:
            candidate_task = asyncio.create_task(
                _build_graph(
                    [candidate_source],
                    session_id=session_id,
                    semaphore=semaphore,
                    link_relations=False,
                )
            )

        (
            state_update,
            execution_update,
            knowledge_base_update,
            candidate_graph,
        ) = await asyncio.gather(
            state_task,
            execution_task,
            knowledge_base_task,
            candidate_task,
        )

        state_graph, _, state_new_nodes = state_update
        execution_graph, _ = execution_update
        knowledge_base_graph, _ = knowledge_base_update

        if resolved_candidate_kind == "tool_execution":
            tool_candidate_atoms = candidate_graph.atomic_nodes(active_only=True)
            if (
                len(candidate_graph.nodes) != 1
                or len(tool_candidate_atoms) != 1
                or len(candidate_graph.edges) != 0
            ):
                raise PromptVerificationError(
                    "Tool-execution candidate graph must contain exactly one flat "
                    "atomic raw-code node and no edges; got "
                    f"nodes={len(candidate_graph.nodes)} "
                    f"atomic_nodes={len(tool_candidate_atoms)} "
                    f"edges={len(candidate_graph.edges)}"
                )

        if state_new_nodes:
            await asyncio.to_thread(
                link_new_nodes_to_logic_slots,
                source_graph=state_graph,
                new_node_ids=state_new_nodes,
                target_graphs=_logic_target_graphs(include_reasoning=True),
            )

        if (
            PROMPT_VERIFIER_BULK_VERIFICATION_ENABLED
            and resolved_candidate_kind != "tool_execution"
        ):
            candidate_atoms_for_bulks = sorted(
                candidate_graph.atomic_nodes(active_only=True),
                key=_candidate_atom_order_key,
            )
            bulks = _build_candidate_verification_bulks(
                candidate=candidate_content,
                candidate_graph=candidate_graph,
                candidate_atoms=candidate_atoms_for_bulks,
            )
            if not bulks:
                raise PromptVerificationError(
                    "Bulk verification is enabled but Stanza produced no "
                    "verification bulks"
                )

            atom_by_id = {
                atom.id: atom
                for atom in candidate_atoms_for_bulks
            }
            decision = CandidateContextDecision(verdict="approved")
            for bulk in bulks:
                bulk_atoms = [
                    atom_by_id[atom_id]
                    for atom_id in bulk.atom_ids
                    if atom_id in atom_by_id
                ]
                if not bulk_atoms:
                    raise PromptVerificationError(
                        "Stanza verification bulk contains no resolvable candidate "
                        f"atoms: bulk={bulk.index} atom_ids={bulk.atom_ids}"
                    )

                decision = await _verify_with_graphs(
                    candidate=bulk.content,
                    candidate_kind=resolved_candidate_kind,
                    candidate_graph=candidate_graph,
                    candidate_atoms_override=bulk_atoms,
                    state_graph=state_graph,
                    execution_graph=execution_graph,
                    knowledge_base_graph=knowledge_base_graph,
                    cuga_policy_graph=cuga_policy_graph,
                    playbook_graph=playbook_graph,
                    reasoning_graph=_VERIFICATION_STATE.reasoning_graph,
                    runtime_facts=_VERIFICATION_STATE.runtime_facts,
                    runtime_variables=runtime_variables,
                    previous_rejection=previous_rejection,
                )
                if decision.verdict == "rejected":
                    break
        else:
            decision = await _verify_with_graphs(
                candidate=candidate_content,
                candidate_kind=resolved_candidate_kind,
                candidate_graph=candidate_graph,
                state_graph=state_graph,
                execution_graph=execution_graph,
                knowledge_base_graph=knowledge_base_graph,
                cuga_policy_graph=cuga_policy_graph,
                playbook_graph=playbook_graph,
                reasoning_graph=_VERIFICATION_STATE.reasoning_graph,
                runtime_facts=_VERIFICATION_STATE.runtime_facts,
                runtime_variables=runtime_variables,
                previous_rejection=previous_rejection,
            )
        valid = decision.verdict == "approved"
        reason = "" if valid else decision.reason.strip()
        global_verdict = decision.verdict

        if valid and resolved_candidate_kind == "reasoning":
            assert prospective_reasoning_step_id is not None
            full_reasoning_graph = await _build_full_candidate_graph_for_reasoning(
                source=candidate_source,
                session_id=session_id,
                semaphore=semaphore,
            )
            await _commit_reasoning_candidate_graph(
                candidate_graph=full_reasoning_graph,
                content=candidate_content,
                step_id=prospective_reasoning_step_id,
            )
        elif valid and resolved_candidate_kind == "terminal":
            await _promote_approved_terminal_to_state(
                candidate_content=candidate_content,
                session_id=session_id,
                semaphore=semaphore,
            )
        # Approved pre-execution code is not promoted to STATE. Completed tool
        # observations are captured separately after execution.

        logger.info(
            "Prompt verifier decision: candidate_kind={} verdict={} "
            "violated_context_ids={} duration={:.3f}s",
            resolved_candidate_kind,
            global_verdict,
            decision.violated_context_ids,
            time.perf_counter() - total_start,
        )

        return VerificationResult(
            valid=valid,
            reason=reason,
        )

    except PromptVerificationError:
        logger.exception("Prompt verifier infrastructure failure")
        raise
    except Exception as exc:
        logger.exception("Prompt verifier infrastructure failure")
        raise PromptVerificationError(
            f"Prompt verifier failed: {type(exc).__name__}: {exc}"
        ) from exc
