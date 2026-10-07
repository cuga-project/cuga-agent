"""System prompts used by memory-graph decomposition and auditing.

These strings are deliberately independent of model transport and graph state.
"""

_SEMANTIC_SEGMENTATION_SYSTEM_PROMPT = """
You perform COARSE SEMANTIC SEGMENTATION before semantic decomposition.

You receive:
- the complete source as ordered deterministic SOURCE BLOCKS;
- an ACTIVE_BLOCK_RANGE that must be partitioned in this call.

The full source is visible only so you can understand references, inherited scope,
and relationships that affect where it is semantically safe to split the ACTIVE
range. You are NOT performing semantic decomposition and you are NOT rewriting
the text.

Your task is to partition exactly the ACTIVE range into meaningful contiguous
semantic chunks.

IMPORTANT RESPONSIBILITY BOUNDARY
---------------------------------
- You decide semantic boundaries.
- Python validates exact coverage/order and recursively asks you to segment any
  resulting chunk that is still larger than the configured leaf threshold.
- Python will NOT arbitrarily split an oversized semantic chunk.
- Therefore, preserve meaningful context when choosing boundaries.
- If a large semantic unit must be divided, choose a boundary where each side
  can later be made self-contained by a light contextual rewrite.
- Do not force unrelated material together merely to approach a target size.

OUTPUT CONTRACT
---------------
Each returned chunk is defined only by:
- start_block_index
- end_block_index

The returned chunks must:
- cover every block in ACTIVE_BLOCK_RANGE exactly once;
- be ordered;
- be contiguous;
- be non-overlapping and gap-free;
- stay entirely inside ACTIVE_BLOCK_RANGE.

If ACTIVE_BLOCK_RANGE is larger than the configured leaf threshold, return at
least TWO strictly smaller chunks. Do not return the entire active range unchanged.

You do NOT need to make every returned child smaller than the leaf threshold in
one call. If a child is still too large, Python will recursively ask you to
segment that child semantically.

Do not output contextualized text, propositions, graph nodes, graph relations, or
summaries.

Return only the structured SemanticSegmentationDecision.
""".strip()


_FINAL_CONTEXTUALIZATION_SYSTEM_PROMPT = """
You perform a LIGHT, SOURCE-GROUNDED contextualization pass over finalized
semantic chunks.

You receive:
- the complete source as exact SOURCE BLOCKS;
- FINAL_LEAF_CHUNKS chosen by recursive semantic segmentation.

Every finalized leaf chunk is already small enough for the later decomposition
model. Your job is to make each leaf locally understandable WITHOUT forwarding
the whole source into decomposition.

For every FINAL_LEAF_CHUNK:
1. identify only the external source blocks genuinely needed to resolve references
   or inherited scope;
2. produce a minimally rewritten contextualized_text that is self-contained.

The later decomposition model receives ONLY contextualized_text for that leaf.
It does NOT receive the whole source or the external context blocks.

AUTHORITATIVE VS DERIVED TEXT
-----------------------------
SOURCE BLOCKS are authoritative.
contextualized_text is derived interpretation only.

Use external context only for things such as:
- pronouns and anaphora;
- phrases such as "this rule", "the above", "that decision", or "these steps";
- omitted subjects/objects that are unambiguous from source context;
- inherited scope from headings or nearby text;
- cross-chunk references that would otherwise be unclear.

Prefer replacing a reference with its referent rather than copying large amounts
of supporting text.

BAD:
    Copy several previous paragraphs into the contextualized chunk.

GOOD:
    Replace "this exception" with the specific exception/rule it refers to.

LOSSLESS LIGHT-TOUCH REQUIREMENT
--------------------------------
Preserve:
- polarity and modality;
- conjunction vs disjunction;
- conditions and exceptions;
- quantifiers, thresholds, counts, and ordering;
- temporal scope and attribution;
- restrictive words such as only, unless, before, after, first, correctly,
  exactly, any, all, none, and equivalent phrasing.

Do not add facts, rules, permissions, prohibitions, implications, causal claims,
or prerequisites absent from the source.

If a reference is genuinely ambiguous, preserve the ambiguity rather than invent
one interpretation.

CONTEXT BLOCKS
--------------
context_block_indices may reference source blocks outside the primary leaf when
they are necessary to interpret it.

- Do not list blocks already inside the primary leaf.
- Use the smallest useful context set.
- External context is not copied wholesale into contextualized_text.
- Context block identities are preserved as metadata for later graph/linking use.

OUTPUT CONTRACT
---------------
Return exactly one FinalChunkContextPlan for every FINAL_LEAF_CHUNK.
chunk_index must match the supplied final leaf index.
Do not omit, duplicate, or reorder chunk indices.

Keep contextualized_text concise. Its purpose is reference/scope resolution, not
summarization of the whole source.

Return only the structured FinalChunkContextDecision.
""".strip()


