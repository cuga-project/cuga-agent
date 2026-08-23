from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from cuga.backend.memory_graph import (
    GraphBuildRequest,
    GraphBuildResult,
    GraphBuilder,
    MemoryGraph,
    NodeKind,
    SourceType,
    link_new_nodes,
)
from cuga.backend.memory_graph.graph_serialization import (
    compute_prompt_hash,
    load_graph_for_prompt,
    save_graph,
)
from cuga.backend.memory_graph.retrieval import RankedNode, rank_nodes
from cuga.backend.memory_graph.traversal import (
    TraversalConfig,
    build_coverage_aware_mini_graphs,
)
from cuga.backend.llm.models import LLMManager
from cuga.config import settings


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


class PromptVerificationError(RuntimeError):
    """Raised when the verifier itself fails rather than rejecting a candidate."""


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


@dataclass(frozen=True)
class _EvidenceSource:
    source_id: str
    source_type: SourceType
    content: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _ResolvedExpression:
    """Verifier-side representation of one Python expression.

    ``static_value`` is populated only when the expression can be evaluated
    deterministically without executing candidate code. ``dependency_call_ids``
    records explicit dependencies on earlier awaited calls in the same candidate.
    """

    rendered: str
    provenance: Literal[
        "literal",
        "local_static",
        "prior_call_result",
        "unresolved",
    ]
    static_value: Any | None = None
    dependency_call_ids: tuple[str, ...] = ()
    source_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class _LocalBinding:
    resolved: _ResolvedExpression


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

    ``state_graph`` is persistent for the session. ``state_context_cursor`` is
    the number of raw model-context messages already incorporated into that graph.
    ``state_context_signatures`` records the processed raw prefix so we can detect
    truncation or rewriting and safely fall back to a full state rebuild.

    ``reasoning_graph`` is a separate temporary trajectory containing only
    accepted intermediate reasoning steps for the current internal generation
    cycle. It is never merged into STATE or authority and therefore cannot be
    used as self-grounding evidence for external facts.
    """

    owner_session_id: str | None = None
    graph_session_id: str | None = None
    cuga_policy_initialized: bool = False
    playbook_initialized: bool = False
    cuga_policy_graph: MemoryGraph | None = None
    playbook_graph: MemoryGraph | None = None
    runtime_initialized: bool = False
    runtime_facts: dict[str, Any] = field(default_factory=dict)
    state_graph: MemoryGraph | None = None
    state_context_cursor: int = 0
    state_context_signatures: list[str] = field(default_factory=list)
    reasoning_graph: MemoryGraph | None = None
    reasoning_steps: list[_ReasoningStepRecord] = field(default_factory=list)


_VERIFICATION_STATE = _PromptVerificationState()


_PYTHON_BLOCK_RE = re.compile(r"```python\s*(.*?)```", flags=re.IGNORECASE | re.DOTALL)
_REASONING_PREFIX_RE = re.compile(r"^\s*reasoning\s*:\s*", flags=re.IGNORECASE)
# Rebuilding the graph is intentionally expensive in this first implementation.
# Limit concurrent source builds so recursive graph construction does not fan out
# into an unbounded number of simultaneous model-call chains.
_GRAPH_BUILD_CONCURRENCY = 4

# Persist expensive, session-independent authority graphs between runs. The
# graph index and graph JSON files live together so indexed relative paths remain
# stable regardless of the process working directory used during later loads.
_GRAPH_CACHE_DIR = Path(".memory_graph_cache")
_GRAPH_CACHE_INDEX = _GRAPH_CACHE_DIR / "memory_graphs.json"

# Retrieve independently from the two evidence spaces. The CUGA-policy and
# Playbook graphs are combined into one logical ADHERENCE graph; STATE remains
# separate so normative and factual evidence cannot silently substitute for one
# another.
_VERIFIER_TOP_K = 5
_VERIFIER_TRAVERSAL_CONFIG = TraversalConfig(
    max_nodes_per_mini_graph=50,
)



_VERIFICATION_SYSTEM_PROMPT = """
You are a strict context-grounding verifier for a tool-using agent.

The user message contains a compact line-oriented projection with these sections:

[CANDIDATE_KIND]
For this prompt the value is terminal. The candidate is the proposed user-facing
assistant response, not an intermediate reasoning step.

[RAW_CANDIDATE]
The proposed user-facing assistant output.

[CANDIDATE_ATOMS]
Atomic propositions from the candidate. Candidate IDs are local IDs such as C1,
C2, and C3. Return exactly one decision for every supplied candidate ID.

[EVIDENCE]
Selected evidence statements:
    P1 [policy]: statement
    D1 [document]: statement
    T1 [tool]: statement
    U1 [user]: statement
    A1 [assistant]: statement
The local ID is only a reference. The bracketed type determines source semantics.

[RELATIONS]
Typed graph relations:
    P1 --REQUIRES--> P2
    U1 --SAME_ENTITY-- U2
Directed arrows preserve source/target direction. Symmetric relations have no
arrow. RELATED_TO alone is not proof of support.

[RELEVANCE]
For each candidate atom, lists its selected ADHERENCE and STATE evidence IDs.
Judge that atom only from its listed evidence statements and relations whose two
endpoints are both listed for that atom. A truncation marker means traversal hit
its configured node limit. Absence from selected evidence is not proof of the
opposite.

[REASONING_HISTORY]
Previously accepted intermediate reasoning steps for this same internal
generation cycle. They provide trajectory/continuity context ONLY. They are not
authoritative evidence for external facts, successful actions, permissions, or
satisfied prerequisites. Never use a prior reasoning step to self-ground a
material claim in the terminal candidate.

[RUNTIME]
Deterministic runtime tool facts. "directly_callable" is authoritative for tools
available directly to the agent in the current prepared execution context.
"prompt_visible" identifies tools explicitly exposed to the generation model.
Do not claim a directly_callable tool is unavailable or requires discovery or
unlocking unless explicit supplied authority establishes an additional
requirement.

The candidate is NOT evidence for itself.

You MUST return exactly one atom decision for every supplied candidate_atom_id.
Do not omit, duplicate, rename, or invent candidate atom IDs.

Atom verdict semantics
----------------------
supported:
    The material candidate atom is grounded and permitted by the supplied
    evidence that applies to it.

contradicted:
    The material candidate atom conflicts with stronger evidence or an
    applicable authority rule.

insufficient:
    The material candidate atom requires a factual premise, successful outcome,
    permission, prerequisite, or consequential argument that is not established
    by the supplied evidence.

not_applicable:
    The atom is merely conversational/stylistic framing and makes no material
    factual, policy, permission, action, success, or precondition claim.

Verification rules
------------------
A. Ground factual assertions and action arguments.
   - Tool evidence is authoritative for observed environment state and successful
     or failed tool execution.
   - User evidence is authoritative for the user's request, preference, consent,
     and values they explicitly provide, but NOT automatically for bank records,
     tool outcomes, policy, or other external state.
   - Assistant evidence is low-authority conversational memory. It may help with
     continuity, but cannot by itself establish environment facts, successful
     actions, or satisfied safety/policy preconditions.

B. Enforce applicable ADHERENCE evidence.
   - Policy evidence is binding behavioral authority.
   - Document evidence is authoritative for the domain facts/rules it states.
   - Check positive requirements as well as prohibitions.
   - Respect conditions, modality, temporal scope, exceptions, qualifiers, and
     explicit override/supersession language.
   - Do not invent precedence that the evidence does not establish.

C. Distinguish a prerequisite from a prerequisite-establishing procedure.
   - A rule may require condition P before a protected operation while another
     rule permits/requires action V specifically to establish P.
   - Do NOT reject V merely because P is not already true when V is the procedure
     that establishes or checks P.
   - Example: V --ENABLES--> P and protected_action --REQUIRES--> P.
     The requirement on protected_action does not automatically prohibit V.
   - Only require P before V when supplied authority explicitly establishes that
     V itself requires P.

D. Claims about actions.
   - Do not accept a claim that an action succeeded unless pre-candidate STATE
     evidence shows that it succeeded.
   - If the candidate merely says it will perform an internal lookup/action when
     that action should be executed now and all required arguments are available,
     treat the material atom as invalid.
   - Tool availability or unlock requirements may be judged ONLY from [RUNTIME]
     or explicit supplied evidence. Never invent an unlock/discovery requirement.

