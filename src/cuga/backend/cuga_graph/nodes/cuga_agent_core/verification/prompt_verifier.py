from __future__ import annotations

import ast
import asyncio
import builtins
import copy
import datetime as datetime_module
import hashlib
import json
import os
import re
import time
import typing as typing_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cuga.backend.memory_graph import (
    GraphBuildRequest,
    GraphBuildResult,
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
    RankedNode,
    build_retrieval_text,
    rank_nodes,
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
from cuga.backend.memory_graph.traversal import (
    TraversalConfig,
    build_coverage_aware_mini_graphs,
)
from cuga.backend.memory_graph.local_community_ppr import (
    LocalCommunityConfig,
    LocalCommunityDetector,
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


class CandidateContextDecision(BaseModel):
    """Final raw-candidate decision against reconstructed source context."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["approved", "rejected"]
    reason: str = ""
    violated_context_ids: list[str] = Field(default_factory=list)


@dataclass
class _CandidateQueryContextEntry:
    graph_name: str
    source_type: str
    statement_node_id: str
    statement: str
    covered_atomic_node_ids: set[str] = field(default_factory=set)
    triggered_by_candidate_atom_ids: set[str] = field(default_factory=set)
    context_id: str = ""


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
        "runtime_variable",
        "prior_call_result",
        "derived_from_prior_call_result",
        "unresolved",
    ]
    static_value: Any | None = None
    dependency_call_ids: tuple[str, ...] = ()
    source_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class _LocalBinding:
    resolved: _ResolvedExpression


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

    ``execution_graph`` is a separate append-only factual audit graph. Each node
    is one already-completed tool invocation containing the tool name, exact
    runtime parameters, and observed output in the same atomic statement. These
    nodes are not decomposed and have no edges; they participate only in normal
    candidate-conditioned retrieval.

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
    reasoning_graph: MemoryGraph | None = None
    reasoning_steps: list[_ReasoningStepRecord] = field(default_factory=list)
    committed_reasoning_graph: MemoryGraph | None = None


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
# Version verifier authority-graph cache identities whenever reconstruction
# depends on new construction-time metadata. This avoids globally invalidating
# unrelated serialized MemoryGraphs.
_AUTHORITY_GRAPH_CACHE_SCHEMA = "semantic_context_dependency_closure_v1"

# Retrieve independently from the two evidence spaces. The CUGA-policy and
# Playbook graphs are combined into one logical ADHERENCE graph; STATE remains
# separate so normative and factual evidence cannot silently substitute for one
# another.
_VERIFIER_TOP_K = 5
_VERIFIER_TRAVERSAL_CONFIG = TraversalConfig(
    max_nodes_per_mini_graph=50,
)
_VERIFIER_LOCAL_COMMUNITY_CONFIG = LocalCommunityConfig(
    restart_probability=0.15,
    max_accepted_conductance=0.45,
    max_sweep_volume_fraction=0.5,
)



_SOURCE_CONTEXT_VERIFICATION_SYSTEM_PROMPT = """
You are a strict verifier for a tool-using agent.

The candidate decomposition was used ONLY as a retrieval aid. It is deliberately
NOT shown to you because decomposition can lose condition, modality, reference,
or temporal scope. Judge the untouched [RAW_CANDIDATE] as the authoritative
meaning of what the agent proposes to say/do.

The user message contains:

[CANDIDATE_KIND]
One of terminal, reasoning, or tool_execution.

[RAW_CANDIDATE]
The exact original candidate. Preserve its conditions, hypotheticals, future
tense, requests, prerequisites, negation, and temporal language. For example,
"Once I have X, I can do Y" does NOT assert that X is currently true and does
NOT assert that Y has already happened.

[CANDIDATE_QUERY_CONTEXT]
A deduplicated list of reconstructed source-level statements. Each item has a
stable ID Q1, Q2, ... and an origin tag. Normal evidence statements are retrieved
from four already-built graphs: CUGA policy, Playbook, verified conversational
STATE, and completed tool-execution history. When present, one additional
verifier_rejection statement is appended temporarily from the immediately prior
verifier rejection in the current correction chain; it is not graph evidence.
Candidate atomic fragments are not evidence and must never be reconstructed or
treated as independent claims. Retrieved evidence atoms are likewise only retrieval
anchors: each normal Q statement is reconstructed to at least the complete original
source sentence containing its selected atomic evidence, and may expand further when
semantic dependency closure requires broader governing context.

For context origins:
- cuga_policy: binding behavioral/runtime instructions for the agent.
- playbook: binding domain/playbook authority.
- execution: deterministic completed tool-execution evidence. Each execution
  statement keeps the exact tool identity, exact runtime parameters, and observed
  output together in one indivisible fact. The fact that a tool was invoked does
  not by itself mean its intended action succeeded; determine success or failure
  from the output contained in that same execution statement. Do not combine
  parameters from one execution statement with the output of another.
- user: information/intent actually supplied or claimed by the user.
- assistant: previously approved conversational state; useful for continuity, but
  do not let it override stronger policy/execution/user evidence.
- reasoning: previously committed verified reasoning state, when present. It may
  support continuity but is not user-provided evidence and cannot establish tool
  outcomes by itself.
- verifier_rejection: temporary correction-history context containing only the
  immediately preceding candidate rejected by the verifier and that rejection's
  result. It is not independent policy, user, execution, or domain evidence and
  must not be treated as authoritative grounding. Use it only to maintain
  consistency across consecutive correction attempts and to recognize the issue
  that the replacement candidate is intended to resolve.

Completed tool observations live only in the execution graph; they are not copied
into conversational STATE. Provenance is semantically binding. When an applicable
requirement depends on the USER providing, knowing, confirming, or correctly giving
information, a value originating only from execution or another internal source
does NOT satisfy that requirement and must never be attributed to the user. When
the requirement is to verify a user-provided value against trusted internal data,
count it as satisfied only when the relevant value is present in a user statement
and matches the corresponding trusted execution output. A trusted internal value
by itself proves only what the database/tool contains, not that the user knew or
supplied it. A mismatch does not count and must not be repaired by substituting the
internal value for the user's claim.

[EXECUTION_DETAILS]
Present only when useful for tool_execution. It contains deterministic call,
argument-provenance, tool-spec, and runtime facts. These are auxiliary runtime
facts, not candidate decomposition. A provenance entry of
``runtime_variable(name)`` means CUGA deterministically resolved that Python name
from its current VariablesManager before verification. This proves what the
expression will evaluate to at execution time, but it does NOT by itself prove
that the value was supplied by the user, came from a successful tool result, or
satisfies a policy prerequisite; use the source-level context for those claims.

For same-candidate multi-tool execution, provenance is also authoritative about
dataflow. ``prior_call_result(Cn ...)`` means the argument is the direct future
result of earlier call Cn. ``derived_from_prior_call_result(Cn ...)`` means the
argument is produced by local Python transformations whose dataflow ultimately
depends on earlier call Cn. These values do not exist before Cn executes, so do
NOT require their concrete value or independent factual grounding at pre-execution
verification time. This exemption applies only to the earlier-tool-derived portion
of the argument. It does NOT exempt the later tool itself, its policy/prerequisite
checks, its parameter names/shape, or any independent literal/local/runtime values
in the same candidate.

[REASONING_HISTORY]
Previously accepted reasoning trajectory, when present. It may explain continuity
but is not stronger than policy/playbook/user/execution evidence and must not
self-ground external facts.

Decision task
-------------
Decide whether the RAW candidate is permissible given the supplied source-level
context. Return approved unless the raw candidate materially violates,
contradicts, or bypasses an applicable supplied statement/runtime requirement.

Important rules:
1. Evaluate the RAW candidate holistically. Never turn a condition, future plan,
   request, hypothetical, or prerequisite into a present-tense factual claim.
2. A candidate may describe an action that will occur only AFTER a prerequisite;
   do not reject it merely because that prerequisite is not true yet.
3. A request for information needed to establish a prerequisite is not the same
   as claiming that the prerequisite is already satisfied.
4. Do reject a claim that an action/result already happened when supplied state
   does not establish it and an applicable policy requires grounding/execution.
5. Do reject an action that applicable policy/playbook context prohibits or whose
   required preconditions the candidate actually attempts to bypass.
6. For tool_execution, verify that executing every shown call now is allowed and
   that consequential argument values are grounded by the supplied context/runtime
   facts, EXCEPT for values whose deterministic [EXECUTION_DETAILS] provenance is
   ``prior_call_result(...)`` or ``derived_from_prior_call_result(...)``. For those
   same-candidate dependencies, verify only that the dependency is on an earlier
   listed call and do not require a concrete pre-execution value or independent
   factual grounding for the derived portion. Still verify the later tool's
   availability, policy/prerequisites, argument names/shape, and every other
   independently supplied argument normally. A prior call result must not be
   treated as already-observed factual evidence before execution.
7. Absence of a fact from selected context is not automatically proof of its
   opposite. Reject for missing support only when an applicable supplied rule or
   runtime requirement makes that support/precondition necessary.
8. Use only supplied context/runtime evidence plus ordinary linguistic/logical
   reasoning. Do not import outside domain facts.
9. Reconcile apparent conflicts and circular dependencies among applicable binding
   statements before judging the candidate. If a literal reading would make two
   instructions mutually unsatisfiable, would require a prerequisite to already be
   true before performing the explicitly prescribed procedure for establishing that
   prerequisite, or would otherwise make a required procedure impossible, resolve
   the ambiguity using the narrowest interpretation that allows the applicable
   statements to remain jointly effective. In particular, when a specific applicable
   instruction explicitly prescribes an action, lookup, tool, or procedure as the
   means of checking, satisfying, or establishing a prerequisite imposed by a more
   general rule, treat that prescribed establishing step as an enabling exception to
   the general restriction only for the minimum scope necessary to establish the
   prerequisite, unless the supplied context explicitly says the establishing step
   itself requires that prerequisite. Prefer a specific procedural instruction over
   a conflicting general formulation only within that specific procedural scope. Do
   not invent broader permissions, discard unrelated restrictions, or use this rule
   to excuse a candidate when no real conflict or circularity exists. If no narrow
   reconciliation can make the applicable statements jointly coherent, state the
   unresolved conflict in the rejection reason rather than silently choosing an
   arbitrary interpretation.
10. Distinguish internal information access from user-facing disclosure. A read-only
   internal tool call that retrieves information for the agent is not, by itself, a
   disclosure of that information to the user. Likewise, information appearing in an
   execution result, internal runtime observation, or execution-protocol print()
   output is not user-facing merely because the agent can observe it. Do not treat
   such internal retrieval or required runtime output as a privacy leak unless an
   applicable supplied rule explicitly prohibits the lookup/access itself. Evaluate
   disclosure separately when a terminal/user-facing candidate actually communicates
   the information to the user; at that point, enforce any applicable privacy,
   authentication, or non-disclosure requirement normally. This rule does not make
   read-only access universally permissible and does not override an explicit policy
   that forbids the internal lookup itself.
11. When the candidate proposes a policy-controlled categorical value or choice
   (for example a reason code, status, route, mode, category, or tool selection),
   treat the proposed value as a hypothesis to verify, not as evidence for its own
   applicability. Do not approve merely because the proposed value is a valid option
   or can be made superficially plausible. Instead perform this comparison before
   deciding:
   a. Determine the actual situation established by the supplied context/runtime
      evidence.
   b. Identify every supplied alternative whose stated applicability conditions may
      match that situation, not only the alternative named by the candidate.
   c. Evaluate the applicability of each alternative independently against the
      supplied evidence. Do not stretch or merge distinct conditions merely because
      their wording is related. In particular, distinct operations remain distinct
      unless the supplied context explicitly equates them; for example, a failed
      account/database lookup is not automatically a failed knowledge-base search.
   d. Apply any supplied tier, priority, precedence, specificity, or ordering rule
      after determining applicability. If authority says to choose the highest-priority
      or highest-tier applicable option, the proposed value is permissible only when
      no higher-priority applicable alternative is established by the supplied context.
   e. Reject a lower-priority, lower-tier, catch-all, or less-specific proposed value
      when a supplied higher-priority/more-specific alternative applies. When possible,
      cite both the Q statement establishing the selection/priority rule and the Q
      statement(s) establishing the competing applicable alternative.
12. Use ordinary linguistic and logical inference when needed to combine supplied
   facts and apply supplied rules, but distinguish entailment from speculation. Do not
   introduce a new classification, equivalence, prerequisite, causal link, or factual
   premise merely because it seems plausible, typical, likely, or semantically similar
   to something in the context. Any inferred bridge between supplied statements must
   be supported by their combined meaning strongly enough that the conclusion follows
   from them, rather than merely being a reasonable guess. Preserve distinctions that
   the supplied statements preserve; related concepts are not automatically equivalent
   or members of the same policy category. Before applying a conditional rule, establish
   that its applicability condition is supported by the supplied evidence or by a
   strongly entailed inference from that evidence. Do not use a speculative bridge as
   the basis for rejection. This rule does not require every conclusion to be stated
   verbatim: ordinary entailments needed to connect facts to an applicable rule remain
   allowed.
13. When a verifier_rejection Q statement is present, account for it explicitly when
   judging the replacement candidate. Do not oscillate back to a position that ignores
   the immediately preceding rejection. Determine whether the new candidate actually
   resolves, avoids, or still contains the issue recorded there. The prior rejection is
   correction-history context only: it does not override stronger current evidence and
   must not be used by itself as proof that the new candidate is impermissible.

If rejected, violated_context_ids should contain the Q IDs of the statements that
make the candidate impermissible whenever such Q statements exist. Use only IDs
actually supplied. If rejection is based solely on deterministic execution/runtime
facts, the list may be empty. If approved, violated_context_ids must be empty and
reason may be empty or omitted. If rejected, provide a concise reason when confident;
do not invent a corrective explanation merely to populate the field. A missing or
empty reason alone is not a basis for changing the verdict. When a reason is given,
describe the RAW candidate semantics, not retrieval fragments.
""".strip()


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

[LOGIC]
Deterministic propositional verification for candidate atoms that match a stored
logic slot. Simple literal-to-literal constraints are compiled directly to CNF;
compound Boolean/cardinality structures are compiled from ASTs only when needed.
``status=entailed`` means the connected represented logic forces the candidate
true. ``status=contradicted`` means it forces the candidate false.
``status=undetermined`` means at least one satisfying assignment still allows the
candidate to be false; do not let the LLM fill that missing formal premise.
``status=inconsistent`` means the selected logical component is internally
inconsistent and must not be used as positive support. Unresolved slots are free
Boolean variables, never implicitly true.

[REASONING_HISTORY]
Previously accepted intermediate reasoning steps for this same internal
generation cycle. They provide trajectory/continuity context ONLY. They are not
authoritative evidence for external facts, successful actions, permissions, or
satisfied prerequisites. Never use a prior reasoning step to self-ground a
material claim in the terminal candidate. Verified reasoning propositions may,
however, have already been incorporated into [LOGIC] as explicit Boolean facts;
trust only that deterministic projection for such formal use.

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
   - When [LOGIC] reports a matching candidate as contradicted, reject it. When a
     candidate is governed by the shown formal rules and is undetermined because
     a required slot is unresolved/unestablished, treat the missing premise as
     insufficient rather than reasoning it into existence.

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

[LOGIC]
Deterministic SAT classification for candidate atoms that match stored logical
slots. Entailed/contradicted results are authoritative within the represented
formal logic. Undetermined means the required proposition has not been formally
established yet; do not perform the missing world/date/arithmetic reasoning inside
the verifier. The agent must surface that reasoning as a later verified semantic
proposition, which can then bind the unresolved slot.

[REASONING_HISTORY]
Previously accepted reasoning steps in this same internal reasoning cycle.
These steps may establish trajectory, hypotheses already under consideration,
prior planning choices, and logical continuity. They are NOT authoritative
external evidence and must never be used to prove bank state, tool outcomes,
policy permissions, user-provided values, or satisfied prerequisites merely
from their text. Once accepted, their semantic propositions may be used by the
separate deterministic [LOGIC] layer.

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
- runtime_variable(name): resolved from CUGA's current persistent VariablesManager
  before this candidate executes;
- prior_call_result(Cn via name): the direct runtime result of an earlier call
  in this same execution candidate;
- derived_from_prior_call_result(Cn via name): a local Python expression whose
  dataflow depends on an earlier call result (for example parsing, indexing,
  regex extraction, string cleanup, or another local transformation);
- unresolved(expr): could not be resolved deterministically and has no known
  dependency on an earlier same-candidate tool call.
A local_static value does NOT need a separate STATE fact proving that the Python
variable exists; its existence/value was established deterministically from the
candidate code. A runtime_variable value likewise does not need STATE evidence
merely to prove the Python name/value binding: that binding was read directly from
CUGA's VariablesManager. You must still verify that the semantic CONTENT of a
consequential local_static or runtime_variable value is grounded in
STATE/policy/execution evidence as appropriate.

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
   - Treat values marked literal, local_static, or runtime_variable as concrete
     values actually proposed by the candidate. Judge whether their semantic
     content is grounded; do not reject merely because the original call referred
     to a deterministically resolved Python variable.
   - A prior_call_result(Cn ...) or derived_from_prior_call_result(Cn ...) value
     is an explicit same-candidate dependency. Do not require its runtime value to
     already exist in pre-candidate STATE and do not independently grounding-check
     the earlier-tool-derived portion of that argument. The earlier call has not
     executed yet; provenance is what is known pre-execution.
   - This exemption does not authorize the later call. Still verify the later
     tool's availability, policy/prerequisites, parameter names/shape, and all
     independent literal/local/runtime argument content normally.
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


def _reset_reasoning_trace(
    *,
    reason: str,
    preserve_logic_bindings: bool = False,
) -> None:
    reasoning_graph = _VERIFICATION_STATE.reasoning_graph
    step_count = len(_VERIFICATION_STATE.reasoning_steps)
    reasoning_node_ids = set(reasoning_graph.nodes) if reasoning_graph is not None else set()
    node_count = len(reasoning_node_ids)

    removed_bindings = 0
    if reasoning_node_ids and not preserve_logic_bindings:
        for graph in (
            _VERIFICATION_STATE.cuga_policy_graph,
            _VERIFICATION_STATE.playbook_graph,
            _VERIFICATION_STATE.state_graph,
        ):
            if graph is not None:
                removed_bindings += graph.remove_logic_bindings(reasoning_node_ids)

    _VERIFICATION_STATE.reasoning_graph = None
    _VERIFICATION_STATE.reasoning_steps = []
    if step_count or node_count or removed_bindings:
        logger.info(
            "Prompt verifier reasoning trace reset: reason={} steps={} nodes={} "
            "removed_logic_bindings={} preserve_logic_bindings={}",
            reason,
            step_count,
            node_count,
            removed_bindings,
            preserve_logic_bindings,
        )


def _logic_target_graphs(*, include_reasoning: bool = True) -> list[MemoryGraph]:
    graphs: list[MemoryGraph] = []
    for graph in (
        _VERIFICATION_STATE.cuga_policy_graph,
        _VERIFICATION_STATE.playbook_graph,
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

    logger.info(
        "Prompt verifier committed reasoning step: step_id={} nodes={} edges={} "
        "logic_slots={} total_steps={} total_reasoning_nodes={}",
        step_id,
        len(new_node_ids),
        len(step_edge_ids),
        len(candidate_graph.logic_layer.slots),
        len(_VERIFICATION_STATE.reasoning_steps),
        len(reasoning_graph.nodes),
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

    logger.info(
        "Prompt verifier transferred reasoning to STATE: reason={} nodes={} "
        "edges={} logic_slots={}",
        reason,
        len(reasoning_graph.nodes),
        len(reasoning_graph.edges),
        len(reasoning_graph.logic_layer.slots),
    )
    _reset_reasoning_trace(reason=reason, preserve_logic_bindings=True)


def commit_reasoning_trace_to_state() -> None:
    """Public finalization hook for an accepted terminal trajectory."""
    _transfer_reasoning_to_state(reason="accepted_terminal")


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
            and binding.resolved.provenance in {
                "literal",
                "local_static",
                "runtime_variable",
            }
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


def _runtime_variable_source_names(
    used_names: tuple[str, ...],
    bindings: dict[str, _LocalBinding],
) -> tuple[str, ...]:
    """Return underlying CUGA runtime-variable names used by an expression."""
    runtime_names: list[str] = []
    for name in used_names:
        binding = bindings.get(name)
        if binding is None or binding.resolved.provenance != "runtime_variable":
            continue
        runtime_names.extend(binding.resolved.source_names or (name,))
    return tuple(dict.fromkeys(runtime_names))


def _resolve_expression(
    node: ast.AST,
    bindings: dict[str, _LocalBinding],
    *,
    direct_literal: bool = False,
) -> _ResolvedExpression:
    """Resolve a candidate expression without executing arbitrary Python."""
    # Preserve provenance for direct references rather than relabeling them as
    # generic locals. In particular, a direct name bound to an earlier tool call
    # remains a direct prior_call_result, while a name already produced by a local
    # transformation of that result remains derived_from_prior_call_result.
    if isinstance(node, ast.Name):
        binding = bindings.get(node.id)
        if binding is not None and binding.resolved.provenance in {
            "runtime_variable",
            "prior_call_result",
            "derived_from_prior_call_result",
        }:
            return binding.resolved

    ok, value, used_names = _static_value_from_expr(node, bindings)
    if ok:
        runtime_names = _runtime_variable_source_names(used_names, bindings)
        provenance: Literal[
            "literal",
            "local_static",
            "runtime_variable",
            "prior_call_result",
            "derived_from_prior_call_result",
            "unresolved",
        ]
        if runtime_names:
            provenance = "runtime_variable"
        else:
            provenance = "literal" if direct_literal and not used_names else "local_static"
        return _ResolvedExpression(
            rendered=repr(value),
            provenance=provenance,
            static_value=value,
            source_names=runtime_names or used_names,
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
        # A non-name expression that depends on an earlier awaited call is not
        # unresolved: its concrete value is future, but its dataflow provenance is
        # known. We intentionally do not try to execute/interpret transformations
        # such as re.search(...), .group(), .strip(), indexing, or parsing here.
        return _ResolvedExpression(
            rendered=rendered,
            provenance="derived_from_prior_call_result",
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


class _DryRunBlockedOperation(RuntimeError):
    """Candidate operation intentionally unavailable in verifier dry-run."""


class _DryRunSymbolicDependency(RuntimeError):
    """Concrete dry-run cannot continue because it needs a future tool result."""


@dataclass(frozen=True)
class _DryRunToolResult:
    """Opaque future value returned by a recorder stub instead of a real tool."""

    call_id: str
    tool_name: str

    def __bool__(self) -> bool:
        raise _DryRunSymbolicDependency(
            f"Control flow depends on future result_of({self.call_id})"
        )

    def __str__(self) -> str:
        raise _DryRunSymbolicDependency(
            f"String conversion depends on future result_of({self.call_id})"
        )

    def __format__(self, format_spec: str) -> str:
        raise _DryRunSymbolicDependency(
            f"Formatting depends on future result_of({self.call_id})"
        )


class _DryRunAsyncioModule:
    """Small asyncio facade without network/subprocess/event-loop escape hatches."""

    gather = staticmethod(asyncio.gather)
    create_task = staticmethod(asyncio.create_task)
    wait = staticmethod(asyncio.wait)
    as_completed = staticmethod(asyncio.as_completed)
    sleep = staticmethod(asyncio.sleep)
    Queue = asyncio.Queue
    Lock = asyncio.Lock
    Event = asyncio.Event
    Semaphore = asyncio.Semaphore


_DRY_RUN_ALLOWED_MODULES: dict[str, Any] = {
    "re": re,
    "json": json,
    "typing": typing_module,
    "datetime": datetime_module,
    "time": time,
    "asyncio": _DryRunAsyncioModule(),
}

_DRY_RUN_FORBIDDEN_NAMES = {
    "open",
    "eval",
    "exec",
    "compile",
    "globals",
    "locals",
    "vars",
    "input",
    "breakpoint",
    "help",
    "getattr",
    "setattr",
    "delattr",
    "__import__",
}


class _DryRunSafetyValidator(ast.NodeVisitor):
    """Block capability-bearing code while permitting ordinary local Python."""

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root not in _DRY_RUN_ALLOWED_MODULES:
                raise _DryRunBlockedOperation(
                    f"Import {alias.name!r} is unavailable in verifier dry-run"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        if node.level:
            raise _DryRunBlockedOperation(
                "Relative imports are unavailable in verifier dry-run"
            )
        root = str(node.module or "").split(".", 1)[0]
        if root not in _DRY_RUN_ALLOWED_MODULES:
            raise _DryRunBlockedOperation(
                f"Import from {node.module!r} is unavailable in verifier dry-run"
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> Any:
        if isinstance(node.ctx, ast.Load) and node.id in _DRY_RUN_FORBIDDEN_NAMES:
            raise _DryRunBlockedOperation(
                f"Name {node.id!r} is unavailable in verifier dry-run"
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        # Prevent reflection escapes such as ``obj.__class__.__mro__`` while
        # leaving normal methods (.group/.strip/.split/etc.) completely usable.
        if node.attr.startswith("__"):
            raise _DryRunBlockedOperation(
                f"Dunder attribute {node.attr!r} is unavailable in verifier dry-run"
            )
        self.generic_visit(node)


class _DryRunToolCallTransformer(ast.NodeTransformer):
    """Replace awaited real-tool calls with verifier recorder calls."""

    def __init__(self, *, tool_names: set[str], runtime_names: set[str]) -> None:
        self.tool_names = set(tool_names)
        self.runtime_names = set(runtime_names)
        self._assigned_to: str | None = None

    def _expr_meta(self, node: ast.AST) -> dict[str, Any]:
        try:
            ast.literal_eval(node)
            literal = True
        except Exception:
            literal = False
        names = [
            child.id
            for child in ast.walk(node)
            if isinstance(child, ast.Name)
        ]
        return {
            "expr": _expr_text(node),
            "literal": literal,
            "direct_runtime_name": (
                node.id
                if isinstance(node, ast.Name) and node.id in self.runtime_names
                else None
            ),
            "source_names": list(dict.fromkeys(names)),
        }

    def visit_Assign(self, node: ast.Assign) -> Any:
        previous = self._assigned_to
        self._assigned_to = ", ".join(_expr_text(target) for target in node.targets)
        node.value = self.visit(node.value)
        self._assigned_to = previous
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        previous = self._assigned_to
        self._assigned_to = _expr_text(node.target)
        if node.value is not None:
            node.value = self.visit(node.value)
        self._assigned_to = previous
        return node

    def visit_Expr(self, node: ast.Expr) -> Any:
        previous = self._assigned_to
        self._assigned_to = None
        node.value = self.visit(node.value)
        self._assigned_to = previous
        return node

    def visit_Await(self, node: ast.Await) -> Any:
        value = node.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in self.tool_names
        ):
            metadata = {
                "positional": [self._expr_meta(arg) for arg in value.args],
                "keywords": [
                    {
                        "name": kw.arg,
                        **self._expr_meta(kw.value),
                    }
                    for kw in value.keywords
                ],
            }
            replacement = ast.Await(
                value=ast.Call(
                    func=ast.Name(id="__verifier_tool_call__", ctx=ast.Load()),
                    args=[
                        ast.Constant(value=value.func.id),
                        ast.Constant(value=self._assigned_to),
                        ast.Constant(
                            value=json.dumps(
                                metadata,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                        ),
                        *[self.visit(arg) for arg in value.args],
                    ],
                    keywords=[
                        ast.keyword(arg=kw.arg, value=self.visit(kw.value))
                        for kw in value.keywords
                    ],
                )
            )
            return ast.copy_location(replacement, node)
        return self.generic_visit(node)


def _dry_run_import(
    name: str,
    globals: dict[str, Any] | None = None,
    locals: dict[str, Any] | None = None,
    fromlist: tuple[str, ...] | list[str] = (),
    level: int = 0,
) -> Any:
    if level:
        raise _DryRunBlockedOperation(
            "Relative imports are unavailable in verifier dry-run"
        )
    root = str(name or "").split(".", 1)[0]
    module = _DRY_RUN_ALLOWED_MODULES.get(root)
    if module is None:
        raise _DryRunBlockedOperation(
            f"Import {name!r} is unavailable in verifier dry-run"
        )
    return module


def _dry_run_builtins() -> dict[str, Any]:
    """Broad local-computation builtins with capability-bearing entries removed."""
    allowed_names = {
        "abs",
        "all",
        "any",
        "ascii",
        "bin",
        "bool",
        "bytearray",
        "bytes",
        "callable",
        "chr",
        "complex",
        "dict",
        "divmod",
        "enumerate",
        "filter",
        "float",
        "format",
        "frozenset",
        "hash",
        "hex",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "map",
        "max",
        "memoryview",
        "min",
        "next",
        "object",
        "oct",
        "ord",
        "pow",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "type",
        "zip",
        "BaseException",
        "Exception",
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "IndexError",
        "KeyError",
        "LookupError",
        "RuntimeError",
        "StopIteration",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
    }
    result = {
        name: getattr(builtins, name)
        for name in allowed_names
        if hasattr(builtins, name)
    }
    result["__import__"] = _dry_run_import
    # Printing is execution-protocol mechanics here. Do not emit anything and do
    # not call str()/repr() on symbolic future tool results.
    result["print"] = lambda *args, **kwargs: None
    return result


def _dry_run_clone(value: Any) -> Any:
    """Avoid mutating live VariablesManager values during speculative execution."""
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _dry_run_dependency_call_ids(value: Any) -> tuple[str, ...]:
    dependencies: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, _DryRunToolResult):
            dependencies.append(item.call_id)
            return
        if isinstance(item, dict):
            for key, val in item.items():
                collect(key)
                collect(val)
            return
        if isinstance(item, (list, tuple, set, frozenset)):
            for child in item:
                collect(child)

    collect(value)
    return tuple(dict.fromkeys(dependencies))


def _dry_run_resolved_expression(
    value: Any,
    metadata: dict[str, Any] | None,
) -> _ResolvedExpression:
    metadata = dict(metadata or {})
    expression = str(metadata.get("expr") or repr(value))
    source_names = tuple(
        str(name)
        for name in metadata.get("source_names", [])
        if str(name)
    )

    if isinstance(value, _DryRunToolResult):
        return _ResolvedExpression(
            rendered=f"result_of({value.call_id})",
            provenance="prior_call_result",
            dependency_call_ids=(value.call_id,),
            source_names=source_names,
        )

    dependencies = _dry_run_dependency_call_ids(value)
    if dependencies:
        return _ResolvedExpression(
            rendered=expression,
            provenance="derived_from_prior_call_result",
            dependency_call_ids=dependencies,
            source_names=source_names,
        )

    direct_runtime_name = str(metadata.get("direct_runtime_name") or "").strip()
    if direct_runtime_name:
        provenance: Literal[
            "literal",
            "local_static",
            "runtime_variable",
            "prior_call_result",
            "derived_from_prior_call_result",
            "unresolved",
        ] = "runtime_variable"
        source_names = (direct_runtime_name,)
    elif bool(metadata.get("literal", False)):
        provenance = "literal"
    else:
        provenance = "local_static"

    return _ResolvedExpression(
        rendered=repr(value),
        provenance=provenance,
        static_value=_dry_run_clone(value),
        source_names=source_names,
    )


async def _extract_candidate_calls_dry_run(
    candidate: str,
    *,
    runtime_variables: dict[str, Any] | None = None,
    tool_names: set[str],
) -> list[dict[str, Any]]:
    """Execute local Python faithfully while replacing every real tool with a stub.

    This is verifier-side speculative execution only. Runtime variables are copied
    into the dry-run function's local namespace. Ordinary deterministic Python is
    then allowed to run normally (regex, parsing, indexing, comprehensions,
    conditionals, helper functions, datetime formatting, etc.). Awaited calls whose
    direct function name is a currently callable CUGA tool are rewritten to an
    async recorder and are never executed against the real environment.
    """
    blocks = _PYTHON_BLOCK_RE.findall(candidate)
    if not blocks:
        return []
    if not tool_names:
        raise _DryRunBlockedOperation(
            "Runtime tool inventory is unavailable for verifier dry-run"
        )

    body: list[ast.stmt] = []
    runtime_names = {
        str(name)
        for name in (runtime_variables or {})
        if isinstance(name, str) and name.isidentifier()
    }
    transformer = _DryRunToolCallTransformer(
        tool_names=tool_names,
        runtime_names=runtime_names,
    )

    for block in blocks:
        tree = ast.parse(block)
        _DryRunSafetyValidator().visit(tree)
        transformed = transformer.visit(tree)
        ast.fix_missing_locations(transformed)
        body.extend(transformed.body)

    runtime_assignments: list[ast.stmt] = []
    for name in sorted(runtime_names):
        runtime_assignments.append(
            ast.Assign(
                targets=[ast.Name(id=name, ctx=ast.Store())],
                value=ast.Subscript(
                    value=ast.Name(id="__runtime_variables__", ctx=ast.Load()),
                    slice=ast.Constant(value=name),
                    ctx=ast.Load(),
                ),
            )
        )

    dry_function = ast.AsyncFunctionDef(
        name="__verifier_dry_run__",
        args=ast.arguments(
            posonlyargs=[],
            args=[],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        ),
        body=[*runtime_assignments, *body] or [ast.Pass()],
        decorator_list=[],
        returns=None,
        type_comment=None,
    )
    module = ast.Module(body=[dry_function], type_ignores=[])
    ast.fix_missing_locations(module)

    extracted: list[dict[str, Any]] = []

    async def recorder(
        tool_name: str,
        assigned_to: str | None,
        metadata_json: str,
        *args: Any,
        **kwargs: Any,
    ) -> _DryRunToolResult:
        call_id = f"C{len(extracted) + 1}"
        try:
            metadata = json.loads(metadata_json)
        except Exception:
            metadata = {}
        positional_meta = list(metadata.get("positional") or [])
        keyword_meta_rows = list(metadata.get("keywords") or [])
        keyword_meta = {
            str(row.get("name")): row
            for row in keyword_meta_rows
            if isinstance(row, dict) and row.get("name") is not None
        }

        positional_args = [
            _dry_run_resolved_expression(
                value,
                positional_meta[index] if index < len(positional_meta) else None,
            )
            for index, value in enumerate(args)
        ]
        keyword_args = {
            name: _dry_run_resolved_expression(value, keyword_meta.get(name))
            for name, value in kwargs.items()
        }

        extracted.append(
            {
                "call_id": call_id,
                "call": str(tool_name),
                "positional_args": positional_args,
                "keyword_args": keyword_args,
                "assigned_to": assigned_to,
            }
        )
        return _DryRunToolResult(call_id=call_id, tool_name=str(tool_name))

    dry_globals: dict[str, Any] = {
        "__builtins__": _dry_run_builtins(),
        "__name__": "__prompt_verifier_dry_run__",
        "__runtime_variables__": {
            name: _dry_run_clone(value)
            for name, value in (runtime_variables or {}).items()
            if isinstance(name, str) and name.isidentifier()
        },
        "__verifier_tool_call__": recorder,
    }
    compiled = compile(module, "<prompt-verifier-dry-run>", "exec")
    exec(compiled, dry_globals, dry_globals)
    await dry_globals["__verifier_dry_run__"]()
    return extracted


def _extract_candidate_calls_static(
    candidate: str,
    *,
    runtime_variables: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Legacy conservative AST extractor retained as a compatibility fallback."""
    blocks = _PYTHON_BLOCK_RE.findall(candidate)
    if not blocks:
        return []

    extracted: list[dict[str, Any]] = []
    bindings: dict[str, _LocalBinding] = {
        name: _LocalBinding(
            resolved=_ResolvedExpression(
                rendered=repr(value),
                provenance="runtime_variable",
                static_value=value,
                source_names=(name,),
            )
        )
        for name, value in (runtime_variables or {}).items()
        if isinstance(name, str) and name.isidentifier()
    }

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
                elif resolved.provenance == "runtime_variable":
                    bound = _ResolvedExpression(
                        rendered=resolved.rendered,
                        provenance="runtime_variable",
                        static_value=resolved.static_value,
                        dependency_call_ids=resolved.dependency_call_ids,
                        source_names=resolved.source_names or (name,),
                    )
                elif resolved.provenance in {
                    "prior_call_result",
                    "derived_from_prior_call_result",
                }:
                    bound = _ResolvedExpression(
                        rendered=resolved.rendered,
                        provenance=resolved.provenance,
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


async def _extract_candidate_calls(
    candidate: str,
    *,
    runtime_variables: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Prefer faithful restricted dry-run; fall back to the legacy AST extractor."""
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


# Keep the verifier model independent from the model used by the main CUGA
# agent. The existing transport/base-URL/auth settings are cloned below; only
# verifier-specific model/runtime knobs are changed. The default verifier is
# GPT-OSS-120B and _get_model() binds reasoning_effort="high" for GPT-OSS.
# PROMPT_VERIFIER_MODEL_NAME remains environment-overridable for experiments.
PROMPT_VERIFIER_MODEL_NAME = os.environ.get(
    "PROMPT_VERIFIER_MODEL_NAME",
    "aws/gpt-oss-120b",
).strip()

# Claude Haiku 4.5 supports manual extended thinking rather than the newer
# adaptive/effort API. Its maximum output budget is 64k tokens. Reserve 1,024
# tokens for the verifier's required JSON answer and give every remaining token
# to thinking. Environment overrides are retained for controlled experiments.
PROMPT_VERIFIER_CLAUDE_MAX_TOKENS = int(
    os.environ.get("PROMPT_VERIFIER_CLAUDE_MAX_TOKENS", "64000")
)
PROMPT_VERIFIER_CLAUDE_RESPONSE_TOKEN_RESERVE = int(
    os.environ.get("PROMPT_VERIFIER_CLAUDE_RESPONSE_TOKEN_RESERVE", "1024")
)
PROMPT_VERIFIER_CLAUDE_THINKING_BUDGET_TOKENS = int(
    os.environ.get(
        "PROMPT_VERIFIER_CLAUDE_THINKING_BUDGET_TOKENS",
        str(
            PROMPT_VERIFIER_CLAUDE_MAX_TOKENS
            - PROMPT_VERIFIER_CLAUDE_RESPONSE_TOKEN_RESERVE
        ),
    )
)


def _validated_claude_thinking_budget() -> tuple[int, int]:
    """Return (max_tokens, thinking_budget_tokens) for Haiku extended thinking.

    Anthropic requires ``budget_tokens >= 1024`` and ``budget_tokens < max_tokens``.
    Haiku 4.5 supports up to 64k total output tokens. Keep the verifier-specific
    defaults at that ceiling while preserving a small visible-output reserve for
    the required JSON decision.
    """
    max_tokens = PROMPT_VERIFIER_CLAUDE_MAX_TOKENS
    thinking_budget = PROMPT_VERIFIER_CLAUDE_THINKING_BUDGET_TOKENS

    if max_tokens <= 1024 or max_tokens > 64000:
        raise PromptVerificationError(
            "PROMPT_VERIFIER_CLAUDE_MAX_TOKENS must be in [1025, 64000]; "
            f"got {max_tokens}"
        )
    if thinking_budget < 1024 or thinking_budget >= max_tokens:
        raise PromptVerificationError(
            "PROMPT_VERIFIER_CLAUDE_THINKING_BUDGET_TOKENS must be >= 1024 "
            "and strictly less than PROMPT_VERIFIER_CLAUDE_MAX_TOKENS; "
            f"got budget={thinking_budget} max_tokens={max_tokens}"
        )
    return max_tokens, thinking_budget


def _is_claude_model_name(model_name: str) -> bool:
    """Whether a provider model alias denotes Claude/Anthropic."""
    return "claude" in str(model_name or "").lower()


def _runtime_model_name(model: Any) -> str:
    return str(
        getattr(model, "model_name", "")
        or getattr(model, "model", "")
        or PROMPT_VERIFIER_MODEL_NAME
    )


def _verifier_model_settings() -> dict[str, Any]:
    """Clone CUGA transport/auth settings and apply verifier-only model settings.

    Claude is reached through the same OpenAI-compatible provider transport used
    by CUGA. Do not forward settings that are specific to GPT reasoning models or
    OpenAI sampling penalties. In particular, CUGA's LLM layer notes that Bedrock
    Claude variants can reject requests containing both ``temperature`` and
    ``top_p``; the verifier keeps the inherited temperature and removes ``top_p``.
    """
    configured = settings.agent.code.model
    to_dict = getattr(configured, "to_dict", None)
    if callable(to_dict):
        model_settings = dict(to_dict())
    elif isinstance(configured, dict):
        model_settings = dict(configured)
    else:
        model_settings = dict(configured)

    model_settings["model"] = PROMPT_VERIFIER_MODEL_NAME

    if _is_claude_model_name(PROMPT_VERIFIER_MODEL_NAME):
        # Haiku 4.5 uses Anthropic manual extended thinking. CUGA's extra_params
        # is the provider-specific passthrough merged into the OpenAI-compatible
        # client kwargs, so place the Anthropic ``thinking`` object there.
        #
        # Extended thinking is incompatible with non-default temperature/top_k
        # and with ordinary top_p tuning. Use temperature=1 (Anthropic's allowed
        # default while thinking is enabled) and omit the other sampling knobs.
        max_tokens, thinking_budget = _validated_claude_thinking_budget()
        model_settings["max_tokens"] = max_tokens
        model_settings["temperature"] = 1.0
        model_settings["top_p"] = None
        model_settings.pop("top_k", None)
        for key in (
            "reasoning_effort",
            "frequency_penalty",
            "presence_penalty",
        ):
            model_settings.pop(key, None)

        extra_params = model_settings.get("extra_params")
        sanitized_extra_params = (
            dict(extra_params) if isinstance(extra_params, dict) else {}
        )
        for key in (
            "reasoning_effort",
            "top_p",
            "top_k",
            "frequency_penalty",
            "presence_penalty",
            "response_format",
            "thinking",
        ):
            sanitized_extra_params.pop(key, None)
        sanitized_extra_params["thinking"] = {
            "type": "enabled",
            "budget_tokens": thinking_budget,
        }
        model_settings["extra_params"] = sanitized_extra_params

    return model_settings


def _get_model(*, reasoning_effort: Literal["low", "medium", "high"] = "high"):
    """Get the dedicated verifier model using CUGA's existing provider transport.

    ``reasoning_effort`` is retained only for backwards-compatible environment
    overrides to GPT-OSS. Claude Haiku 4.5 instead receives Anthropic manual
    extended thinking with the verifier's maximum configured thinking budget.
    """
    model_settings = _verifier_model_settings()
    model = LLMManager().get_model(model_settings)
    model_name = _runtime_model_name(model)

    if _is_claude_model_name(model_name):
        max_tokens, thinking_budget = _validated_claude_thinking_budget()
        logger.info(
            "Prompt verifier LLM selected: model={} mode=claude_plain_json "
            "extended_thinking=enabled max_tokens={} thinking_budget_tokens={} "
            "response_token_reserve={}",
            model_name,
            max_tokens,
            thinking_budget,
            max_tokens - thinking_budget,
        )
    else:
        logger.info(
            "Prompt verifier LLM selected: model={} mode=structured_output",
            model_name,
        )
    if "gpt-oss" in model_name.lower() or "gpt-oss" in PROMPT_VERIFIER_MODEL_NAME.lower():
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

    parameters_json = _json_snapshot(parameters)
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
    logger.info(
        "[PROMPT_VERIFIER_EXECUTION_CAPTURE] record_id={} tool_name={} "
        "parameters={} output={}",
        record_id,
        tool_name,
        parameters_json,
        output_json,
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
    logger.info(
        "Prompt verifier execution graph updated: new_nodes={} total_nodes={} "
        "edges={} cursor={}",
        len(new_nodes),
        len(graph.nodes),
        len(graph.edges),
        _VERIFICATION_STATE.execution_graph_cursor,
    )
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
        # represented exclusively by execution_graph. Do not decompose/copy them
        # into STATE.
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


def _plain_json_messages(
    *,
    messages: list[BaseMessage],
    schema: type[BaseModel],
    retry: bool,
) -> list[BaseMessage]:
    """Append provider-neutral JSON-schema instructions to verifier messages."""
    schema_json = json.dumps(
        schema.model_json_schema(),
        ensure_ascii=False,
    )
    prefix = (
        "The previous structured-output attempt did not produce a recoverable "
        "decision. "
        if retry
        else ""
    )
    return [
        *messages,
        HumanMessage(
            content=(
                f"{prefix}Return ONLY one JSON object that matches the following "
                "JSON schema exactly. Do not use markdown, code fences, commentary, "
                "or any text outside the JSON object.\n\n"
                f"JSON_SCHEMA:\n{schema_json}"
            )
        ),
    ]


async def _plain_json_invoke(
    *,
    model: Any,
    messages: list[BaseMessage],
    schema: type[BaseModel],
    label: str,
    retry: bool = False,
) -> tuple[Any, dict[str, Any] | None]:
    """Invoke the model without function calling and recover one JSON object.

    This is the primary structured-output path for Claude aliases. CUGA already
    avoids native OpenAI JSON-schema formatting for Claude/Bedrock because that
    format can be translated to unsupported Bedrock ``output_config`` fields.
    Prompting for schema-conforming JSON keeps the verifier on the provider's
    ordinary chat path and remains compatible with OpenAI-style proxy transports.
    """
    plain_messages = _plain_json_messages(
        messages=messages,
        schema=schema,
        retry=retry,
    )
    log_method = logger.warning if retry else logger.debug
    log_method(
        "Invoking {} as plain JSON for {}.",
        _runtime_model_name(model),
        label,
    )
    response = await model.ainvoke(plain_messages)
    return response, _extract_json_from_message(response)


async def _plain_json_retry(
    *,
    model: Any,
    messages: list[BaseMessage],
    schema: type[BaseModel],
    label: str,
) -> dict[str, Any] | None:
    """Retry once without function calling and request a JSON object only."""
    _, parsed = await _plain_json_invoke(
        model=model,
        messages=messages,
        schema=schema,
        label=label,
        retry=True,
    )
    return parsed


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
        raw_prompt=cache_prompt,
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
    _VERIFICATION_STATE.runtime_variables_manager = None

    _VERIFICATION_STATE.state_graph = None
    _VERIFICATION_STATE.state_context_cursor = 0
    _VERIFICATION_STATE.state_context_signatures = []
    _VERIFICATION_STATE.execution_graph = None
    _VERIFICATION_STATE.execution_records = []
    _VERIFICATION_STATE.execution_graph_cursor = 0
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

    logger.info(
        "Prompt verifier runtime initialized: session={} prompt_tools={} "
        "execution_tools={} find_tools_enabled={} variables_manager_registered={}",
        session_id,
        prompt_tool_names,
        normalized_execution_names,
        bool(find_tools_enabled),
        variables_manager is not None,
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
    """Render semantic retrieval text for the exact runtime tools being called.

    Tool descriptions are runtime capability metadata, not verifier authority.
    They are used only to explain what an otherwise code-only candidate action
    means before policy/playbook/context retrieval runs. The final Q statements
    still come exclusively from the normal evidence graphs.
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
    seen: set[str] = set()
    for call in calls:
        name = str(call.get("call") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)

        tool = tool_by_name.get(name)
        if tool is None:
            continue

        description = str(tool.get("description") or "").strip()
        if not description:
            continue

        lines.append(
            "Called runtime tool semantic context:\n"
            f"Tool: {name}\n"
            f"Description: {description}"
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
        combined.merge_logic_layer(graph.logic_layer)

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
    """Retrieve top-k anchors and preserve each anchor's expanded mini-graph.

    The mini-graph boundary is semantically important for source reconstruction.
    Expanded nodes are retrieval support for their anchor; they must not later be
    flattened into independent Q statements.
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
            "mini_graphs": [],
            "truncated": False,
        }

    traversal = build_coverage_aware_mini_graphs(
        evidence_graph,
        [item.node.id for item in ranked],
        config=_VERIFIER_TRAVERSAL_CONFIG,
    )

    selected_node_ids = sorted(traversal.all_node_ids)
    selected_edge_ids = sorted(traversal.all_edge_ids)

    mini_graphs = [
        {
            "anchor_id": mini_graph.anchor_id,
            "node_ids": list(mini_graph.node_ids),
            "edge_ids": list(mini_graph.edge_ids),
            "node_depths": list(mini_graph.node_depths),
            "truncated": bool(mini_graph.truncated),
        }
        for mini_graph in traversal.mini_graphs
    ]

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
                for node_id, depth in getattr(mini_graph, "node_depths", ())
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
        "mini_graphs": mini_graphs,
        "truncated": traversal.truncated,
    }

def _candidate_atom_order_key(node: Any) -> tuple[int, int, str]:
    starts = [
        ref.span.start
        for ref in getattr(node, "source_refs", [])
        if getattr(ref, "span", None) is not None
    ]
    return (min(starts) if starts else 10**12, int(getattr(node, "depth", 0)), node.id)



def _hierarchical_ancestor_ids_including_self(
    graph: MemoryGraph,
    node_id: str,
) -> set[str]:
    """Return hierarchical ancestors of ``node_id`` plus the node itself."""
    result = {node_id}
    frontier = [node_id]
    while frontier:
        current_id = frontier.pop()
        for parent in graph.parents(current_id):
            if parent.id in result:
                continue
            result.add(parent.id)
            frontier.append(parent.id)
    return result


def _covers_partition(
    *,
    graph: MemoryGraph,
    candidate_ancestor_id: str,
    partition_node_ids: set[str],
) -> bool:
    """Whether one hierarchy node covers every selected node in the partition."""
    return all(
        candidate_ancestor_id
        in _hierarchical_ancestor_ids_including_self(graph, node_id)
        for node_id in partition_node_ids
    )


def _semantic_context_closure_target_id(
    *,
    graph: MemoryGraph,
    node_id: str,
) -> str | None:
    """Resolve one atom's mandatory semantic-context closure target.

    Graph construction marks an atomic leaf when the semantic governor for its
    source unit lies outside the extracted unit.  The marker stores the temporary
    ID of the minimum hierarchy ancestor that encloses that external scope.  This
    function resolves that construction-time ID through the actual ancestor chain
    instead of assuming a fixed number of upward hops.

    Missing metadata means the node is semantically closed under the graph
    version that produced it.  A present-but-unresolvable target is treated as
    graph corruption: silently ignoring it would recreate exactly the evidence
    loss this closure rule is intended to prevent.
    """
    node = graph.nodes.get(node_id)
    if node is None:
        raise PromptVerificationError(
            f"Cannot resolve semantic-context closure for unknown node {node_id!r}"
        )

    metadata = dict(getattr(node, "metadata", {}) or {})
    if not bool(metadata.get("semantic_context_dependency_external", False)):
        return None

    target_temporary_id = str(
        metadata.get("semantic_context_closure_ancestor_temporary_id") or ""
    ).strip()
    if not target_temporary_id:
        raise PromptVerificationError(
            "Semantic-context-dependent node is missing its closure ancestor: "
            f"node_id={node_id}"
        )

    frontier = [node_id]
    visited: set[str] = set()
    while frontier:
        current_id = frontier.pop(0)
        if current_id in visited:
            continue
        visited.add(current_id)

        current = graph.nodes.get(current_id)
        if current is None:
            continue
        if str(current.metadata.get("temporary_id") or "") == target_temporary_id:
            return current_id

        frontier.extend(
            parent.id
            for parent in sorted(
                graph.parents(current_id),
                key=lambda item: (-int(getattr(item, "depth", 0)), item.id),
            )
            if parent.id not in visited
        )

    raise PromptVerificationError(
        "Could not resolve semantic-context closure ancestor through hierarchy: "
        f"node_id={node_id} target_temporary_id={target_temporary_id}"
    )


def _source_sentence_spans(source_text: str) -> list[tuple[int, int]]:
    """Return deterministic source-level sentence/structural-unit spans.

    Atomic graph nodes are retrieval units, not safe evidence units. Reconstruction
    therefore needs a source-text floor that restores at least the complete sentence
    containing each selected atom. We intentionally compute that floor from the
    immutable RAW_SOURCE text and root-relative source spans rather than from the
    atom's decomposed wording.

    Sentence-final punctuation is the primary boundary. Markdown headings, bullets,
    table rows, and blank-line boundaries are also treated as structural sentence
    boundaries so punctuation-free list items do not absorb unrelated following
    content. Existing semantic-context dependency closure remains responsible for
    expanding a self-contained sentence/list item to a larger governing scope when
    necessary.
    """
    text = str(source_text or "")
    if not text:
        return []

    boundaries: set[int] = {0, len(text)}

    # Normal prose sentence endings. Require whitespace/end after punctuation so
    # common inline forms such as ``e.g.,`` do not split at the abbreviation dot.
    for match in re.finditer(r"[.!?](?:[\"')\]]+)?(?=\s|$)", text):
        boundaries.add(match.end())

    # Structural Markdown/source boundaries. A bullet/table/heading line is a
    # complete source unit even when it omits terminal punctuation. Blank lines
    # also separate independent source blocks.
    line_start = 0
    lines = text.splitlines(keepends=True)
    structural_line_re = re.compile(
        r"^[ \t]{0,3}(?:#{1,6}[ \t]+|[-*+][ \t]+|\d+[.)][ \t]+|\|)"
    )
    for index, line in enumerate(lines):
        line_end = line_start + len(line)
        bare = line.rstrip("\r\n")
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if (
            not bare.strip()
            or structural_line_re.match(line) is not None
            or structural_line_re.match(next_line) is not None
        ):
            boundaries.add(line_end)
        line_start = line_end

    ordered = sorted(boundary for boundary in boundaries if 0 <= boundary <= len(text))
    spans: list[tuple[int, int]] = []
    for left, right in zip(ordered, ordered[1:]):
        if left >= right:
            continue
        # Keep root-relative coordinates but trim surrounding whitespace so the
        # required span corresponds to the semantic source sentence itself.
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if left < right:
            spans.append((left, right))
    return spans


def _source_sentence_spans_for_node(
    *,
    graph: MemoryGraph,
    node_id: str,
) -> list[tuple[str, tuple[int, int]]]:
    """Return complete source-sentence spans touched by one selected node."""
    node = graph.nodes.get(node_id)
    if node is None:
        raise PromptVerificationError(
            f"Cannot resolve source-sentence closure for unknown node {node_id!r}"
        )

    root = graph.nodes.get(node.source_root_id)
    if root is None:
        raise PromptVerificationError(
            "Cannot resolve source-sentence closure because the source root is "
            f"missing: node_id={node_id} source_root_id={node.source_root_id}"
        )

    source_text = str(root.content or "")
    sentence_spans = _source_sentence_spans(source_text)
    if not sentence_spans:
        return []

    targets: list[tuple[str, tuple[int, int]]] = []
    seen: set[tuple[str, int, int]] = set()
    for source_ref in list(getattr(node, "source_refs", []) or []):
        span = getattr(source_ref, "span", None)
        if span is None:
            continue
        source_id = str(getattr(source_ref, "source_id", "") or "")
        start = int(span.start)
        end = int(span.end)
        for sentence_start, sentence_end in sentence_spans:
            # Exact containment is typical. Overlap is retained as a conservative
            # fallback for an atom whose source span crosses a punctuation boundary.
            overlaps = start < sentence_end and end > sentence_start
            contains_start = sentence_start <= start < sentence_end
            if not overlaps and not contains_start:
                continue
            key = (source_id, sentence_start, sentence_end)
            if key in seen:
                continue
            seen.add(key)
            targets.append((source_id, (sentence_start, sentence_end)))
    return targets


def _node_covers_source_span(
    *,
    node: Any,
    source_id: str,
    target_span: tuple[int, int],
) -> bool:
    """Whether one hierarchy node contains the complete target source span."""
    target_start, target_end = target_span
    for source_ref in list(getattr(node, "source_refs", []) or []):
        span = getattr(source_ref, "span", None)
        if span is None:
            continue
        if source_id and str(getattr(source_ref, "source_id", "") or "") != source_id:
            continue
        if int(span.start) <= target_start and int(span.end) >= target_end:
            return True
    return False


def _source_sentence_closure_target_ids(
    *,
    graph: MemoryGraph,
    node_id: str,
) -> set[str]:
    """Return lowest ancestor IDs that cover every full sentence touched by node."""
    targets: set[str] = set()
    for source_id, sentence_span in _source_sentence_spans_for_node(
        graph=graph,
        node_id=node_id,
    ):
        ancestor_ids = _hierarchical_ancestor_ids_including_self(graph, node_id)
        covering = []
        for ancestor_id in ancestor_ids:
            ancestor = graph.get_node(ancestor_id)
            if _node_covers_source_span(
                node=ancestor,
                source_id=source_id,
                target_span=sentence_span,
            ):
                covering.append(ancestor)
        if not covering:
            raise PromptVerificationError(
                "No hierarchy ancestor covers the selected atom's complete source "
                "sentence: "
                f"node_id={node_id} source_id={source_id!r} "
                f"sentence_span={sentence_span}"
            )

        # Highest depth = lowest/closest hierarchy ancestor that restores the
        # complete source sentence. UUID is only a deterministic tie-breaker.
        target = max(
            covering,
            key=lambda item: (int(getattr(item, "depth", 0)), item.id),
        )
        targets.add(target.id)
    return targets


def _partition_required_cover_ids(
    *,
    graph: MemoryGraph,
    partition_node_ids: set[str],
) -> tuple[set[str], set[str], set[str]]:
    """Return evidence + sentence floor + semantic dependency closure targets."""
    sentence_target_ids = {
        target_id
        for node_id in partition_node_ids
        for target_id in _source_sentence_closure_target_ids(
            graph=graph,
            node_id=node_id,
        )
    }
    semantic_target_ids = {
        target_id
        for node_id in partition_node_ids
        for target_id in [
            _semantic_context_closure_target_id(
                graph=graph,
                node_id=node_id,
            )
        ]
        if target_id is not None
    }
    required = set(partition_node_ids) | sentence_target_ids | semantic_target_ids
    return required, sentence_target_ids, semantic_target_ids


def _closest_covering_ancestor(
    *,
    graph: MemoryGraph,
    partition_node_ids: set[str],
    preferred_start_id: str | None,
) -> Any:
    """Return the lowest hierarchy node satisfying sentence + dependency closure.

    Atomic nodes remain retrieval anchors only. Every selected atom first imposes
    a source-sentence floor: the reconstructed evidence must cover the complete
    original source sentence containing that atom. Construction-time semantic
    dependency metadata may impose an even larger closure target when the sentence
    itself depends on an external governor/list/conditional scope.

    This is deliberately *not* a fixed ``go up one level`` rule. Reconstruction
    climbs only as far as needed to cover selected evidence, complete source
    sentences, and mandatory semantic closure targets. RAW_SOURCE remains legal
    when it is the only common cover.
    """
    if not partition_node_ids:
        raise PromptVerificationError("Cannot reconstruct an empty mini-graph partition")

    (
        required_cover_ids,
        sentence_target_ids,
        semantic_target_ids,
    ) = _partition_required_cover_ids(
        graph=graph,
        partition_node_ids=partition_node_ids,
    )

    sentence_ascent_needed = not sentence_target_ids.issubset(partition_node_ids)
    semantic_ascent_needed = not semantic_target_ids.issubset(partition_node_ids)

    if sentence_target_ids:
        logger.info(
            "Prompt verifier source-sentence closure required: partition_nodes={} "
            "sentence_targets={}",
            sorted(partition_node_ids),
            sorted(sentence_target_ids),
        )
    if semantic_target_ids:
        logger.info(
            "Prompt verifier semantic-context closure required: partition_nodes={} "
            "closure_targets={}",
            sorted(partition_node_ids),
            sorted(semantic_target_ids),
        )

    if preferred_start_id is not None and preferred_start_id in partition_node_ids:
        frontier = [graph.get_node(preferred_start_id)]
        visited: set[str] = set()
        while frontier:
            current = frontier.pop(0)
            if current.id in visited:
                continue
            visited.add(current.id)
            if _covers_partition(
                graph=graph,
                candidate_ancestor_id=current.id,
                partition_node_ids=required_cover_ids,
            ):
                if (
                    (sentence_ascent_needed or semantic_ascent_needed)
                    and current.id != preferred_start_id
                ):
                    logger.info(
                        "Prompt verifier evidence-context ascent: "
                        "preferred_start_id={} selected_ancestor_id={} "
                        "sentence_targets={} semantic_targets={}",
                        preferred_start_id,
                        current.id,
                        sorted(sentence_target_ids),
                        sorted(semantic_target_ids),
                    )
                return current
            parents = sorted(
                graph.parents(current.id),
                key=lambda item: (-int(getattr(item, "depth", 0)), item.id),
            )
            frontier.extend(parents)

    common_ids: set[str] | None = None
    for node_id in sorted(required_cover_ids):
        ancestors = _hierarchical_ancestor_ids_including_self(graph, node_id)
        common_ids = ancestors if common_ids is None else common_ids & ancestors

    if not common_ids:
        raise PromptVerificationError(
            "Mini-graph source-root partition has no common hierarchical ancestor "
            "after source-sentence and semantic-context closure: "
            + ", ".join(sorted(required_cover_ids))
        )

    candidates = [graph.get_node(node_id) for node_id in common_ids]
    return max(candidates, key=lambda item: (int(getattr(item, "depth", 0)), item.id))


def _partition_mini_graph_by_source_root(
    *,
    graph: MemoryGraph,
    node_ids: list[str],
) -> dict[str, set[str]]:
    """Split an expanded anchor mini-graph into independent source trees."""
    partitions: dict[str, set[str]] = {}
    for node_id in node_ids:
        node = graph.nodes.get(node_id)
        if node is None:
            continue
        source_root_id = str(getattr(node, "source_root_id", "") or node.id)
        partitions.setdefault(source_root_id, set()).add(node_id)
    return partitions

def _partition_source_root_by_local_community(
    *,
    graph: MemoryGraph,
    detector: LocalCommunityDetector,
    source_root_node_ids: set[str],
    preferred_seed_id: str | None,
    node_depths: dict[str, int],
) -> list[tuple[str, set[str], str, float | None, bool]]:
    """Split one source-root mini-graph partition into local semantic hubs.

    Community detection is performed on the full active atomic lateral graph,
    treating every lateral edge as undirected. Only the nodes already reached by
    the existing mini-graph traversal are retained for reconstruction. PPR
    communities can overlap, so the retrieval anchor claims overlap first and
    remaining reached nodes seed additional communities in traversal-depth order.

    Returns tuples of:
        (seed_id, selected_node_ids, community_id, sweep_conductance, accepted_cut)
    """
    atomic_ids = {
        node_id
        for node_id in source_root_node_ids
        if node_id in graph.nodes
        and graph.nodes[node_id].kind == NodeKind.ATOMIC_FACT
    }
    non_atomic_ids = set(source_root_node_ids) - atomic_ids

    partitions: list[tuple[str, set[str], str, float | None, bool]] = []
    for partition in detector.partition_selected_nodes(
        atomic_ids,
        preferred_seed_id=(
            preferred_seed_id if preferred_seed_id in atomic_ids else None
        ),
        seed_priority=node_depths,
    ):
        result = partition.community
        selected_ids = set(partition.selected_node_ids)
        partitions.append(
            (
                partition.seed_id,
                selected_ids,
                result.community_id,
                result.sweep_conductance,
                result.accepted_sweep_cut,
            )
        )
        logger.info(
            "Prompt verifier local community: seed_id={} community_id={} "
            "selected_nodes={} full_community_nodes={} connected_component_size={} "
            "sweep_conductance={} accepted_sweep_cut={} sweep_prefix_size={} "
            "pagerank_iterations={}",
            partition.seed_id,
            result.community_id,
            sorted(selected_ids),
            len(result.node_ids),
            result.connected_component_size,
            result.sweep_conductance,
            result.accepted_sweep_cut,
            result.sweep_prefix_size,
            result.iterations,
        )

    # Lateral traversal is expected to operate on atomic nodes. Preserve safety
    # if a future relation source introduces a non-atomic lateral endpoint: never
    # let such nodes force unrelated atomic hubs to share a covering ancestor.
    for node_id in sorted(non_atomic_ids):
        partitions.append(
            (
                node_id,
                {node_id},
                f"non-atomic-{node_id}",
                None,
                False,
            )
        )

    return partitions


def _node_source_type(node: Any) -> str:
    source_refs = list(getattr(node, "source_refs", []) or [])
    if not source_refs:
        return "unknown"
    source_type = getattr(source_refs[0], "source_type", None)
    return getattr(source_type, "value", str(source_type or "unknown"))


def _build_candidate_query_context(
    *,
    candidate_atoms: list[Any],
    cuga_policy_graph: MemoryGraph,
    playbook_graph: MemoryGraph,
    state_graph: MemoryGraph,
    execution_graph: MemoryGraph,
) -> list[_CandidateQueryContextEntry]:
    """Retrieve per anchor and reconstruct one statement per local semantic hub.

    For each candidate atom and evidence graph:
      1. retrieve top-k anchors;
      2. expand each anchor into its existing relation-sensitive mini-graph;
      3. split that mini-graph by ``source_root_id``;
      4. within each source tree, separate reached atomic nodes using seeded
         Personalized PageRank + conductance-sweep local communities;
      5. emit the lowest covering hierarchy ancestor independently for each
         resulting local-community partition.

    A lateral relation into another community therefore creates another local
    hierarchy ascent instead of forcing the original ascent toward RAW_SOURCE.
    Expanded atomic nodes never independently become Q statements.
    """
    graph_specs = [
        ("cuga_policy", cuga_policy_graph),
        ("playbook", playbook_graph),
        ("context", state_graph),
        ("execution", execution_graph),
    ]
    community_detectors = {
        graph_name: LocalCommunityDetector(
            evidence_graph,
            config=_VERIFIER_LOCAL_COMMUNITY_CONFIG,
        )
        for graph_name, evidence_graph in graph_specs
    }

    ordered_entries: list[_CandidateQueryContextEntry] = []
    entry_by_key: dict[tuple[str, str], _CandidateQueryContextEntry] = {}
    for candidate_atom in sorted(candidate_atoms, key=_candidate_atom_order_key):
        for graph_name, evidence_graph in graph_specs:
            evidence = _select_evidence_for_atom(
                candidate_atom=candidate_atom,
                evidence_graph=evidence_graph,
                evidence_space=graph_name,
            )
            detector = community_detectors[graph_name]

            for mini_graph in evidence["mini_graphs"]:
                anchor_id = str(mini_graph["anchor_id"])
                node_depths = {
                    str(node_id): int(depth)
                    for node_id, depth in mini_graph.get("node_depths", [])
                }
                source_root_partitions = _partition_mini_graph_by_source_root(
                    graph=evidence_graph,
                    node_ids=list(mini_graph["node_ids"]),
                )
                if not source_root_partitions:
                    continue

                anchor_node = evidence_graph.nodes.get(anchor_id)
                anchor_root_id = (
                    str(getattr(anchor_node, "source_root_id", "") or anchor_id)
                    if anchor_node is not None
                    else None
                )
                ordered_root_ids = sorted(
                    source_root_partitions,
                    key=lambda root_id: (0 if root_id == anchor_root_id else 1, root_id),
                )

                for source_root_id in ordered_root_ids:
                    source_root_node_ids = source_root_partitions[source_root_id]
                    hub_partitions = _partition_source_root_by_local_community(
                        graph=evidence_graph,
                        detector=detector,
                        source_root_node_ids=source_root_node_ids,
                        preferred_seed_id=(
                            anchor_id if source_root_id == anchor_root_id else None
                        ),
                        node_depths=node_depths,
                    )

                    for (
                        local_seed_id,
                        partition_node_ids,
                        community_id,
                        sweep_conductance,
                        accepted_cut,
                    ) in hub_partitions:
                        ancestor = _closest_covering_ancestor(
                            graph=evidence_graph,
                            partition_node_ids=partition_node_ids,
                            preferred_start_id=local_seed_id,
                        )

                        if ancestor.kind == NodeKind.RAW_SOURCE:
                            logger.warning(
                                "Prompt verifier local-community reconstruction reached "
                                "RAW_SOURCE: candidate_atom_id={} evidence_space={} "
                                "anchor_id={} local_seed_id={} source_root_id={} "
                                "community_id={} sweep_conductance={} accepted_cut={} "
                                "partition_nodes={} statement_chars={}",
                                candidate_atom.id,
                                graph_name,
                                anchor_id,
                                local_seed_id,
                                source_root_id,
                                community_id,
                                sweep_conductance,
                                accepted_cut,
                                len(partition_node_ids),
                                len(ancestor.content or ""),
                            )

                        key = (graph_name, ancestor.id)
                        covered_atomic_ids = {
                            node_id
                            for node_id in partition_node_ids
                            if node_id in evidence_graph.nodes
                            and evidence_graph.nodes[node_id].kind == NodeKind.ATOMIC_FACT
                        }
                        entry = entry_by_key.get(key)
                        if entry is None:
                            entry = _CandidateQueryContextEntry(
                                graph_name=graph_name,
                                source_type=_node_source_type(ancestor),
                                statement_node_id=ancestor.id,
                                statement=ancestor.content.strip(),
                                covered_atomic_node_ids=set(covered_atomic_ids),
                                triggered_by_candidate_atom_ids={candidate_atom.id},
                            )
                            entry_by_key[key] = entry
                            ordered_entries.append(entry)
                        else:
                            entry.covered_atomic_node_ids.update(covered_atomic_ids)
                            entry.triggered_by_candidate_atom_ids.add(candidate_atom.id)

    for index, entry in enumerate(ordered_entries, start=1):
        entry.context_id = f"Q{index}"
        logger.info(
            "[PROMPT_VERIFIER_QUERY_CONTEXT] id={} graph={} source_type={} "
            "statement_node_id={} covered_atomic_nodes={} candidate_query_atoms={} "
            "statement={!r}",
            entry.context_id,
            entry.graph_name,
            entry.source_type,
            entry.statement_node_id,
            sorted(entry.covered_atomic_node_ids),
            sorted(entry.triggered_by_candidate_atom_ids),
            entry.statement,
        )

    logger.info(
        "Prompt verifier candidate query context built: candidate_atoms={} "
        "context_statements={} policy={} playbook={} context={} execution={}",
        len(candidate_atoms),
        len(ordered_entries),
        sum(1 for item in ordered_entries if item.graph_name == "cuga_policy"),
        sum(1 for item in ordered_entries if item.graph_name == "playbook"),
        sum(1 for item in ordered_entries if item.graph_name == "context"),
        sum(1 for item in ordered_entries if item.graph_name == "execution"),
    )
    return ordered_entries


_VERIFIER_CONTEXT_ID_RE = re.compile(r"\bQ\d+\b", flags=re.IGNORECASE)


def _sanitize_previous_verifier_rejection_result(result: str) -> str:
    """Remove verifier-local Q labels before carrying a rejection forward.

    Q IDs are meaningful only inside the verifier call that created them. A prior
    rejection may say things such as "Q28 and Q17 establish ..."; if that text
    is embedded verbatim into the next call, the model can mistake those stale IDs
    for IDs in the new [CANDIDATE_QUERY_CONTEXT]. Preserve the substantive rejection
    explanation while replacing only the obsolete local labels.
    """
    sanitized = _VERIFIER_CONTEXT_ID_RE.sub(
        "the cited prior context statement",
        str(result or ""),
    )
    return _one_line(sanitized)


def _append_previous_verifier_rejection_context(
    entries: list[_CandidateQueryContextEntry],
    previous_rejection: tuple[str, str] | None,
) -> None:
    """Append one label-sanitized ephemeral statement for the prior rejection.

    This record is deliberately not inserted into any memory graph. It exists
    only in the source-context projection for the current verifier call, so it
    cannot contaminate STATE, authority, execution history, retrieval, or logic.

    Verifier-local Q IDs from the previous call are stripped before insertion.
    The new entry receives exactly one fresh Q ID belonging to the current call.
    """
    if previous_rejection is None:
        return

    previous_candidate, previous_result = previous_rejection
    candidate_text = _one_line(previous_candidate)
    result_text = _sanitize_previous_verifier_rejection_result(previous_result)
    if not candidate_text or not result_text:
        return

    entry = _CandidateQueryContextEntry(
        graph_name="verifier_rejection",
        source_type="verifier_rejection",
        statement_node_id="previous-verifier-rejection",
        statement=(
            f"Output [{candidate_text}] was rejected with result [{result_text}]."
        ),
        context_id=f"Q{len(entries) + 1}",
    )
    entries.append(entry)

    logger.info(
        "[PROMPT_VERIFIER_QUERY_CONTEXT] id={} graph={} source_type={} "
        "statement_node_id={} covered_atomic_nodes=[] candidate_query_atoms=[] "
        "statement={!r}",
        entry.context_id,
        entry.graph_name,
        entry.source_type,
        entry.statement_node_id,
        entry.statement,
    )


def _validate_context_decision(
    decision: CandidateContextDecision,
    context_entries: list[_CandidateQueryContextEntry],
) -> None:
    valid_ids = {entry.context_id for entry in context_entries}
    invalid = sorted(set(decision.violated_context_ids) - valid_ids)
    if invalid:
        raise PromptVerificationError(
            "Verifier returned unknown candidate-query-context IDs: " + ", ".join(invalid)
        )
    if decision.verdict == "approved" and decision.violated_context_ids:
        raise PromptVerificationError(
            "Verifier returned violated_context_ids for an approved candidate"
        )


def _candidate_query_origin_label(entry: _CandidateQueryContextEntry) -> str:
    if entry.graph_name in {"cuga_policy", "playbook", "execution", "verifier_rejection"}:
        return entry.graph_name

    if entry.graph_name == "context":
        mapping = {
            SourceType.USER_MESSAGE.value: "user",
            SourceType.ASSISTANT_MESSAGE.value: "assistant",
            "reasoning": "reasoning",
        }
        return mapping.get(entry.source_type, entry.source_type or "state")

    return entry.graph_name


def _render_candidate_query_context(
    entries: list[_CandidateQueryContextEntry],
) -> list[str]:
    return [
        f"{entry.context_id} [{_candidate_query_origin_label(entry)}]: "
        f"{_one_line(entry.statement)}"
        for entry in entries
    ]

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
    state_graph: MemoryGraph,
    execution_graph: MemoryGraph,
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
    decomposition lost conditional/reference/temporal scope.
    """
    candidate_atoms = sorted(
        candidate_graph.atomic_nodes(active_only=True),
        key=_candidate_atom_order_key,
    )
    if not candidate_atoms:
        raise PromptVerificationError(
            "Candidate decomposition produced no atomic retrieval propositions"
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

    logger.info(
        "Prompt verifier source-context projection: candidate_kind={} "
        "candidate_retrieval_atoms={} context_statements={} "
        "previous_verifier_rejection={} projection_chars={}",
        candidate_kind,
        len(candidate_atoms),
        len(context_entries),
        previous_rejection is not None,
        len(verifier_text),
    )

    model = _get_model(reasoning_effort="high")
    messages: list[BaseMessage] = [
        SystemMessage(content=_SOURCE_CONTEXT_VERIFICATION_SYSTEM_PROMPT),
        HumanMessage(content=verifier_text),
    ]

    # Correlate the untouched candidate, the exact verifier prompt, the raw
    # structured-output response, and the final parsed decision in one trace.
    candidate_hash = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:12]
    verification_id = (
        f"{candidate_kind}:{candidate_hash}:{time.time_ns()}"
    )

    logger.info(
        "[PROMPT_VERIFIER_LLM_INPUT] verification_id={} candidate_kind={} "
        "candidate_hash={} context_statements={} previous_verifier_rejection={}\n"
        "===== VERIFIER SYSTEM PROMPT =====\n{}\n"
        "===== VERIFIER USER PROMPT =====\n{}\n"
        "===== END VERIFIER INPUT =====",
        verification_id,
        candidate_kind,
        candidate_hash,
        len(context_entries),
        previous_rejection is not None,
        _SOURCE_CONTEXT_VERIFICATION_SYSTEM_PROMPT,
        verifier_text,
    )

    model_name = _runtime_model_name(model)
    decision_label = "raw-candidate source-context verification decision"

    if _is_claude_model_name(model_name):
        # Mirror CUGA's Claude/Bedrock compatibility strategy: do not rely on
        # OpenAI native structured-output/tool-schema transport for this model.
        # Ask for schema-conforming JSON on the ordinary chat path instead.
        raw_message, raw_args = await _plain_json_invoke(
            model=model,
            messages=messages,
            schema=CandidateContextDecision,
            label=decision_label,
        )
        logger.info(
            "[PROMPT_VERIFIER_LLM_RAW_RESULT] verification_id={} candidate_kind={} "
            "candidate_hash={} structured_output_mode=plain_json raw={!r}",
            verification_id,
            candidate_kind,
            candidate_hash,
            raw_message,
        )

        if raw_args is None:
            raw_args = await _plain_json_retry(
                model=model,
                messages=messages,
                schema=CandidateContextDecision,
                label=decision_label,
            )

        if raw_args is None:
            raise PromptVerificationError(
                "Could not recover CandidateContextDecision JSON from Claude verifier output"
            )

        try:
            decision = CandidateContextDecision.model_validate(raw_args)
        except ValidationError as exc:
            logger.warning(
                "Claude verifier returned JSON that did not match the decision schema: {}. "
                "Retrying once as plain JSON.",
                exc,
            )
            retry_args = await _plain_json_retry(
                model=model,
                messages=messages,
                schema=CandidateContextDecision,
                label=decision_label,
            )
            if retry_args is None:
                raise PromptVerificationError(
                    "Claude verifier returned invalid CandidateContextDecision JSON"
                ) from exc
            decision = CandidateContextDecision.model_validate(retry_args)
    else:
        structured_model = model.with_structured_output(
            CandidateContextDecision,
            method="function_calling",
            include_raw=True,
        )
        result = await structured_model.ainvoke(messages)

        logger.info(
            "[PROMPT_VERIFIER_LLM_RAW_RESULT] verification_id={} candidate_kind={} "
            "candidate_hash={} parsed={!r} parsing_error={!r} raw={!r}",
            verification_id,
            candidate_kind,
            candidate_hash,
            result.get("parsed"),
            result.get("parsing_error"),
            result.get("raw"),
        )

        parsed = result.get("parsed")
        if parsed is not None:
            decision = (
                parsed
                if isinstance(parsed, CandidateContextDecision)
                else CandidateContextDecision.model_validate(parsed)
            )
        else:
            raw_message = result.get("raw")
            parsing_error = result.get("parsing_error")
            raw_args = _extract_structured_args(raw_message)
            if raw_args is None:
                raw_args = await _plain_json_retry(
                    model=model,
                    messages=messages,
                    schema=CandidateContextDecision,
                    label=decision_label,
                )
            if raw_args is None:
                raise PromptVerificationError(
                    "Could not recover CandidateContextDecision JSON. "
                    f"Original parsing error: {parsing_error}"
                )
            decision = CandidateContextDecision.model_validate(raw_args)

    _validate_context_decision(decision, context_entries)
    logger.info(
        "[PROMPT_VERIFIER_LLM_RESULT] verification_id={} candidate_kind={} "
        "candidate_hash={} verdict={} violated_context_ids={} reason={!r}",
        verification_id,
        candidate_kind,
        candidate_hash,
        decision.verdict,
        decision.violated_context_ids,
        decision.reason,
    )
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

    logger.info(
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
            # Query evidence with both the resolved executable action and the
            # exact prompt-visible runtime description of every called tool.
            # The tool description is only semantic retrieval metadata: it is
            # never emitted as a Q statement and never treated as authority.
            tool_retrieval_lines = _called_tool_retrieval_lines(
                candidate_calls,
                _VERIFICATION_STATE.runtime_facts,
            )
            candidate_graph_content = "\n".join(
                [
                    *(
                        f"Proposed tool execution: {_render_candidate_call(call)}"
                        for call in candidate_calls
                    ),
                    *tool_retrieval_lines,
                ]
            )
            logger.debug(
                "Prompt verifier tool semantic retrieval augmentation: "
                "called_tools={} descriptions_added={}",
                [str(call.get("call") or "") for call in candidate_calls],
                len(tool_retrieval_lines),
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

        graph_start = time.perf_counter()

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
        candidate_task = asyncio.create_task(
            _build_graph(
                [candidate_source],
                session_id=session_id,
                semaphore=semaphore,
                link_relations=False,
            )
        )

        state_update, execution_update, candidate_graph = await asyncio.gather(
            state_task,
            execution_task,
            candidate_task,
        )

        state_graph, state_update_mode, state_new_nodes = state_update
        execution_graph, execution_new_nodes = execution_update

        if state_new_nodes:
            await asyncio.to_thread(
                link_new_nodes_to_logic_slots,
                source_graph=state_graph,
                new_node_ids=state_new_nodes,
                target_graphs=_logic_target_graphs(include_reasoning=True),
            )

        graph_time = time.perf_counter() - graph_start

        logger.info(
            "Prompt verifier graph preparation complete: candidate_kind={} "
            "cuga_policy_nodes={} playbook_nodes={} state_update_mode={} "
            "state_new_nodes={} state_cursor={} state_nodes={} "
            "execution_new_nodes={} execution_nodes={} execution_edges={} "
            "reasoning_steps={} reasoning_nodes={} candidate_retrieval_nodes={} "
            "wall_time={:.3f}s",
            resolved_candidate_kind,
            len(cuga_policy_graph.nodes),
            len(playbook_graph.nodes),
            state_update_mode,
            len(state_new_nodes),
            _VERIFICATION_STATE.state_context_cursor,
            len(state_graph.nodes),
            execution_new_nodes,
            len(execution_graph.nodes),
            len(execution_graph.edges),
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
            execution_graph=execution_graph,
            cuga_policy_graph=cuga_policy_graph,
            playbook_graph=playbook_graph,
            reasoning_graph=_VERIFICATION_STATE.reasoning_graph,
            runtime_facts=_VERIFICATION_STATE.runtime_facts,
            runtime_variables=runtime_variables,
            previous_rejection=previous_rejection,
        )
        verification_time = time.perf_counter() - verification_start

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
            logger.info(
                "Prompt verifier accepted reasoning step: retrieval-only candidate "
                "was rebuilt through the full logic pipeline before reasoning commit"
            )
        elif valid and resolved_candidate_kind == "terminal":
            await _promote_approved_terminal_to_state(
                candidate_content=candidate_content,
                session_id=session_id,
                semaphore=semaphore,
            )
            logger.info(
                "Prompt verifier accepted terminal candidate: full approved graph "
                "is now in STATE; verified reasoning remains pending finalization"
            )
        elif valid:
            # Pre-execution code is not a factual state observation. The sandbox
            # records each completed invocation separately after the real tool call.
            # Completed tool/execution observations are not duplicated into STATE.
            logger.info(
                "Prompt verifier accepted tool-execution candidate: not promoting "
                "pre-execution code to STATE; awaiting completed execution record"
            )
        else:
            logger.info(
                "Prompt verifier rejected {} candidate: keeping prior accepted "
                "reasoning trajectory and grounding state for regeneration",
                resolved_candidate_kind,
            )

        logger.info(
            "Prompt verifier decision: candidate_kind={} verdict={} "
            "violated_context_ids={} verification_time={:.3f}s total_time={:.3f}s "
            "reason={!r}",
            resolved_candidate_kind,
            global_verdict,
            decision.violated_context_ids,
            verification_time,
            time.perf_counter() - total_start,
            decision.reason,
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