_CONTEXTUAL_CHUNKING_AUDIT_SYSTEM_PROMPT = """
You audit finalized source-backed contextualized leaf chunks for semantic
faithfulness.

You receive:
- the complete ordered SOURCE BLOCKS;
- the finalized primary leaf chunks;
- exact external context blocks selected for each leaf;
- each derived contextualized_text.

Mark complete=true only if all of the following hold:

1. Every contextualized_text faithfully represents its own primary source leaf.
2. External context is used only to resolve references, inherited scope, ellipsis,
   or otherwise necessary interpretation.
3. No contextualized leaf invents facts, rules, conditions, permissions,
   prohibitions, exceptions, causal claims, prerequisites, or implications.
4. Important modality, polarity, conjunction/disjunction, ordering, thresholds,
   quantifiers, attribution, uncertainty, and restrictive qualifiers are preserved.
5. References such as "this", "that", "it", "they", "the above", or "these rules"
   are resolved only when source context supports the resolution.
6. A leaf that would otherwise be misleading or materially ambiguous is made
   self-contained through a faithful light rewrite.
7. Contextualization does not reproduce unrelated independent semantics from
   context blocks.
8. The contextualized text is concise enough to serve as a local decomposition
   input rather than a restatement of the full source.

The exact source leaves and context blocks are authoritative.
The contextualized text is only a derived aid for later decomposition.

Return:
- complete: true/false
- issues: a list of localized issue objects. Every issue object MUST contain:
  - chunk_index: the exact zero-based chunk_index from FINAL_CONTEXTUALIZED_LEAVES
  - issue: a concise description of the semantic distortion, unresolved dependency,
    wrong referent, lost scope, unsupported addition, or excessive context copying
- reason: concise overall assessment

LOCALIZATION REQUIREMENT
------------------------
When complete=false, identify every defective leaf by its exact supplied
chunk_index. Do not report an unindexed/global issue when the defect belongs to a
leaf. Do not include good leaves in issues. When complete=true, issues must be
empty.
""".strip()


_LOGIC_NORMALIZATION_SYSTEM_PROMPT = """
Normalize source-explicit RELATIONAL and PROPOSITIONAL structure in exactly ONE
source statement.

This is a narrow language-normalization task. Do not build persistent graph edges,
do not create semantic nodes, and do not decide whether an AST is required. Python
performs deterministic routing after your response.

You receive:
- SOURCE: the exact statement being analyzed;
- AVAILABLE_PROPOSITIONS: semantic atomic descendants already produced for this
  statement.

Return LogicNormalizationDecision with two independent kinds of structure:
1. ``relations`` for source-explicit BINARY semantic relations whose two endpoints
   are exact AVAILABLE_PROPOSITIONS;
2. ``slots`` / ``expressions`` / ``clauses`` only for Boolean/cardinality structure
   that must first be normalized canonically.

If SOURCE has no source-explicit binary relation or Boolean/cardinality structure,
return empty relations, slots, expressions, and clauses. Ordinary standalone facts,
requirements, prohibitions, and procedures do not need an entry merely because they
are propositions.

BINARY SEMANTIC RELATIONS
-------------------------
Use ``relations`` when SOURCE explicitly relates two semantic propositions with one
binary relation that can live directly in the graph. Important examples include:
- PRECEDES for before/after/first/then ordering;
- REQUIRES for an explicit prerequisite/dependency relation;
- ENABLES for an explicitly stated enabling/establishment relation;
- CAUSES for explicit causal language;
- QUALIFIES for an explicit qualification/exception relation;
- SUPPORTS or SUPERSEDES when explicitly stated.

Examples:
- "Before A, do B" -> B PRECEDES A.
- "After A, do B" -> A PRECEDES B.
- "B requires A" -> B REQUIRES A.
- "A enables B" -> A ENABLES B.

Use proposition indices only for exact endpoint identity. Do not point a relation at
a broader rule node merely because it contains one endpoint. Do not invent a binary
relation when the source is ambiguous. ``DECOMPOSES_INTO`` is forbidden.

Do NOT directly emit IMPLIES for Boolean condition/effect language. Normalize such
language through clauses below so Python can determine whether it reduces to one or
more binary IMPLIES edges or requires compound logic.

LANGUAGE NORMALIZATION
----------------------
Interpret equivalent natural-language connectives, not only literal keywords.
Conjunction may be expressed by "and", "both", "together with", "along with",
"as well as", or "in addition to". Disjunction may be expressed by "or",
"either", "one of", "alternatively", or equivalent phrasing. Conditions may use
"if", "when", "provided that", "assuming", "only if", "unless", or equivalent
constructions. Temporal order may use "before", "after", "first", "then",
"prior to", "following", or equivalent phrasing. Normalize meaning, not surface
words.

SLOTS AND BINDING
-----------------
Create a slot only for a proposition that participates in Boolean/cardinality
structure. Bind proposition_index only when one AVAILABLE_PROPOSITION independently
expresses the same complete proposition. A broader conditional/rule node is not
identical to one of its internal operands. Leave proposition_index=null when there
is no exact semantic proposition. Unresolved slots are valid.

Use proposition_value=false only when the available proposition explicitly asserts
the negation of the slot. Never bind by inference, arithmetic, date reasoning,
world knowledge, or implication.

TEMPORARY CANONICAL EXPRESSIONS
-------------------------------
Use direct signed slot operands for literals. Use expression nodes only to expose
the source's actual grouping:
- AND
- OR
- NOT for scoped negation that cannot be represented as a signed literal
- AT_LEAST / AT_MOST / EXACTLY for Boolean cardinality only

Do NOT optimize or distribute expressions yourself. Python owns that decision.
Examples:
- "If A then B" -> rule with condition=A, effect=B.
- "A only if B" -> rule with condition=A, effect=B.
- "A if B" -> rule with condition=B, effect=A.
- "If A then B and C" -> rule with effect=AND(B,C).
- "If A or B then C" -> rule with condition=OR(A,B).
- "If A and B then C" -> rule with condition=AND(A,B).
- "If A then B or C" -> rule with effect=OR(B,C).

Python will later convert reducible literal-level rules into graph IMPLIES edges,
for example:
- A -> (B AND C) becomes A->B and A->C;
- (A OR B) -> C becomes A->C and B->C;
while irreducible forms such as (A AND B)->C or A->(B OR C) remain in the logic
layer.

CLAUSES
-------
Use kind=rule for explicit Boolean condition -> effect structure. Use kind=assertion
only when truth-functional grouping itself must be preserved, such as an asserted
OR, cardinality constraint, or scoped compound negation. Do not create an assertion
for ordinary standalone A, NOT A, or A AND B when the semantic graph already carries
those independent assertions.

A statement may contain more than one explicit relation or logical clause.
evidence_text must be a source-supported excerpt.

Return only LogicNormalizationDecision.
""".strip()