E. Retrieval scope.
   - You receive selected evidence, not the entire original graphs.
   - Do not treat absence from selected evidence as positive evidence that the
     opposite is true.
   - If a material premise required for an atom is not established, use
     insufficient rather than inventing missing facts.

F. Conversational atoms.
   - Politeness, acknowledgements, and stylistic framing that make no material
     claim should be not_applicable.
   - A clarification question can be supported when asking is allowed/required
     and the requested information is genuinely missing.
   - Do not mark a clarification question supported when supplied STATE evidence
     already contains enough information to proceed.

Conflict precedence
-------------------
Use source semantics:
- policy governs what the agent may/must do;
- document governs domain facts/rules described by that document;
- tool governs current observed environment state/tool outcomes;
- user governs user intent/consent/provided values;
- assistant is weakest and is overridden by stronger sources.

Do not use external/world knowledge to fill missing evidence. You may use ordinary
linguistic and logical reasoning to compare statements, conditions, and relations.

Keep each atom reason short and actionable, ideally one sentence.
""".strip()


_REASONING_VERIFICATION_SYSTEM_PROMPT = """
You are a strict verifier for ONE intermediate reasoning step produced by a
 tool-using agent.

This is not yet a user-facing answer and not yet an executable tool action. The
candidate is one explicit step in a temporary reasoning trajectory. A valid
reasoning step may be exploratory, conditional, uncertain, or planning-oriented.
Do not require an explicitly tentative hypothesis to already be established as
an external fact merely because the agent is considering it.

The user message contains these sections:

[CANDIDATE_KIND]
The value is reasoning.

[RAW_CANDIDATE]
The proposed intermediate reasoning step. A leading "REASONING:" marker may
have been removed before verification.

[CANDIDATE_ATOMS]
Atomic propositions extracted from the reasoning step. Return exactly one
decision for every supplied candidate ID.

[EVIDENCE]
Selected grounding evidence from policy/document/tool/user/assistant STATE.
These are the only sources that can ground external facts.

[RELATIONS]
Typed relations among selected grounding-evidence statements.

[RELEVANCE]
The selected ADHERENCE and STATE evidence available to each candidate atom.

[REASONING_HISTORY]
Previously accepted reasoning steps in this same internal reasoning cycle.
These steps may establish trajectory, hypotheses already under consideration,
prior planning choices, and logical continuity. They are NOT authoritative
external evidence and must never be used to prove bank state, tool outcomes,
policy permissions, user-provided values, or satisfied prerequisites.

[RUNTIME]
Deterministic runtime tool facts.

Reasoning-step semantics
------------------------
Mark an atom supported when it is a legitimate intermediate reasoning move given
the grounding evidence and prior reasoning trajectory. This includes explicitly
framed possibilities, hypotheses, questions-to-resolve, conditionals, and plans
that do not falsely assert an ungrounded external fact as established.

Mark contradicted when the reasoning step conflicts with supplied stronger
policy/state evidence or asserts a conclusion that supplied evidence directly
refutes.

Mark insufficient when the step presents a material external premise as
established even though the supplied grounding evidence does not establish it.
Do NOT use insufficient merely because a clearly tentative hypothesis has not
been proven yet.

Use not_applicable only for non-material stylistic/framing atoms.

Important distinctions
----------------------
- "I should check whether the user is verified" can be valid reasoning without
  verification already being established.
- "If email plus phone are sufficient, then I can proceed" is conditional and
  does not assert that sufficiency is already established.
- "The user is verified" is an external factual conclusion and needs grounding.
- "I should call tool X next" is a planning statement, not proof that tool X has
  already run or succeeded.
- Prior reasoning can explain why the agent is exploring a branch, but cannot
  upgrade a hypothesis into authoritative evidence.

The candidate is NOT evidence for itself. Do not use external/world knowledge to
fill missing grounding. Keep each atom reason short and actionable.
""".strip()


_TOOL_VERIFICATION_SYSTEM_PROMPT = """
You are a strict PRE-EXECUTION verifier for a tool-using agent.

This is NOT a user-facing answer verification step. The candidate is an
intermediate execution response that CUGA will consume in order to invoke tools.
A code-only execution response is a valid terminal output for this model step.
Do NOT require a natural-language answer before or after the tool code, and do
NOT apply user-facing response-format rules to this candidate.

The user message contains these sections:

[CALLS]
The proposed awaited tool calls in execution order. Each call has a local ID such
as C1 or C2. Safe local Python values are deterministically inlined here. Values
that depend on an earlier awaited call are shown as explicit result_of(Cn)
dependencies. Unresolved expressions remain visibly unresolved rather than being
guessed.

[ARGUMENT_PROVENANCE]
For each positional/keyword argument, records how its displayed value was
obtained:
- literal: written directly in the call;
- local_static(name): resolved from a deterministic local assignment in this
  same code block;
- prior_call_result(Cn via name): the runtime result of an earlier call in this
  same execution candidate;
- unresolved(expr): could not be resolved deterministically.
A local_static value does NOT need a separate STATE fact proving that the Python
variable exists; its existence/value was established deterministically from the
candidate code. You must still verify that the semantic CONTENT of a consequential
local_static value is grounded in STATE/policy as appropriate.

[CALLED_TOOL_SPECS]
Compact runtime descriptions/signatures for the tools named in [CALLS]. These are
deterministic facts captured from CUGA's prepared runtime.

[EVIDENCE]
Selected policy/document/tool/user/assistant statements relevant to the proposed
execution.

[RELATIONS]
Typed relations between selected evidence statements.

[RELEVANCE]
The ADHERENCE and STATE evidence available for each proposed call. Evidence may
be shared across calls because the execution candidate was retrieved as one
small action plan.

[REASONING_HISTORY]
Previously accepted intermediate reasoning steps for this internal generation
cycle. They may explain the trajectory that led to the proposed call, but they
are NOT authoritative grounding evidence for external facts, argument values,
permissions, successful outcomes, or satisfied prerequisites.

[RUNTIME]
Deterministic runtime inventory. "directly_callable" is authoritative for tools
that CUGA can execute directly in the current prepared execution context.

You MUST return exactly one atom decision for every call ID in [CALLS]. Use that
call ID as candidate_atom_id. Do not invent or rename IDs.

What to verify
--------------
For each proposed call, verify ONLY the legitimacy of executing that call now:
1. Tool identity/availability.
   - The named tool must be directly callable according to [RUNTIME], unless the
     proposed call itself is the directly-callable discovery/execution wrapper.
   - Never invent an unlock, discovery, or availability requirement that is not
     present in [RUNTIME] or explicit policy evidence.

2. Arguments/parameters.
   - Argument names and shape must be compatible with the supplied runtime tool
     signature when a signature is available.
   - Treat values marked literal or local_static as concrete values actually
     proposed by the candidate. Judge whether their semantic content is grounded;
     do not reject merely because the original call referred to a local variable.
   - A prior_call_result(Cn ...) value is an explicit same-candidate dependency.
     Do not require its runtime value to already exist in pre-candidate STATE.
   - An unresolved(...) consequential value normally lacks enough grounding unless
     supplied evidence independently establishes exactly what it denotes.
   - Do not invent IDs, account values, emails, dates, reason enums, or other
     consequential values.

3. Policy and prerequisites.
   - The proposed action must not violate applicable policy evidence.
   - Required prerequisites for THIS action must be established.
   - Distinguish a protected action from an action that establishes/checks its
     prerequisite. Do NOT require prerequisite P before action V when V is the
     permitted procedure for establishing/checking P. Example:
       V --ENABLES--> P
       protected_action --REQUIRES--> P
     The requirement on protected_action does not automatically prohibit V.

4. Multi-call execution.
   - Calls are evaluated in listed order.
   - A later call may legitimately consume the value assigned by an earlier call
     in the same candidate when [ARGUMENT_PROVENANCE] shows that dependency.
   - Do not require the earlier tool result to already exist in pre-candidate
     STATE; the tools have not executed yet.

What NOT to verify at this stage
--------------------------------
- Do NOT require evidence that the proposed tool call already succeeded.
- Do NOT treat assignment of a tool result or print(result) as a claim that the
  tool already succeeded; those are execution mechanics.