_LOGICAL_STRUCTURE_AUDIT_SYSTEM_PROMPT = """
Audit one proposed COMPOUND propositional-logic remainder against SOURCE and
AVAILABLE_PROPOSITIONS.

This is a compound-logic audit, not semantic coverage and not a checklist of
available propositions. SOURCE-explicit simple binary relations are supplied
separately as NORMALIZED_SIMPLE_RELATIONS and are owned by the semantic relation
graph, not by the logic layer.

Representation rule
-------------------
Simple A -> B and every other Boolean rule that Python can distribute losslessly
to binary IMPLIES edges belongs in NORMALIZED_SIMPLE_RELATIONS and should NOT be
duplicated in PROPOSED_COMPOUND_LOGIC. AST expressions are appropriate only for
genuine irreducible AND/OR/cardinality/nested Boolean structure.

Unresolved slots are valid
--------------------------
An unresolved slot is a first-class Boolean variable. It may appear anywhere a
resolved slot may appear: as a rule condition/effect or inside a compound
expression. ``proposition_index=null`` means only that no existing semantic node
is bound yet. It does NOT invalidate the surrounding logical structure.

AVAILABLE_PROPOSITIONS boundary
-------------------------------
Bind proposition_index only for exact proposition identity. Containment, overlap,
or participation in a larger rule is not identity.

Examples:
- available: "If C, perform A"; slot: "perform A" => DO NOT bind.
- available: "When time is needed, use get_current_time()"; slot: "use
  get_current_time()" => DO NOT bind unless a separate proposition independently
  states the action.
- available: "Y requires P"; slots "Y" and "P" => neither is automatically bound
  to the broader requirement node.
- available: "At least 30 days have passed"; matching slot => bind.

Completeness criteria
---------------------
Mark complete=true when:
- every irreducible SOURCE-explicit Boolean/cardinality structure is represented
  with correct scope, direction, and polarity;
- any reducible Boolean structure is already represented by
  NORMALIZED_SIMPLE_RELATIONS rather than duplicated as an AST;
- compound ASTs are used only where compound structure is actually required;
- every proposition_index binding is an exact semantic-identity binding;
- slots lacking an exact available proposition remain unresolved;
- rule-only terms are not incorrectly asserted true;
- truth-functional assertions whose grouping matters (for example OR/cardinality)
  are preserved; ordinary semantic facts are not duplicated as logic assertions;
- no arithmetic/date/world/deductive reasoning was used to fill missing slots.

IMPORTANT:
- A partial representation is NOT incomplete because slots are unresolved.
- Do not require graph-owned PRECEDES/REQUIRES/ENABLES/CAUSES/QUALIFIES relations
  to appear in the logic layer.
- Do not require a reducible IMPLIES relation to appear in the logic layer when it
  is present in NORMALIZED_SIMPLE_RELATIONS.
- Do not require every AVAILABLE_PROPOSITION to appear in logic.
- Ordinary non-logical statements do not need logic entries.
- Never set complete=false for an item you call optional, preferable, cleaner, or
  merely an alternative representation.

Return:
- complete
- missing_logic: only required source-explicit logical structure that is absent or
  materially misrepresented
- unsupported_logic: invented/mis-scoped structure, invalid exact bindings, or
  unnecessary AST structure that changes meaning
- reason: concise assessment
""".strip()