- Do NOT reject because the candidate contains only executable code.
- Do NOT require a final natural-language response in the same model message.
- Do NOT grade whether this is the most efficient next action unless policy
  explicitly makes that action impermissible.

Verdicts
--------
supported:
    The proposed call is available, its material arguments are grounded, and no
    applicable evidence prohibits executing it now.

contradicted:
    The proposed call conflicts with deterministic runtime facts or applicable
    policy/state evidence.

insufficient:
    A material argument, permission, or prerequisite required for THIS call is
    not established by the supplied evidence.

not_applicable:
    Use only if a listed item is not actually a material tool call. Normally each
    [CALLS] item should receive supported, contradicted, or insufficient.

Source precedence
-----------------
- policy governs what the agent may/must do;
- document governs domain facts/rules it states;
- tool governs already-observed environment state/tool outcomes;
- user governs user intent/consent/provided values;
- assistant history is weakest.

Do not use external/world knowledge to fill missing evidence. Keep each reason
short and actionable, ideally one sentence.
""".strip()




def _strip_reasoning_prefix(candidate: str) -> str:
    """Return reasoning content without the external REASONING: protocol marker."""
    return _REASONING_PREFIX_RE.sub("", candidate, count=1).strip()


def _reasoning_source_type() -> SourceType:
    """Use SourceType.REASONING when schemas support it, with a safe V1 fallback.

    The fallback lets this verifier file be introduced before the schema enum is
    updated in the next implementation step. Because reasoning lives in its own
    graph and is never merged into STATE, the fallback cannot promote reasoning
    to ordinary assistant grounding evidence.
    """
    return getattr(SourceType, "REASONING", SourceType.ASSISTANT_MESSAGE)


def _next_reasoning_step_id() -> str:
    return f"R{len(_VERIFICATION_STATE.reasoning_steps) + 1}"


def _reasoning_history_lines() -> list[str]:
    return [
        f"{step.step_id}: {_one_line(step.content)}"
        for step in _VERIFICATION_STATE.reasoning_steps
    ]


def _reset_reasoning_trace(*, reason: str) -> None:
    step_count = len(_VERIFICATION_STATE.reasoning_steps)
    node_count = (
        len(_VERIFICATION_STATE.reasoning_graph.nodes)
        if _VERIFICATION_STATE.reasoning_graph is not None
        else 0
    )
    _VERIFICATION_STATE.reasoning_graph = None
    _VERIFICATION_STATE.reasoning_steps = []
    if step_count or node_count:
        logger.info(
            "Prompt verifier reasoning trace reset: reason={} steps={} nodes={}",
            reason,
            step_count,
            node_count,
        )


async def _commit_reasoning_candidate_graph(
    *,
    candidate_graph: MemoryGraph,
    content: str,
    step_id: str,
) -> None:
    """Commit an already-verified candidate graph to temporary reasoning state.

    The candidate graph is merged only into ``reasoning_graph``. After insertion,
    lateral relations are inferred against earlier reasoning atoms so the
    temporary graph tracks relationships across accepted reasoning steps. No
    reasoning node is inserted into STATE or authority.
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

    edge_ids_before_linking = set(reasoning_graph.edges)
    if new_node_ids:
        await asyncio.to_thread(
            link_new_nodes,
            reasoning_graph,
            new_node_ids,
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

    logger.info(
        "Prompt verifier committed reasoning step: step_id={} nodes={} edges={} "
        "total_steps={} total_reasoning_nodes={}",
        step_id,
        len(new_node_ids),
        len(step_edge_ids),
        len(_VERIFICATION_STATE.reasoning_steps),
        len(reasoning_graph.nodes),
    )


def reset_reasoning_trace() -> None:
    """Public hook for orchestration code to abandon the current reasoning cycle."""
    _reset_reasoning_trace(reason="external_reset")


def _expr_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparseable>"


def _call_name(call: ast.Call) -> str:
    return _expr_text(call.func)


def _static_value_from_expr(
    node: ast.AST,
    bindings: dict[str, _LocalBinding],
) -> tuple[bool, Any, tuple[str, ...]]:
    """Safely evaluate a deliberately small, side-effect-free expression subset."""
    if isinstance(node, ast.Constant):
        return True, node.value, ()

    if isinstance(node, ast.Name):
        binding = bindings.get(node.id)
        if (
            binding is not None
            and binding.resolved.provenance in {"literal", "local_static"}
        ):
            return True, binding.resolved.static_value, (node.id,)
        return False, None, (node.id,)

    if isinstance(node, ast.List):
        values: list[Any] = []
        names: list[str] = []
        for item in node.elts:
            ok, value, used = _static_value_from_expr(item, bindings)
            if not ok:
                return False, None, tuple(dict.fromkeys([*names, *used]))
            values.append(value)
            names.extend(used)
        return True, values, tuple(dict.fromkeys(names))

    if isinstance(node, ast.Tuple):
        values: list[Any] = []
        names: list[str] = []
        for item in node.elts:
            ok, value, used = _static_value_from_expr(item, bindings)
            if not ok:
                return False, None, tuple(dict.fromkeys([*names, *used]))
            values.append(value)
            names.extend(used)
        return True, tuple(values), tuple(dict.fromkeys(names))

    if isinstance(node, ast.Set):
        values: list[Any] = []
        names: list[str] = []
        for item in node.elts:
            ok, value, used = _static_value_from_expr(item, bindings)
            if not ok:
                return False, None, tuple(dict.fromkeys([*names, *used]))
            values.append(value)
            names.extend(used)
        try:
            return True, set(values), tuple(dict.fromkeys(names))
        except TypeError:
            return False, None, tuple(dict.fromkeys(names))

    if isinstance(node, ast.Dict):
        result: dict[Any, Any] = {}
        names: list[str] = []
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                return False, None, tuple(dict.fromkeys(names))
            key_ok, key, key_used = _static_value_from_expr(key_node, bindings)
            value_ok, value, value_used = _static_value_from_expr(
                value_node,
                bindings,
            )
            names.extend(key_used)
            names.extend(value_used)
            if not key_ok or not value_ok:
                return False, None, tuple(dict.fromkeys(names))
            try:
                result[key] = value
            except TypeError:
                return False, None, tuple(dict.fromkeys(names))
        return True, result, tuple(dict.fromkeys(names))

    if isinstance(node, ast.UnaryOp):
        ok, value, used = _static_value_from_expr(node.operand, bindings)
        if not ok:
            return False, None, used
        try:
            if isinstance(node.op, ast.USub):
                return True, -value, used
            if isinstance(node.op, ast.UAdd):
                return True, +value, used
            if isinstance(node.op, ast.Not):
                return True, not value, used
        except Exception:
            return False, None, used
        return False, None, used

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left_ok, left, left_used = _static_value_from_expr(node.left, bindings)
        right_ok, right, right_used = _static_value_from_expr(node.right, bindings)
        used = tuple(dict.fromkeys([*left_used, *right_used]))
        if not left_ok or not right_ok:
            return False, None, used
        try:
            return True, left + right, used
        except Exception:
            return False, None, used

    if isinstance(node, ast.JoinedStr):
        pieces: list[str] = []
        names: list[str] = []
        for item in node.values:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                pieces.append(item.value)
                continue
            if not isinstance(item, ast.FormattedValue):
                return False, None, tuple(dict.fromkeys(names))
            if item.format_spec is not None:
                return False, None, tuple(dict.fromkeys(names))
            ok, value, used = _static_value_from_expr(item.value, bindings)
            names.extend(used)
            if not ok:
                return False, None, tuple(dict.fromkeys(names))
            if item.conversion == 114:  # !r
                pieces.append(repr(value))
            elif item.conversion == 97:  # !a
                pieces.append(ascii(value))
            else:
                pieces.append(str(value))
        return True, "".join(pieces), tuple(dict.fromkeys(names))

    return False, None, ()


def _dependency_call_ids_for_expr(
    node: ast.AST,
    bindings: dict[str, _LocalBinding],
) -> tuple[str, ...]:
    dependencies: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Name):
            continue
        binding = bindings.get(child.id)
        if binding is None:
            continue
        dependencies.extend(binding.resolved.dependency_call_ids)
    return tuple(dict.fromkeys(dependencies))


def _resolve_expression(
    node: ast.AST,
    bindings: dict[str, _LocalBinding],
    *,
    direct_literal: bool = False,
) -> _ResolvedExpression:
    """Resolve a candidate expression without executing arbitrary Python."""
    ok, value, used_names = _static_value_from_expr(node, bindings)
    if ok:
        provenance: Literal[
            "literal",
            "local_static",
            "prior_call_result",
            "unresolved",
        ] = "literal" if direct_literal and not used_names else "local_static"
        return _ResolvedExpression(
            rendered=repr(value),
            provenance=provenance,
            static_value=value,
            source_names=used_names,
        )

    dependencies = _dependency_call_ids_for_expr(node, bindings)
    source_names = tuple(
        dict.fromkeys(
            child.id
            for child in ast.walk(node)
            if isinstance(child, ast.Name)
        )
    )
    rendered = _expr_text(node)

    if dependencies:
        if isinstance(node, ast.Name):
            binding = bindings.get(node.id)
            if (
                binding is not None
                and binding.resolved.provenance == "prior_call_result"
            ):
                return binding.resolved
        return _ResolvedExpression(
            rendered=rendered,
            provenance="prior_call_result",
            dependency_call_ids=dependencies,
            source_names=source_names,
        )

    return _ResolvedExpression(
        rendered=rendered,
        provenance="unresolved",
        source_names=source_names,
    )


def _simple_assignment_names(statement: ast.stmt) -> list[str]:
    targets: list[ast.AST] = []
    if isinstance(statement, ast.Assign):
        targets = list(statement.targets)
    elif isinstance(statement, ast.AnnAssign):
        targets = [statement.target]

    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
    return names


def _awaited_call_from_statement(
    statement: ast.stmt,
) -> tuple[ast.Call, str | None] | None:
    value: ast.AST | None = None
    assigned_to: str | None = None

    if isinstance(statement, ast.Assign):
        value = statement.value
        assigned_to = ", ".join(_expr_text(target) for target in statement.targets)
    elif isinstance(statement, ast.AnnAssign):
        value = statement.value
        assigned_to = _expr_text(statement.target)
    elif isinstance(statement, ast.Expr):
        value = statement.value

    if isinstance(value, ast.Await) and isinstance(value.value, ast.Call):
        return value.value, assigned_to
    return None


def _extract_candidate_calls(candidate: str) -> list[dict[str, Any]]:
    """Build a small sequential execution plan for awaited tool calls.

    The parser walks top-level statements in source order, resolves safe local
    assignments, and preserves explicit dependencies on results of earlier calls.
    It never executes candidate code and never evaluates arbitrary function calls.
    """
    blocks = _PYTHON_BLOCK_RE.findall(candidate)
    if not blocks:
        return []

    extracted: list[dict[str, Any]] = []
    bindings: dict[str, _LocalBinding] = {}

    for block in blocks:
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue

        for statement in tree.body:
            awaited = _awaited_call_from_statement(statement)
            if awaited is not None:
                call, assigned_to = awaited
                call_id = f"C{len(extracted) + 1}"

                positional_args = [
                    _resolve_expression(arg, bindings, direct_literal=True)
                    for arg in call.args
                ]
                keyword_args = {
                    (kw.arg or "**"): _resolve_expression(
                        kw.value,
                        bindings,
                        direct_literal=True,
                    )
                    for kw in call.keywords
                }

                extracted.append(
                    {
                        "call_id": call_id,
                        "call": _call_name(call),
                        "positional_args": positional_args,
                        "keyword_args": keyword_args,
                        "assigned_to": assigned_to,
                    }
                )

                for name in _simple_assignment_names(statement):
                    bindings[name] = _LocalBinding(
                        resolved=_ResolvedExpression(
                            rendered=f"result_of({call_id})",
                            provenance="prior_call_result",
                            dependency_call_ids=(call_id,),
                            source_names=(name,),
                        )
                    )
                continue

            # Track only simple top-level assignments. Complex targets remain
            # unresolved rather than being approximated.
            assignment_value: ast.AST | None = None
            if isinstance(statement, ast.Assign):
                assignment_value = statement.value
            elif isinstance(statement, ast.AnnAssign):
                assignment_value = statement.value

            if assignment_value is None:
                continue

            names = _simple_assignment_names(statement)
            if not names:
                continue

            resolved = _resolve_expression(
                assignment_value,
                bindings,
                direct_literal=False,
            )
            for name in names:
                if resolved.provenance in {"literal", "local_static"}:
                    bound = _ResolvedExpression(
                        rendered=resolved.rendered,
                        provenance="local_static",
                        static_value=resolved.static_value,
                        dependency_call_ids=resolved.dependency_call_ids,
                        source_names=tuple(
                            dict.fromkeys([name, *resolved.source_names])
                        ),
                    )
                elif resolved.provenance == "prior_call_result":
                    bound = _ResolvedExpression(
                        rendered=resolved.rendered,
                        provenance="prior_call_result",
                        dependency_call_ids=resolved.dependency_call_ids,
                        source_names=tuple(
                            dict.fromkeys([name, *resolved.source_names])
                        ),
                    )
                else:
                    bound = _ResolvedExpression(
                        rendered=resolved.rendered,
                        provenance="unresolved",
                        source_names=tuple(
                            dict.fromkeys([name, *resolved.source_names])
                        ),
                    )
                bindings[name] = _LocalBinding(resolved=bound)

    return extracted


def _get_model(*, reasoning_effort: Literal["low", "medium", "high"]):
    """Get CUGA's configured model and set gpt-oss reasoning effort per call."""
    model = LLMManager().get_model(settings.agent.code.model)
    model_name = str(
        getattr(model, "model_name", "")
        or getattr(model, "model", "")
        or ""
    ).lower()

    if "gpt-oss" in model_name:
        return model.bind(reasoning_effort=reasoning_effort)
    return model


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