_RELATION_SYSTEM_PROMPT = """
You identify direct semantic relations between atomic propositions in a memory graph.

You receive:
- exactly one ANCHOR proposition;
- a small list of CANDIDATE propositions that were preselected by another layer.

You are NOT searching the graph.
You are NOT creating graph nodes or edges.
You are NOT responsible for candidate retrieval.
Your only task is to decide which supplied candidates have a meaningful direct
relation to the anchor, what relation exists, and its direction.

Allowed relation types
----------------------
related_to:
    The propositions are directly semantically related, but none of the more
    specific relation types below applies.

equivalent_to:
    The propositions express materially the same fact, rule, state, or action.

corefers_with:
    The propositions contain expressions that refer to the same underlying
    referent.

same_entity:
    The propositions concern the same concrete entity.

same_event:
    The propositions concern the same event or action occurrence.

supports:
    One proposition provides evidence or justification for the other.

contradicts:
    The propositions cannot both hold under the same applicable conditions,
    attribution, and temporal scope.

qualifies:
    One proposition narrows, conditions, limits, or adds an exception/qualification
    to the other.

serves_goal:
    One proposition describes an action/state that serves the goal expressed by
    the other.

requires:
    One proposition depends on the other being satisfied or true.

enables:
    One proposition makes the other possible or helps establish a prerequisite
    for it.

precedes:
    One proposition must occur before the other in an explicitly stated
    temporal or procedural order.

causes:
    One proposition causally produces or leads to the other.

supersedes:
    One proposition replaces an older state, value, instruction, or fact
    represented by the other.

Direction
---------
For directional relations, return:
- anchor_to_candidate when the anchor bears the relation to the candidate;
- candidate_to_anchor when the candidate bears the relation to the anchor.

For symmetric relations, return symmetric.

Treat these relations as symmetric:
- related_to
- equivalent_to
- corefers_with
- same_entity
- same_event
- contradicts

All other allowed relations are directional.

Important rules
---------------
- ``anchor_context_paths`` and candidate ``context_paths`` are semantic ancestry,
  not extra facts. Use them to disambiguate inherited scope and referents.
- Identical leaf wording under different parents does NOT imply equivalent_to,
  corefers_with, or any other identity relation. Compare complete contextual
  meanings. If "this rule" points to different rules, keep the occurrences
  distinct.
- Different wording may still be equivalent when the contextual paths establish
  the same complete proposition.
- Omit candidates that have no meaningful direct relation to the anchor.
- Do not create a relation merely because two propositions share a broad topic.
- Prefer the most specific supported relation over related_to.
- Do not invent facts or relations not supported by the proposition meanings.
- Account for polarity, modality, conditions, temporal scope, attribution, and
  qualifiers.
- Two rules are NOT contradictory merely because one states a prerequisite and
  another describes the procedure used to establish that prerequisite.
- Distinguish a prerequisite from the procedure that establishes/checks it. If
  proposition A describes the permitted method for establishing/checking state P,
  and proposition B expresses or depends on P, prefer an ENABLES relation from A
  toward P/B when supported; do not reverse it into "A requires P" unless the
  proposition meanings explicitly state that prerequisite.
- Generic forms such as "to establish P, perform A" or "verify P by checking A"
  normally encode procedural direction from A toward P, not a requirement that P
  already hold before A can occur.
- Keep ordering distinct from prerequisites. "A happens before B" should use
  PRECEDES when the order is explicit; do not encode mere ordering as REQUIRES.
- Generic ordering forms:
  - "First A, then B" -> A PRECEDES B.
  - "Before B, perform A" -> A PRECEDES B.
  - "After A, perform B" -> A PRECEDES B.
- PRECEDES is directional. The source is the earlier action/state and the target
  is the later action/state.
- IMPLIES is reserved for the source-explicit normalization pass and must not be
  inferred by this broader relation-linking model.
- The hierarchical relation decomposes_into is forbidden here; it is owned by
  deterministic hierarchy construction.
- other_node_id must be the ID of one of the supplied candidates.
""".strip()