def _message_signature(
    message: dict[str, Any] | BaseMessage,
) -> str:
    """Return a stable signature for the raw context representation we consume."""
    payload = {
        "role": _message_role(message),
        "content": _message_text(message),
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


def _extract_state_sources(
    current_context: list[dict[str, Any] | BaseMessage],
    *,
    start_index: int = 0,
) -> list[_EvidenceSource]:
    """Extract dynamic conversation/tool evidence from a raw context slice.

    ``start_index`` is the position of the first supplied message in the original
    full context. Keeping absolute positions in source IDs makes incremental
    appends stable: a message originally at context index 8 remains
    ``context-8-*`` even when only ``current_context[8:]`` is processed.

    System messages are intentionally ignored. Authority is initialized
    separately through the Playbook and CUGA-policy initialization hooks.
    """
    state_sources: list[_EvidenceSource] = []

    for index, message in enumerate(
        current_context,
        start=start_index,
    ):
        role = _message_role(message)
        text = _message_text(message).strip()
        if not text or role == "system":
            continue

        source_prefix = f"context-{index}"
        base_metadata = {
            "message_role": role,
            "context_index": index,
        }

        if role == "tool":
            state_sources.append(
                _EvidenceSource(
                    source_id=f"{source_prefix}-tool",
                    source_type=SourceType.TOOL_RESULT,
                    content=text,
                    metadata=base_metadata,
                )
            )
            continue

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
            source_type = (
                SourceType.TOOL_RESULT
                if _looks_like_tool_result(text)
                else SourceType.USER_MESSAGE
            )
            state_sources.append(
                _EvidenceSource(
                    source_id=f"{source_prefix}-user",
                    source_type=source_type,
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


def _extract_json_object_from_text(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from model text, including fenced JSON."""
    if not isinstance(text, str):
        return None

    stripped = text.strip()
    if not stripped:
        return None

    # Fast path: the whole response is JSON.
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Common model format: ```json ... ``` or ``` ... ```.
    fence_match = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fence_match is not None:
        try:
            parsed = json.loads(fence_match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Last resort: scan for the first decodable JSON object embedded in prose.
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def _extract_json_from_message(raw_message: Any) -> dict[str, Any] | None:
    """Recover a JSON object from an AIMessage's content/metadata."""
    if raw_message is None:
        return None

    content = getattr(raw_message, "content", None)

    if isinstance(content, dict):
        return content

    if isinstance(content, str):
        parsed = _extract_json_object_from_text(content)
        if parsed is not None:
            return parsed

    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                # Some providers return structured content blocks.
                if isinstance(item.get("json"), dict):
                    return item["json"]
                for key in ("text", "content", "arguments", "input"):
                    value = item.get(key)
                    if isinstance(value, dict):
                        return value
                    if isinstance(value, str):
                        parsed = _extract_json_object_from_text(value)
                        if parsed is not None:
                            return parsed
            elif isinstance(item, str):
                parsed = _extract_json_object_from_text(item)
                if parsed is not None:
                    return parsed

    additional_kwargs = getattr(raw_message, "additional_kwargs", None) or {}

    # Older OpenAI function_call representation.
    function_call = additional_kwargs.get("function_call")
    if isinstance(function_call, dict):
        arguments = function_call.get("arguments")
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            parsed = _extract_json_object_from_text(arguments)
            if parsed is not None:
                return parsed

    # ReasoningChatOpenAI preserves reasoning_content in additional_kwargs.
    reasoning_content = additional_kwargs.get("reasoning_content")
    if isinstance(reasoning_content, str):
        parsed = _extract_json_object_from_text(reasoning_content)
        if parsed is not None:
            return parsed

    return None


def _extract_structured_args(
    raw_message: Any,
) -> dict[str, Any] | None:
    """Recover structured-output arguments from a raw LangChain AIMessage."""

    # Preferred LangChain representation.
    tool_calls = getattr(raw_message, "tool_calls", None)

    if tool_calls:
        first_call = tool_calls[0]

        if isinstance(first_call, dict):
            args = first_call.get("args")
        else:
            args = getattr(first_call, "args", None)

        if isinstance(args, dict):
            return args

        if isinstance(args, str):
            parsed = _extract_json_object_from_text(args)
            if parsed is not None:
                return parsed

    # OpenAI-compatible raw tool-call representation.
    additional_kwargs = getattr(raw_message, "additional_kwargs", None) or {}
    raw_tool_calls = additional_kwargs.get("tool_calls", [])

    if raw_tool_calls:
        first_call = raw_tool_calls[0]
        if isinstance(first_call, dict):
            function = first_call.get("function", {})
            if isinstance(function, dict):
                arguments = function.get("arguments")
                if isinstance(arguments, dict):
                    return arguments
                if isinstance(arguments, str):
                    parsed = _extract_json_object_from_text(arguments)
                    if parsed is not None:
                        return parsed

    # Some gpt-oss/OpenAI-compatible paths answer with ordinary JSON content
    # rather than emitting the requested structured tool call.
    return _extract_json_from_message(raw_message)


async def _plain_json_retry(
    *,
    model: Any,
    messages: list[BaseMessage],
    schema: type[BaseModel],
    label: str,
) -> dict[str, Any] | None:
    """Retry once without function calling and request a JSON object only."""
    schema_json = json.dumps(
        schema.model_json_schema(),
        ensure_ascii=False,
    )

    retry_messages = [
        *messages,
        HumanMessage(
            content=(
                "The previous structured-output attempt did not produce a "
                "recoverable function call. Return ONLY one JSON object that "
                "matches the following JSON schema exactly. Do not use markdown, "
                "code fences, commentary, or any text outside the JSON object.\n\n"
                f"JSON_SCHEMA:\n{schema_json}"
            )
        ),
    ]

    logger.warning(
        "Structured output missing for {}. Retrying once as plain JSON.",
        label,
    )

    response = await model.ainvoke(retry_messages)
    return _extract_json_from_message(response)


async def _build_source_results(
    sources: list[_EvidenceSource],
    *,
    session_id: str,
    semaphore: asyncio.Semaphore,
) -> list[GraphBuildResult]:
    """Build hierarchical graph results for the supplied evidence sources."""
    if not sources:
        return []

    async def build_one(source: _EvidenceSource) -> GraphBuildResult:
        request = GraphBuildRequest(
            session_id=session_id,
            source_id=source.source_id,
            source_type=source.source_type,
            content=source.content,
            metadata=source.metadata,
        )

        builder = GraphBuilder()

        # GraphBuilder's model wrapper is synchronous. Keep each recursive build
        # off the verifier event loop and limit simultaneous source builds.
        async with semaphore:
            return await asyncio.to_thread(builder.build, request)

    return list(
        await asyncio.gather(
            *(build_one(source) for source in sources)
        )
    )


async def _apply_build_results(
    graph: MemoryGraph,
    build_results: list[GraphBuildResult],
    *,
    link_relations: bool = True,
) -> set[str]:
    """Apply new hierarchies, then infer lateral relations involving new nodes."""
    new_node_ids: set[str] = set()

    for build_result in build_results:
        new_node_ids.update(
            graph.apply_build_result(build_result)
        )

    if new_node_ids and link_relations:
        # Relation linking may compare new atoms with atoms already present in
        # ``graph``. It never reconsiders old-to-old pairs.
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
    """Build a new memory graph from evidence sources."""
    graph = MemoryGraph()

    build_results = await _build_source_results(
        sources,
        session_id=session_id,
        semaphore=semaphore,
    )
    await _apply_build_results(
        graph,
        build_results,
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
    """Append only new evidence sources to an existing memory graph."""
    build_results = await _build_source_results(
        sources,
        session_id=session_id,
        semaphore=semaphore,
    )
    return await _apply_build_results(
        graph,
        build_results,
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
) -> tuple[MemoryGraph, str, int]:
    """Bring the persistent state graph up to date with ``current_context``.

    Returns:
        ``(graph, update_mode, appended_source_count)`` where update_mode is one
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

        _VERIFICATION_STATE.state_graph = graph
        _VERIFICATION_STATE.state_context_cursor = len(current_context)
        _VERIFICATION_STATE.state_context_signatures = current_signatures

        return graph, "initial_build", len(sources)

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

        _VERIFICATION_STATE.state_graph = graph
        _VERIFICATION_STATE.state_context_cursor = len(current_context)
        _VERIFICATION_STATE.state_context_signatures = current_signatures

        return graph, "fallback_rebuild", len(sources)

    graph = _VERIFICATION_STATE.state_graph
    cursor = _VERIFICATION_STATE.state_context_cursor

    if cursor == len(current_context):
        return graph, "reuse", 0

    new_messages = current_context[cursor:]
    new_sources = _extract_state_sources(
        new_messages,
        start_index=cursor,
    )

    # Even if the raw delta contains only ignored system/empty messages, advance
    # the raw cursor after confirming the prefix was unchanged.
    if new_sources:
        await _append_sources_to_graph(
            graph,
            new_sources,
            session_id=session_id,
            semaphore=semaphore,
        )
        update_mode = "append"
    else:
        update_mode = "cursor_advance"

    _VERIFICATION_STATE.state_context_cursor = len(current_context)
    _VERIFICATION_STATE.state_context_signatures = current_signatures

    return graph, update_mode, len(new_sources)


def _playbook_graph_cache_prompt(
    playbooks: list[AuthoritySource],
) -> str:
    """Return a deterministic cache identity based only on Playbook text.

    Runtime source IDs and metadata are intentionally excluded because they may
    change between otherwise identical Tau attempts/runs. The ordered list
    representation preserves boundaries between multiple Playbooks while making
    identical textual Playbook inputs reuse the same cached graph.
    """
    contents = [
        playbook.content.strip()
        for playbook in playbooks
        if playbook.content.strip()
    ]

    return json.dumps(
        contents,
        ensure_ascii=False,
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

    prompt_hash = compute_prompt_hash(content)
    load_start = time.perf_counter()
    cached_graph = load_graph_for_prompt(
        content,
        graph_type="cuga_policy",
        memory_graphs_file=_GRAPH_CACHE_INDEX,
    )

    if cached_graph is not None:
        logger.info(
            "Prompt verifier CUGA-policy graph cache HIT: hash={} "
            "nodes={} edges={} load_time={:.3f}s",
            prompt_hash,
            len(cached_graph.nodes),
            len(cached_graph.edges),
            time.perf_counter() - load_start,
        )
        return cached_graph

    logger.info(
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
        raw_prompt=content,
        graph_type="cuga_policy",
        memory_graphs_file=_GRAPH_CACHE_INDEX,
    )

    logger.info(
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
        logger.info(
            "Prompt verifier Playbook graph cache HIT: hash={} playbooks={} "
            "nodes={} edges={} load_time={:.3f}s",
            prompt_hash,
            len(active_playbooks),
            len(cached_graph.nodes),
            len(cached_graph.edges),
            time.perf_counter() - load_start,
        )
        return cached_graph

    logger.info(
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

    logger.info(
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
        logger.info(
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

    _VERIFICATION_STATE.state_graph = None
    _VERIFICATION_STATE.state_context_cursor = 0
    _VERIFICATION_STATE.state_context_signatures = []
    _VERIFICATION_STATE.reasoning_graph = None
    _VERIFICATION_STATE.reasoning_steps = []

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

    logger.info(
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

    logger.info(
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
) -> None:
    """Snapshot deterministic runtime tooling facts for the verifier.

    This is intentionally a verifier-owned normalization seam. CugaLite passes
    the already-prepared runtime objects/names; it does not interpret them or
    construct verifier-specific policy facts.

    ``prompt_tools`` describes tools explicitly exposed to the model.
    ``execution_tool_names`` describes callable names actually present in the
    execution context. These sets may differ when find_tools shortlisting is
    active.
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

    logger.info(
        "Prompt verifier runtime initialized: session={} prompt_tools={} "
        "execution_tools={} find_tools_enabled={}",
        session_id,
        prompt_tool_names,
        normalized_execution_names,
        bool(find_tools_enabled),
    )


def _node_source_type(graph: MemoryGraph, node_id: str) -> SourceType | None:
    node = graph.nodes[node_id]

    if node.source_refs:
        return node.source_refs[0].source_type

    root = graph.nodes.get(node.source_root_id)
    if root and root.source_refs:
        return root.source_refs[0].source_type

    return None


def _one_line(text: str) -> str:
    """Normalize a statement for the line-oriented verifier projection."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


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
                (_node_source_type(graph, node_id).value
                 if _node_source_type(graph, node_id) is not None
                 else "unknown"),
                _one_line(graph.nodes[node_id].content),
                node_id,
            ),
        )

        for node_id in ordered:
            source_type = _node_source_type(graph, node_id)
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
            source_type = _node_source_type(graph, node_id)
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
        if len(value.dependency_call_ids) == 1 and value.rendered.startswith("result_of("):
            return value.rendered
        return f"{value.rendered} <depends_on:{deps}>"

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

    if value.provenance == "prior_call_result":
        deps = ",".join(value.dependency_call_ids) or "?"
        names = [name for name in value.source_names if name]
        if names:
            return f"prior_call_result({deps} via {names[0]})"
        return f"prior_call_result({deps})"

    return f"unresolved({value.rendered})"


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

def _combine_evidence_graphs(
    *graphs: MemoryGraph,
) -> MemoryGraph:
    """Create a temporary disjoint-union view without changing provenance."""
    combined = MemoryGraph()

    for graph in graphs:
        for node in graph.nodes.values():
            if node.id in combined.nodes:
                raise PromptVerificationError(
                    "Cannot combine evidence graphs with duplicate node ID "
                    f"{node.id}"
                )
            combined.add_node(node)

    for graph in graphs:
        for edge in graph.edges.values():
            if edge.id in combined.edges:
                raise PromptVerificationError(
                    "Cannot combine evidence graphs with duplicate edge ID "
                    f"{edge.id}"
                )
            combined.add_edge(edge)

    return combined


def _ranked_anchor_payload(
    ranked: list[RankedNode],
) -> list[dict[str, Any]]:
    return [
        {
            "node_id": item.node.id,
            "lexical_score": item.lexical_score,
            "embedding_score": item.embedding_score,
            "combined_score": item.combined_score,
            "used_embedding": item.used_embedding,
            "lexical_components": item.lexical_components,
        }
        for item in ranked
    ]


def _select_evidence_for_atom(
    *,
    candidate_atom: Any,
    evidence_graph: MemoryGraph,
    evidence_space: str,
) -> dict[str, Any]:
    """Retrieve and traverse evidence for one candidate atom.

    Retrieval scores, coverage decisions, traversal steps, and depth diagnostics
    are logged for observability but are intentionally NOT returned to the final
    verifier prompt. The caller receives only the selected evidence IDs plus the
    truncation flag, allowing the final payload to serialize each evidence node
    and relation exactly once.
    """
    ranked = rank_nodes(
        candidate_atom,
        evidence_graph.atomic_nodes(active_only=True),
        top_k=_VERIFIER_TOP_K,
    )

    ranked_payload = _ranked_anchor_payload(ranked)

    logger.info(
        "Prompt verifier retrieval: candidate_atom_id={} evidence_space={} "
        "top_k={}",
        candidate_atom.id,
        evidence_space,
        json.dumps(ranked_payload, ensure_ascii=False),
    )

    if not ranked:
        return {
            "node_ids": [],
            "edge_ids": [],
            "truncated": False,
        }

    traversal = build_coverage_aware_mini_graphs(
        evidence_graph,
        [item.node.id for item in ranked],
        config=_VERIFIER_TRAVERSAL_CONFIG,
    )

    selected_node_ids = sorted(traversal.all_node_ids)
    selected_edge_ids = sorted(traversal.all_edge_ids)

    mini_graph_payload = [
        {
            "anchor_id": mini_graph.anchor_id,
            "node_ids": list(mini_graph.node_ids),
            "edge_ids": list(mini_graph.edge_ids),
            "node_depths": [
                {
                    "node_id": node_id,
                    "best_bounded_depth": depth,
                }
                for node_id, depth in getattr(
                    mini_graph,
                    "node_depths",
                    (),
                )
            ],
            "truncated": mini_graph.truncated,
            "steps": [
                {
                    "from_node_id": step.from_node_id,
                    "to_node_id": step.to_node_id,
                    "edge_id": step.edge_id,
                    "relation": step.relation.value,
                    "bounded_depth_before": step.bounded_depth_before,
                    "bounded_depth_after": step.bounded_depth_after,
                    "reset_applied": step.reset_applied,
                    "closure_applied": step.closure_applied,
                }
                for step in mini_graph.steps
            ],
        }
        for mini_graph in traversal.mini_graphs
    ]

    logger.info(
        "Prompt verifier traversal: candidate_atom_id={} evidence_space={} "
        "selected_nodes={} selected_edges={} skipped_anchors={} truncated={} "
        "mini_graphs={}",
        candidate_atom.id,
        evidence_space,
        selected_node_ids,
        selected_edge_ids,
        list(traversal.skipped_anchor_ids),
        traversal.truncated,
        json.dumps(mini_graph_payload, ensure_ascii=False),
    )

    return {
        "node_ids": selected_node_ids,
        "edge_ids": selected_edge_ids,
        "truncated": traversal.truncated,
    }


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


async def _verify_with_graphs(
    *,
    candidate: str,
    candidate_kind: CandidateKind,
    candidate_graph: MemoryGraph,
    state_graph: MemoryGraph,
    cuga_policy_graph: MemoryGraph,
    playbook_graph: MemoryGraph,
    runtime_facts: dict[str, Any] | None = None,
) -> VerificationDecision:
    candidate_atoms = sorted(
        candidate_graph.atomic_nodes(active_only=True),
        key=lambda node: (node.depth, node.content, node.id),
    )

    if not candidate_atoms:
        raise PromptVerificationError(
            "Candidate decomposition produced no atomic propositions"
        )

    candidate_calls = _extract_candidate_calls(candidate)
    is_tool_execution = candidate_kind == "tool_execution"

    if is_tool_execution and not candidate_calls:
        raise PromptVerificationError(
            "candidate_kind='tool_execution' but no awaited tool calls were found"
        )
    if candidate_kind == "reasoning" and candidate_calls:
        raise PromptVerificationError(
            "A reasoning candidate cannot also contain executable awaited tool calls"
        )

    reasoning_history_lines = _reasoning_history_lines()

    adherence_graph = _combine_evidence_graphs(
        cuga_policy_graph,
        playbook_graph,
    )

    evidence_by_candidate_atom: list[dict[str, Any]] = []
    adherence_node_ids: set[str] = set()
    adherence_edge_ids: set[str] = set()
    state_node_ids: set[str] = set()
    state_edge_ids: set[str] = set()

    for candidate_atom in candidate_atoms:
        adherence_evidence = _select_evidence_for_atom(
            candidate_atom=candidate_atom,
            evidence_graph=adherence_graph,
            evidence_space="adherence",
        )
        state_evidence = _select_evidence_for_atom(
            candidate_atom=candidate_atom,
            evidence_graph=state_graph,
            evidence_space="state",
        )

        atom_adherence_node_ids = list(adherence_evidence["node_ids"])
        atom_adherence_edge_ids = list(adherence_evidence["edge_ids"])
        atom_state_node_ids = list(state_evidence["node_ids"])
        atom_state_edge_ids = list(state_evidence["edge_ids"])

        adherence_node_ids.update(atom_adherence_node_ids)
        adherence_edge_ids.update(atom_adherence_edge_ids)
        state_node_ids.update(atom_state_node_ids)
        state_edge_ids.update(atom_state_edge_ids)

        evidence_by_candidate_atom.append(
            {
                "candidate_atom_real_id": candidate_atom.id,
                "adherence_node_ids": atom_adherence_node_ids,
                "state_node_ids": atom_state_node_ids,
                "adherence_truncated": bool(
                    adherence_evidence["truncated"]
                ),
                "state_truncated": bool(state_evidence["truncated"]),
            }
        )

    local_evidence_ids = _build_local_evidence_ids(
        adherence_graph=adherence_graph,
        adherence_node_ids=adherence_node_ids,
        state_graph=state_graph,
        state_node_ids=state_node_ids,
    )

    evidence_lines = _render_evidence_lines(
        adherence_graph=adherence_graph,
        adherence_node_ids=adherence_node_ids,
        state_graph=state_graph,
        state_node_ids=state_node_ids,
        local_ids=local_evidence_ids,
    )

    relation_lines = _render_relation_lines(
        adherence_graph=adherence_graph,
        adherence_edge_ids=adherence_edge_ids,
        state_graph=state_graph,
        state_edge_ids=state_edge_ids,
        local_ids=local_evidence_ids,
    )

    directly_callable, prompt_visible, find_tools_enabled = (
        _runtime_tool_signatures(runtime_facts)
    )

    runtime_lines = [
        "directly_callable: "
        + (", ".join(directly_callable) if directly_callable else "(none)")
    ]
    if prompt_visible == directly_callable:
        runtime_lines.append("prompt_visible: same_as_directly_callable")
    else:
        runtime_lines.append(
            "prompt_visible: "
            + (", ".join(prompt_visible) if prompt_visible else "(none)")
        )
    runtime_lines.append(
        f"find_tools_enabled: {str(find_tools_enabled).lower()}"
    )

    if is_tool_execution:
        # Tool execution is verified at CALL level, not at decomposed prose-atom
        # level. The candidate graph is used only as a retrieval query so policy
        # and state evidence can still be selected semantically.
        call_ids = [
            str(call.get("call_id") or f"C{index}")
            for index, call in enumerate(candidate_calls, start=1)
        ]
        call_lines = [
            f"{call_id}: {_render_candidate_call(call)}"
            for call_id, call in zip(call_ids, candidate_calls)
        ]

        all_adherence_local_ids = sorted(
            [
                local_evidence_ids[("adherence", node_id)]
                for node_id in adherence_node_ids
            ],
            key=_local_id_sort_key,
        )
        all_state_local_ids = sorted(
            [
                local_evidence_ids[("state", node_id)]
                for node_id in state_node_ids
            ],
            key=_local_id_sort_key,
        )

        any_adherence_truncated = any(
            item["adherence_truncated"]
            for item in evidence_by_candidate_atom
        )
        any_state_truncated = any(
            item["state_truncated"]
            for item in evidence_by_candidate_atom
        )

        relevance_lines: list[str] = []
        for call_id in call_ids:
            line = (
                f"{call_id}: "
                f"adherence={','.join(all_adherence_local_ids) or '-'}; "
                f"state={','.join(all_state_local_ids) or '-'}"
            )
            markers: list[str] = []
            if any_adherence_truncated:
                markers.append("adherence_truncated")
            if any_state_truncated:
                markers.append("state_truncated")
            if markers:
                line += "; " + ",".join(markers)
            relevance_lines.append(line)

        called_tool_spec_lines = _called_tool_spec_lines(
            candidate_calls,
            runtime_facts,
        )
        argument_provenance_lines = _argument_provenance_lines(
            candidate_calls
        )

        verifier_text = "\n".join(
            [
                "[CANDIDATE_KIND]",
                "tool_execution",
                "",
                "[CALLS]",
                *call_lines,
                "",
                "[ARGUMENT_PROVENANCE]",
                *(argument_provenance_lines or ["(none)"]),
                "",
                "[CALLED_TOOL_SPECS]",
                *(called_tool_spec_lines or ["(none)"]),
                "",
                "[EVIDENCE]",
                *(evidence_lines or ["(none)"]),
                "",
                "[RELATIONS]",
                *(relation_lines or ["(none)"]),
                "",
                "[RELEVANCE]",
                *(relevance_lines or ["(none)"]),
                "",
                "[REASONING_HISTORY]",
                *(reasoning_history_lines or ["(none)"]),
                "",
                "[RUNTIME]",
                *runtime_lines,
            ]
        )

        logger.info(
            "Prompt verifier execution plan: calls={} argument_provenance={}",
            call_lines,
            argument_provenance_lines,
        )

        system_prompt = _TOOL_VERIFICATION_SYSTEM_PROMPT
        expected_local_ids = call_ids
        restore_to_graph_ids = False
        candidate_count_for_log = len(candidate_calls)
    else:
        candidate_local_by_real = {
            node.id: f"C{index}"
            for index, node in enumerate(candidate_atoms, start=1)
        }
        candidate_real_by_local = {
            local_id: real_id
            for real_id, local_id in candidate_local_by_real.items()
        }

        candidate_lines = [
            f"{candidate_local_by_real[node.id]}: {_one_line(node.content)}"
            for node in candidate_atoms
        ]

        relevance_lines = []
        for item in evidence_by_candidate_atom:
            candidate_local_id = candidate_local_by_real[
                item["candidate_atom_real_id"]
            ]

            adherence_local_ids = sorted(
                [
                    local_evidence_ids[("adherence", node_id)]
                    for node_id in item["adherence_node_ids"]
                ],
                key=_local_id_sort_key,
            )
            state_local_ids = sorted(
                [
                    local_evidence_ids[("state", node_id)]
                    for node_id in item["state_node_ids"]
                ],
                key=_local_id_sort_key,
            )

            line = (
                f"{candidate_local_id}: "
                f"adherence={','.join(adherence_local_ids) or '-'}; "
                f"state={','.join(state_local_ids) or '-'}"
            )

            markers: list[str] = []
            if item["adherence_truncated"]:
                markers.append("adherence_truncated")
            if item["state_truncated"]:
                markers.append("state_truncated")
            if markers:
                line += "; " + ",".join(markers)

            relevance_lines.append(line)

        verifier_text = "\n".join(
            [
                "[CANDIDATE_KIND]",
                candidate_kind,
                "",
                "[RAW_CANDIDATE]",
                candidate.strip(),
                "",
                "[CANDIDATE_ATOMS]",
                *(candidate_lines or ["(none)"]),
                "",
                "[EVIDENCE]",
                *(evidence_lines or ["(none)"]),
                "",
                "[RELATIONS]",
                *(relation_lines or ["(none)"]),
                "",
                "[RELEVANCE]",
                *(relevance_lines or ["(none)"]),
                "",
                "[REASONING_HISTORY]",
                *(reasoning_history_lines or ["(none)"]),
                "",
                "[RUNTIME]",
                *runtime_lines,
            ]
        )

        system_prompt = (
            _REASONING_VERIFICATION_SYSTEM_PROMPT
            if candidate_kind == "reasoning"
            else _VERIFICATION_SYSTEM_PROMPT
        )
        expected_local_ids = list(candidate_real_by_local)
        restore_to_graph_ids = True
        candidate_count_for_log = len(candidate_atoms)

    logger.info(
        "Prompt verifier minimal projection: candidate_kind={} candidates={} "
        "adherence_nodes={} adherence_relations={} state_nodes={} "
        "state_relations={} projection_chars={}",
        candidate_kind,
        candidate_count_for_log,
        len(adherence_node_ids),
        len(adherence_edge_ids),
        len(state_node_ids),
        len(state_edge_ids),
        len(verifier_text),
    )

    model = _get_model(reasoning_effort="medium")
    verification_messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=verifier_text),
    ]

    structured_model = model.with_structured_output(
        VerificationDecision,
        method="function_calling",
        include_raw=True,
    )
    result = await structured_model.ainvoke(verification_messages)

    parsed = result.get("parsed")
    if parsed is not None:
        local_decision = (
            parsed
            if isinstance(parsed, VerificationDecision)
            else VerificationDecision.model_validate(parsed)
        )
        _validate_atom_decisions(local_decision, expected_local_ids)

        if restore_to_graph_ids:
            decision = _restore_candidate_atom_ids(
                local_decision,
                local_to_real=candidate_real_by_local,
            )
            _validate_atom_decisions(
                decision,
                [node.id for node in candidate_atoms],
            )
            return decision

        return local_decision

    raw_message = result.get("raw")
    parsing_error = result.get("parsing_error")
    raw_args = _extract_structured_args(raw_message)

    if raw_args is None:
        raw_args = await _plain_json_retry(
            model=model,
            messages=verification_messages,
            schema=VerificationDecision,
            label=(
                "per-call tool verification decision"
                if is_tool_execution
                else "per-atom verification decision"
            ),
        )

    if raw_args is None:
        raise PromptVerificationError(
            "Could not recover VerificationDecision JSON. "
            f"Original parsing error: {parsing_error}"
        )

    local_decision = VerificationDecision.model_validate(raw_args)
    _validate_atom_decisions(local_decision, expected_local_ids)

    if restore_to_graph_ids:
        decision = _restore_candidate_atom_ids(
            local_decision,
            local_to_real=candidate_real_by_local,
        )
        _validate_atom_decisions(
            decision,
            [node.id for node in candidate_atoms],
        )
        return decision

    return local_decision


async def verify_candidate(
    current_context: list[dict[str, Any] | BaseMessage],
    candidate: str,
    *,
    candidate_kind: CandidateKind | None = None,
) -> VerificationResult:
    """Verify one reasoning/terminal/tool candidate against current evidence.

    ``current_context`` must contain only CUGA's committed/base model history. The
    temporary reasoning trajectory is owned separately by this module and must not
    be appended to ``current_context``; otherwise reasoning could leak into the
    persistent STATE graph and become self-grounding evidence.

    Candidate-kind behavior:
    - ``reasoning``: verify one intermediate reasoning step. If accepted, its
      already-built candidate graph is committed to the temporary reasoning graph.
    - ``terminal``: verify a user-facing response. Acceptance does not clear the
      temporary reasoning trace here because orchestration still decides whether
      the response is truly terminal or should be auto-continued.
    - ``tool_execution``: verify a pre-execution tool plan. Acceptance keeps the
      temporary reasoning trace alive so reasoning can continue after the tool
      observation is incorporated into normal CUGA STATE.
    - ``None``: backwards-compatible inference; awaited calls imply
      ``tool_execution``, otherwise ``terminal``.

    Rejected candidates never enter the reasoning graph. Previously accepted
    reasoning steps are retained across rejections so orchestration code can pass
    only the latest rejected candidate + verifier feedback on the next attempt.
    """
    raw_candidate = candidate or ""
    if not raw_candidate.strip():
        return VerificationResult(
            valid=False,
            reason="The proposed output is empty.",
        )

    total_start = time.perf_counter()

    try:
        candidate_calls = _extract_candidate_calls(raw_candidate)

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

        if (
            not _VERIFICATION_STATE.playbook_initialized
            or _VERIFICATION_STATE.playbook_graph is None
        ):
            missing_authority.append("playbook_graph")

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
                + ". The SDK must initialize Playbooks and the CugaLite prompt "
                "path must initialize the CUGA-policy graph."
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
            # Query evidence with semantic tool actions rather than Python
            # mechanics such as assignment and printing.
            candidate_graph_content = "\n".join(
                f"Proposed tool execution: {_render_candidate_call(call)}"
                for call in candidate_calls
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
                **(
                    {"reasoning_step_id": prospective_reasoning_step_id}
                    if prospective_reasoning_step_id is not None
                    else {}
                ),
            },
        )

        graph_start = time.perf_counter()

        state_task = asyncio.create_task(
            _update_state_graph(
                current_context,
                session_id=session_id,
                semaphore=semaphore,
            )
        )
        candidate_task = asyncio.create_task(
            _build_graph(
                [candidate_source],
                session_id=session_id,
                semaphore=semaphore,
                link_relations=False,
            )
        )

        state_update, candidate_graph = await asyncio.gather(
            state_task,
            candidate_task,
        )

        state_graph, state_update_mode, state_new_sources = state_update
        graph_time = time.perf_counter() - graph_start

        logger.info(
            "Prompt verifier graph preparation complete: candidate_kind={} "
            "cuga_policy_nodes={} playbook_nodes={} state_update_mode={} "
            "state_new_sources={} state_cursor={} state_nodes={} "
            "reasoning_steps={} reasoning_nodes={} candidate_nodes={} "
            "wall_time={:.3f}s",
            resolved_candidate_kind,
            len(cuga_policy_graph.nodes),
            len(playbook_graph.nodes),
            state_update_mode,
            state_new_sources,
            _VERIFICATION_STATE.state_context_cursor,
            len(state_graph.nodes),
            len(_VERIFICATION_STATE.reasoning_steps),
            (
                len(_VERIFICATION_STATE.reasoning_graph.nodes)
                if _VERIFICATION_STATE.reasoning_graph is not None
                else 0
            ),
            len(candidate_graph.nodes),
            graph_time,
        )

        verification_start = time.perf_counter()
        decision = await _verify_with_graphs(
            candidate=candidate_content,
            candidate_kind=resolved_candidate_kind,
            candidate_graph=candidate_graph,
            state_graph=state_graph,
            cuga_policy_graph=cuga_policy_graph,
            playbook_graph=playbook_graph,
            runtime_facts=_VERIFICATION_STATE.runtime_facts,
        )
        verification_time = time.perf_counter() - verification_start

        valid, reason, global_verdict = _aggregate_atom_decisions(decision)

        if valid and resolved_candidate_kind == "reasoning":
            assert prospective_reasoning_step_id is not None
            await _commit_reasoning_candidate_graph(
                candidate_graph=candidate_graph,
                content=candidate_content,
                step_id=prospective_reasoning_step_id,
            )
            logger.info(
                "Prompt verifier accepted reasoning step: retained as temporary "
                "trajectory context only; not added to grounding STATE"
            )
        elif valid:
            # Do NOT clear the reasoning trace here.
            #
            # An accepted tool execution is an intermediate action in the larger
            # reasoning trajectory: after CUGA executes it, the resulting tool
            # observation becomes authoritative STATE and the model may continue
            # reasoning over that observation.
            #
            # Likewise, an accepted natural-language candidate is not necessarily
            # the end of the graph because shared_nodes.py may classify it as
            # non-terminal and auto-continue. The orchestration layer therefore
            # owns reasoning-trajectory lifetime and explicitly resets it only
            # when the graph truly ends or the trajectory aborts.
            logger.info(
                "Prompt verifier accepted {} candidate: keeping reasoning trace "
                "alive; trajectory lifecycle is owned by the orchestrator",
                resolved_candidate_kind,
            )
        else:
            logger.info(
                "Prompt verifier rejected {} candidate: keeping prior accepted "
                "reasoning trajectory and grounding state for regeneration",
                resolved_candidate_kind,
            )

        logger.info(
            "Prompt verifier decision: candidate_kind={} verdict={} "
            "atom_verdicts={} verification_time={:.3f}s total_time={:.3f}s "
            "reason={!r}",
            resolved_candidate_kind,
            global_verdict,
            [
                {
                    "candidate_atom_id": item.candidate_atom_id,
                    "verdict": item.verdict,
                    "reason": item.reason,
                }
                for item in decision.atoms
            ],
            verification_time,
            time.perf_counter() - total_start,
            reason,
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