_DECOMPOSITION_SYSTEM_PROMPT = """
You decompose exactly ONE statement at a time.

A deterministic dependency-parser scaffold may be supplied with the request. It
is ADVISORY structural evidence, not authoritative semantics. Use its predicate
frames, participants, modality/negation, and connective cues to avoid dropping
source meaning. If the parser is wrong, preserve the source meaning rather than
forcing the scaffold.

You are NOT constructing a graph.
You are NOT responsible for canonical node IDs, parent IDs, hierarchy edges, graph
depth, graph mutation, candidate retrieval, global lateral-relation discovery, or
formal logical parsing.

A dedicated SECOND PASS runs after semantic decomposition and receives the original
source plus all semantic propositions produced by this pass. That second pass owns
source-explicit relations/connectives between operands, including temporal order,
prerequisites, simple condition -> effect structure, Boolean/cardinality grouping,
and IF/WHEN/UNLESS/ONLY-IF/OTHERWISE semantics. Python routes reducible binary
structure to graph relations and keeps only irreducible Boolean structure in the
logic layer. A later relation linker owns broader cross-node relation discovery.

For a composite statement, you MAY return sparse local relation hints when a
relation is completely unambiguous and explicitly stated by the immediate parent.
These hints are optional conveniences. They are NOT part of the semantic-completeness
contract of this pass because the second normalization pass receives the unchanged
SOURCE and can recover source-explicit relations there.

Your goal is LOSSLESS SEMANTIC PROPOSITION DECOMPOSITION, not summarization and not
logical reconstruction. The direct children of a composite statement must
collectively preserve every operationally meaningful OPERAND and every qualifier
that belongs inside an operand. Connective words whose only job is to relate two
separate operands should not be turned into standalone semantic children.

Your only task is to decide whether the supplied statement is:

1. atomic
   - It expresses one independently meaningful fact, rule, requirement,
     prohibition, permission, condition, procedure, prescribed method,
     user claim, observation, or intended action.
   - A statement is NOT atomic merely because it can be summarized in one sentence.
     If it contains multiple independently applicable clauses, conditions,
     procedures, exceptions, or ordered requirements, it is composite.
   - Return kind="atomic".
   - Return children=[].
   - An atomic statement MUST be a standalone, semantically meaningful proposition.
     It must not be a bare auxiliary/modal/connective fragment whose meaning depends
     on a missing lexical predicate or complement. Never emit/accept fragments such
     as "have to", "has to", "must", "should", "can", "need to", "only if",
     or a bare infinitival marker as an atomic proposition. Keep modality and
     auxiliaries attached to the lexical predicate and its required arguments.
   - Do not generate subjects/predicates/objects here. A dedicated post-tree
     extractor fills retrieval payloads only after final atomic leaves are known.

2. composite
   - Its meaning can be separated into more specific direct semantic components.
   - Return kind="composite".
   - Return the DIRECT child statements only.
   - Do not recursively decompose the children yourself.
   - Do not generate subjects/predicates/objects for the composite statement.
   - Do not return proposition/S/P/O fields for children. Retrieval payloads are
     generated only after the complete semantic hierarchy is built.

A composite child must be strictly narrower than its parent.
Never return the complete parent statement unchanged as one of its children.
Do not create vague heading-like children when the parent contains concrete rules.

OCCURRENCE IDENTITY AND REPEATED WORDING
----------------------------------------
Do not emit the same semantic child twice for the same source occurrence. However,
identical or near-identical wording can legitimately appear more than once when it
belongs to different source occurrences, referents, scopes, or parent rules. Those
occurrences must remain distinct.

When the immediate parent makes an anaphoric referent unambiguous, make the child
content self-contained enough to preserve that context. For example, prefer
"The tool-V requirement may be overridden when the user is a kid" over an isolated
"This statement may be overridden when the user is a kid" when "this statement"
clearly refers to the tool-V requirement. Preserve genuine ambiguity rather than
inventing a referent.

Never deduplicate children merely because their surface strings match. Source
occurrence and inherited parent meaning are part of proposition identity.

CHILD OUTPUT CONTRACT
---------------------
Every composite child MUST contain:
- content: the lossless semantic child statement;
- source_text: the best supporting excerpt from the immediate parent statement;
- semantic_role: the child's primary role from the supplied schema.

Prefer copying ``source_text`` verbatim as one contiguous excerpt from the parent.
Do not manufacture evidence. Minor formatting normalization, bullet-marker changes,
or punctuation differences in ``source_text`` are tolerated by the runtime when
the child meaning is still directly supported by the parent. The child ``content``
may paraphrase for clarity only when the meaning is fully preserved.

LOSSLESSNESS REQUIREMENT
------------------------
For a composite statement, every independently operative semantic clause in the
parent MUST be represented by at least one direct child. Do not select only the
"main" ideas. Do not summarize several clauses into a broader child if that loses
how, when, under what condition, by what procedure, in what order, or with what
qualification a rule applies.

Preserve in CHILD CONTENT whenever present:
- facts and assertions;
- requirements and prohibitions;
- permissions and optional actions;
- prerequisites and postconditions;
- conditions and conditional branches;
- procedures, prescribed methods, and means of accomplishing/checking something;
- statements that explain how a prerequisite, state, condition, or result is
  established or verified;
- exceptions, overrides, and fallback rules;
- temporal scope that belongs inside one proposition, such as "before 5 PM" or
  "until the account closes";
- thresholds, counts, quantifiers, and selection rules;
- polarity, modality, attribution, uncertainty, and scope;
- restrictive or satisfaction-changing qualifiers.

The structured PropositionPayload does NOT need to duplicate these nuances. It is
only a coarse subject/predicate/object lexical index; exact semantic fidelity is
owned by child content and the later relation/logic passes.

SEMANTIC OPERANDS MUST SURVIVE; FORMAL CONNECTORS MAY BE DEFERRED
----------------------------------------------------------------
Preserve every independently meaningful operand and every qualifier that belongs
inside an operand. The later normalization pass receives the exact original SOURCE,
so relational/Boolean connectors BETWEEN returned children do not need to be
redundantly copied into child content.

Examples:
- "If A, then B" may decompose to children A and B. The later logic pass owns A -> B.
- "Either A or B" may decompose to children A and B. The later logic pass owns OR.
- "Before A, do B" should decompose to clean children A and B. The later
  normalization pass owns B PRECEDES A; do not create a child "Before A".
- "B requires A" should decompose to clean children B and A. The later
  normalization pass owns B REQUIRES A; do not keep "requires A" attached to B
  merely to preserve the relation word.
- "At least 30 days have passed" is one semantic proposition: "at least 30 days"
  is internal to that proposition and must NOT be weakened to "30 days".
- "Only authorized users may act" must preserve "only authorized users" because
  that restricts the operand itself.

RELATIONAL CONNECTIVES VS OPERAND-INTERNAL QUALIFIERS
-----------------------------------------------------
When before/after/first/then/if/when/unless/requires/depends-on or equivalent
language CONNECTS two independently meaningful operands, return the clean operands
and leave the relation/connective to the later normalization pass.

Do NOT create connective-only children such as "Before", "After", "If", "Then",
"Unless", or "Requires". Do NOT recursively split a clean operand just to preserve
a connective that belongs between siblings.

Preserve temporal/restrictive wording when it is INTERNAL to one proposition and
cannot be represented as a relation between sibling operands. Examples include
"before 5 PM", "for at least 30 days", "already verified", and "only authorized
users".

PROCEDURES AND PREREQUISITES
----------------------------
A prerequisite and the procedure used to establish/check that prerequisite are
different semantic statements and must be preserved separately when both are
present.

Examples of generic forms that contain procedural meaning:
- "To establish P, perform A."
- "To verify P, check A."
- "P is determined by comparing A with B."
- "Before B, establish P by performing A."

Do NOT collapse these into only "B requires P" or only "P is required".
Preserve the procedure/method A as its own child when it is independently useful.
Do NOT collapse an establishment/checking procedure into a generic condition.
Preserve the procedure as semantic content when it is independently useful.

SOURCE GROUNDING
----------------
Every child must be directly supported by a specific phrase, sentence, or clause
in the supplied parent statement. A child may paraphrase for clarity, but it must
not add a rule, prerequisite, exception, or implication that is absent from the
source. Return that support explicitly in the child's ``source_text`` field. Prefer
a verbatim contiguous excerpt from the parent whenever possible. Before returning,
map every child back to supporting source wording and confirm that no operative
source clause is left unmapped. Exact punctuation or formatting identity is less
important than faithful semantic support.

For policy/rule text:
- preserve requirements, prohibitions, permissions, conditions, and procedures;
- preserve modality, operand-internal temporal scope, exceptions, and satisfaction
  criteria;
- preserve how prerequisites are established, not only that they are required;
- preserve what must be true for a condition to count as satisfied;
- preserve explicit prerequisite wording in semantic content; the later
  normalization pass owns condition/prerequisite relations between propositions.

For user messages:
- preserve requests, preferences, values supplied by the user, and claims;
- preserve uncertainty and qualifications in the user's wording;
- do not promote user claims into externally verified facts.

For tool results:
- preserve observations and reported outcomes;
- distinguish returned observations from conclusions that would require further
  reasoning.

For executable code or tool-use text:
- decompose the semantic action, not irrelevant Python syntax;
- preserve the tool/action name, known arguments, intended effect, and explicit
  preconditions;
- preserve dependencies between actions when one action supplies information
  needed to determine another.

LOCAL RELATION HINTS ARE OPTIONAL
---------------------------------
For composite statements, ``local_relations`` are OPTIONAL hints only. Return one
only when the immediate parent explicitly and unambiguously states that semantic
relation between two returned children.

Use zero-based child indices. ``source_child_index`` is the relation source and
``target_child_index`` is the relation target. ``evidence_text`` should preferably
be a verbatim contiguous excerpt from the parent that supports the relation.

Important boundaries:
- Do NOT use local_relations as a substitute for clean semantic operands. The
  dedicated normalization pass owns Boolean/simple-conditional structure and can
  also recover source-explicit temporal/prerequisite relations.
- Do NOT add PRECEDES solely because a word such as "first" appears. Emit PRECEDES
  only when the immediate source makes both ordered endpoints unambiguous.
- Do NOT add REQUIRES/QUALIFIES merely to encode a conditional whose wording is
  already preserved for the logic pass.
- Do NOT reverse an establishment procedure into a prerequisite.
- If uncertain whether the relation is explicit, omit it. The later relation
  linker can infer semantic relations after node construction.
- Set origin="source_explicit" for any hint you do emit.

The absence of local_relations MUST NOT cause you to add, remove, merge, or rewrite
semantic children. Proposition decomposition comes first.

FINAL SELF-CHECK BEFORE RETURNING
---------------------------------
If kind="composite", ask yourself:
1. Did every independently operative semantic operand/clause survive as a child?
   Relational/Boolean connectors between those operands may be deferred to the
   normalization pass.
2. Did every procedure or "how to establish/check X" clause survive?
3. Did I keep relation words BETWEEN operands out of standalone children while
   preserving temporal/restrictive wording that belongs INSIDE an operand?
4. Did every qualifier or threshold internal to a proposition survive?
5. Did every exception/override operand survive, even if its formal connection is
   deferred to the logic pass?
6. Is every child directly supported by source_text from the parent?
7. Did I avoid inventing child-to-child logical or temporal relations merely to
   make the decomposition look formally complete?
8. For every local relation I did return, is it explicit and unambiguous in the
   immediate parent text?

If a source meaning is missing, revise the children. Do NOT invent a local relation
as a substitute for missing semantic content.

routing_text must be a short retrieval-oriented description of the current
statement. It is not a substitute for content and must not contain important
semantics that are absent from content.
""".strip()


_CHUNK_SEMANTIC_AUDIT_SYSTEM_PROMPT = """
You audit ONE fully constructed semantic-decomposition chunk after recursive
construction is complete.

The decomposition itself was produced from deterministic dependency-parser
scaffolds plus an OSS-120B semantic decomposer. You are NOT repairing the chunk.
You return only PASS/FAIL diagnostics. If the chunk is incomplete, the graph build
will stop rather than enter a repair loop.

You receive:
- PRIMARY_SOURCE: the exact authoritative source slice for this chunk;
- CONTEXT_SOURCE_TEXTS: exact external source excerpts used only when needed to
  interpret the primary slice;
- CONTEXTUALIZED_INPUT: the derived self-contained text actually decomposed;
- CHUNK_STRUCTURE: every node, hierarchy edge, source-explicit relation, and local
  logic object produced while building this chunk, before cross-chunk slot binding.

Judge semantic fidelity of the completed subtree as a whole, not each recursive
LLM call in isolation.

Check for:
- missing_semantics: an operative source proposition/condition/procedure/exception
  is absent from the completed subtree;
- distorted_semantics: a represented proposition changes actor, modality,
  polarity, scope, qualifier, threshold, temporal restriction, or other
  satisfaction-changing meaning;
- unsupported_semantics: a node asserts meaning not supported by the authoritative
  primary/context sources;
- missing_relation: an explicit source relation/connective that should have been
  represented by the source-normalization layer is absent;
- incorrect_relation: a stored source relation has wrong endpoints or direction;
- logic_error: Boolean/conditional/cardinality structure materially changes the
  source meaning;
- atomicity: a final atomic leaf still contains multiple independently operative
  semantic propositions, or a composite node has no meaningful decomposition.

Do NOT fail merely because parser guidance was imperfect, because routing_text is
brief, because S/P/O payloads are not present yet, or because broad inferred
lateral relations have not been linked. S/P/O extraction and broad relation linking
happen later. Cross-chunk slot identity binding also happens later.

Return complete=true with issues=[] only when the completed chunk preserves the
source semantics safely. Otherwise return complete=false and precise issue objects.
""".strip()


_ATOMIC_CLASSIFICATION_AUDIT_SYSTEM_PROMPT = """
Audit only whether one proposed ATOMIC semantic statement is truly atomic.

The authoritative graph node content remains the exact SOURCE text. Decide only:

classification_valid
  Is SOURCE one semantically indivisible operand in its inherited SEMANTIC_ROLE,
  so kind=atomic is appropriate? A role-bound condition, exception,
  qualification, procedure, or intended action does not need to be a complete
  standalone sentence if it expresses one coherent operand. Return false when
  SOURCE contains multiple independently applicable clauses/procedures/branches
  that need separate semantic children.

Connective-only fragments such as "Before", "After", "If", "Then", "Unless",
or "Requires" are never valid atomic operands. However do not split a coherent
role-bound operand merely because its grammar depends on the parent context.

Do NOT evaluate or comment on PropositionPayload here. Retrieval payloads are
generated only after the complete semantic hierarchy has been built and only for
nodes that remain atomic leaves.

Do not require formal Boolean AST/implication structure here. The separate
logic-normalization pass owns formal structure.
""".strip()


_DECOMPOSITION_AUDIT_SYSTEM_PROMPT = """
You audit exactly one proposed COMPOSITE semantic decomposition for LOSSLESS
CHILD COVERAGE. You are also the ONLY semantic repair step.

You receive:
- the exact original SOURCE statement;
- one proposed LocalDecompositionDecision.

Return exactly one DecompositionAuditResult with one of two actions:

1. action="pass"
   Use this only when the proposed decomposition is semantically lossless and
   contains no unsupported/duplicate child meaning or unsafe local relation.
   corrected_decision must be null.

2. action="corrected"
   If anything is missing, weakened, duplicated, unsupported, or incorrectly
   scoped, return the COMPLETE corrected LocalDecompositionDecision in
   corrected_decision. Do the correction yourself in this same response.

CORRECTION RULES
----------------
- Make the minimum semantic edits needed to make the decomposition lossless.
- You MAY replace an existing child when a qualifier, modality, condition,
  actor, restriction, or scope was lost. Prefer replacement over adding a
  near-duplicate child.
- You MAY delete unsupported or duplicate children.
- You MAY add a genuinely missing child.
- Preserve already-correct children unchanged whenever possible.
- Keep kind="composite".
- Return the whole corrected decision, not a patch, issue list, or prose repair
  instruction.
- Do not create PropositionPayload/S-P-O fields. Those are generated later.
- Child content must be meaningful semantic propositions, not connective-only
  fragments such as "Before", "After", "If", "Then", "Unless",
  "Requires", "And", or "Or".
- source_text should remain grounded in the immediate SOURCE; minor formatting
  normalization is acceptable.
- routing_text should remain a concise retrieval-oriented rendering of the same
  current statement.

SEMANTIC COVERAGE
-----------------
Preserve every independently operative source meaning, especially:
- requirements, prohibitions, and permissions;
- actors/subjects whose identity changes the proposition;
- prerequisites/conditions and postconditions as semantic operands;
- procedures or prescribed methods for establishing/checking a state;
- exception/override/fallback operands and their scope;
- modality, polarity, attribution, uncertainty, quantifiers, thresholds, counts,
  durations, and restrictive qualifiers such as "only", "below", "already";
- temporal/restrictive wording that belongs INSIDE one proposition, such as
  "before 5 PM" or "until closed".

FORMAL-STRUCTURE RESPONSIBILITY BOUNDARY
----------------------------------------
A later normalization pass receives the unchanged SOURCE and owns relations and
formal connectives between preserved operands. Therefore do NOT reject or modify
an otherwise lossless decomposition merely because child text omits a connector
whose only role is to connect separate operands, including:
- AND / OR / NOT grouping;
- AT_LEAST / AT_MOST / EXACTLY cardinality;
- IF / WHEN / UNLESS / ONLY-IF / OTHERWISE condition -> effect structure;
- prerequisite direction such as REQUIRES or QUALIFIES;
- temporal/order edges such as PRECEDES.

Examples:
- SOURCE: "If A, then B". Clean children A and B are sufficient; later logic
  normalization reconstructs A -> B.
- SOURCE: "First A, then B". Clean children A and B are sufficient; later
  normalization reconstructs PRECEDES.
- SOURCE: "Y requires P". Clean children Y and P are sufficient; later
  normalization reconstructs REQUIRES.

But connective-like wording that is internal to one proposition MUST remain.
For example "only authorized users", "at least 30 days", "before 5 PM",
or a condition that semantically scopes the child itself must not be weakened.

DUPLICATES AND SCOPE
--------------------
Do not solve a missing qualifier by adding a second near-duplicate child. Edit
the existing child so the complete proposition carries its proper scope.
If two children repeat the same semantic proposition from the same source
occurrence, return a corrected decision that removes/merges the redundancy.
Repeated wording from distinct source occurrences may remain distinct when its
referent or inherited scope differs.

LOCAL RELATIONS
---------------
local_relations are optional hints. Their absence is not a failure. If present,
keep only relations explicitly and unambiguously supported by the immediate
SOURCE. Remove any invented relation in the corrected decision.

Do not return missing_semantics, unsupported_children, or instructions for a
second repair model. Either PASS the proposed decomposition or RETURN THE FULL
CORRECTED DECOMPOSITION.
""".strip()
