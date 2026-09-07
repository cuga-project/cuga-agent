from __future__ import annotations

import ast
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import stanza
import torch
from mlx_lm import generate, load

try:
    from mlx_lm import batch_generate
except ImportError:  # Older mlx-lm; keep a safe serial fallback.
    batch_generate = None


POLICY_TITLE = "# Rho-Bank Customer Service Policy"

CONSTRUCTION_CACHE_SCHEMA = 2
CONSTRUCTION_PIPELINE_VERSION = "stanza_dedisco_v12_retrieval_only_asymmetric_guard"
DECONTEXT_CACHE_SCHEMA = 1
DECONTEXT_PROMPT_VERSION = "native_t5_3b_decontext_full_block_v2"
DEFAULT_DECONTEXT_MODEL = "gaotianyu1350/decontextualizer-t5-3b"

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "models",
    "__pycache__", ".mypy_cache", ".ruff_cache",
}

SEARCH_EXTENSIONS = {
    ".json", ".jsonl", ".py", ".txt", ".md", ".yaml", ".yml",
}

VALID_LABELS = {
    "alternation", "attribution", "causal", "comment",
    "concession", "condition", "conjunction", "contrast",
    "elaboration", "explanation", "frame", "mode",
    "organization", "purpose", "query", "reformulation",
    "temporal",
}


SUPPRESSION_STATS = Counter()
MERGED_SPLITS = []


@dataclass
class SourceBlock:
    index: int
    kind: str
    heading: str | None
    text: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class Constituent:
    label: str
    start: int
    end: int
    children: list["Constituent"] = field(default_factory=list)


@dataclass
class SuppressedSplit:
    block_index: int
    block_kind: str
    heading: str | None
    sentence_index: int
    sentence: str
    syntax_type: str
    stanza_deprel: str
    marker: str | None
    unit1: str
    unit2: str
    unit1_issue: str | None
    unit2_issue: str | None
    retained_text: str
    retained_resolution: str = "source_sentence"
    retained_parent_pair_id: int | None = None


@dataclass
class RelationCandidate:
    pair_id: int

    block_index: int
    block_kind: str
    heading: str | None

    sentence_index: int
    sentence: str
    constituency: str

    syntax_type: str
    stanza_deprel: str
    marker: str | None

    unit1_raw: str
    unit2_raw: str
    unit1: str
    unit2: str

    reconstruction_method: str
    reconstruction_notes: list[str]

    deterministic_relation: str | None
    deterministic_note: str | None
    deterministic_direction: str | None

    dedisco_relation: str | None = None
    dedisco_valid: bool | None = None
    dedisco_latency_s: float | None = None

    chosen_relation: str | None = None
    chosen_by: str | None = None

    unit1_final: str | None = None
    unit2_final: str | None = None

    unit1_decontext_changed: bool | None = None
    unit2_decontext_changed: bool | None = None

    unit1_decontext_cache_hit: bool | None = None
    unit2_decontext_cache_hit: bool | None = None

    unit1_decontext_latency_s: float | None = None
    unit2_decontext_latency_s: float | None = None

    unit1_decontext_status: str | None = None
    unit2_decontext_status: str | None = None

    unit1_decontext_raw_output: str | None = None
    unit2_decontext_raw_output: str | None = None


# ============================================================================
# MARKDOWN / DOCUMENT STRUCTURE
# ============================================================================

HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$")
HORIZONTAL_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+?)\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")


def parse_table_row(line: str):
    if not TABLE_ROW_RE.match(line):
        return None

    raw = line.strip().strip("|")
    cells = [cell.strip() for cell in raw.split("|")]

    if len(cells) < 2:
        return None

    return cells


def is_table_separator(cells):
    if not cells:
        return False

    normalized = [re.sub(r"\s+", "", cell) for cell in cells]

    return all(
        bool(TABLE_SEPARATOR_CELL_RE.fullmatch(cell))
        for cell in normalized
    )


def normalize_header_name(value: str):
    return re.sub(r"\s+", " ", value.strip()).strip().lower()


def table_row_to_block(headers, cells):
    """
    V6 table handling.

    Column names are structural metadata and are never injected into the
    sentence passed to Stanza.

    For the Rho-Bank reason-code tables:
        | Reason Code | When to Use |
        | foo         | Customer ... |

    Stanza receives only:
        Customer ...

    while metadata preserves the other cell(s) and source column.
    """

    metadata: dict[str, str] = {}

    if headers and len(headers) == len(cells):
        mapping = {
            header.strip(): cell.strip()
            for header, cell in zip(headers, cells)
            if header.strip() and cell.strip()
        }

        normalized = {
            normalize_header_name(k): (k, v)
            for k, v in mapping.items()
        }

        if "when to use" in normalized:
            original_header, prose = normalized["when to use"]

            metadata = {
                k: v
                for k, v in mapping.items()
                if k != original_header
            }
            metadata["source_column"] = original_header

            return prose.strip(), metadata

        prose_candidates = []

        for header, cell in mapping.items():
            score = (
                len(cell)
                + 40 * int(bool(re.search(r"[.!?]", cell)))
                + 20 * int(len(cell.split()) >= 5)
            )
            prose_candidates.append((score, header, cell))

        if prose_candidates:
            _, prose_header, prose = max(prose_candidates)

            metadata = {
                k: v
                for k, v in mapping.items()
                if k != prose_header
            }
            metadata["source_column"] = prose_header

            return prose.strip(), metadata

    prose = "; ".join(cell.strip() for cell in cells if cell.strip()).strip()
    return prose, metadata


FORMATTING_LABELS = {
    "important",
    "note",
    "warning",
    "caution",
    "remember",
    "tip",
}

# Standalone Markdown/document labels are structural, not propositions.  They
# should normally disappear during source-block construction rather than become
# atomic graph leaves.  The atomic classifier below repeats this test
# defensively in case one reaches recursion through a different path.
STRUCTURAL_TERMINAL_LABELS = {
    "argument", "arguments", "example", "examples", "field", "fields",
    "input", "inputs", "note", "notes", "output", "outputs",
    "parameter", "parameters", "requirement", "requirements",
    "response", "responses", "result", "results", "return", "returns",
    "schema", "schemas", "tool", "tools", "variable", "variables",
}


def structural_terminal_artifact_issue(text: str) -> str | None:
    raw = text.strip()
    if not raw:
        return "empty"

    # Remove common Markdown emphasis/code decoration before deciding whether
    # the remaining text is only punctuation or a section label.
    undecorated = re.sub(r"[*_`]+", "", raw).strip()
    if not re.search(r"[A-Za-z0-9]", undecorated):
        return "punctuation_only"

    normalized = re.sub(r"\s+", " ", undecorated).strip()
    if normalized.endswith(":"):
        label = normalized[:-1].strip().casefold()
        if label in STRUCTURAL_TERMINAL_LABELS:
            return "standalone_structural_label"

    return None


def strip_formatting_label(text: str):
    """
    Remove leading editorial labels such as IMPORTANT:, NOTE:, WARNING:.
    The label is retained as metadata rather than sent to Stanza as prose.
    """

    match = re.match(
        r"^\s*(IMPORTANT|NOTE|WARNING|CAUTION|REMEMBER|TIP)\s*:\s*(.+)$",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return text.strip(), {}

    return (
        match.group(2).strip(),
        {"formatting_label": match.group(1).upper()},
    )


def build_source_blocks(policy: str):
    """Build loss-aware semantic source blocks, including Markdown headings.

    Markdown headings are semantic scope anchors for the memory graph.  They are
    therefore emitted as blocks instead of being retained only as transient
    parser metadata.  Horizontal rules and the ``<required_documents>`` wrapper
    reset document scope so a document/card title can be distinguished from its
    later section headings even when the source uses the same Markdown level for
    both.

    Table headers and editorial prefixes remain structural metadata exactly as
    before; this change is deliberately limited to headings/titles.
    """

    lines = policy.replace("\r\n", "\n").split("\n")

    blocks: list[SourceBlock] = []
    heading: str | None = None
    active_heading_block_index: int | None = None
    document_scope_heading: str | None = None
    document_scope_block_index: int | None = None
    awaiting_document_scope = True
    current_lines: list[str] = []
    current_kind: str | None = None

    def append_block(
        kind: str,
        text: str,
        metadata: dict[str, str] | None = None,
        *,
        preserve_structural: bool = False,
    ) -> int | None:
        text = text.strip()
        metadata = dict(metadata or {})

        if not text:
            return None

        text, label_metadata = strip_formatting_label(text)
        metadata.update(label_metadata)

        if not text:
            return None

        structural_issue = structural_terminal_artifact_issue(text)
        if structural_issue is not None and not preserve_structural:
            # Punctuation debris and formatting-only labels remain excluded.
            # Markdown headings take the explicit preserve_structural path above
            # because they carry entity/section scope for retrieval.
            return None

        if active_heading_block_index is not None:
            metadata.setdefault(
                "active_heading_block_index",
                str(active_heading_block_index),
            )
        if heading:
            metadata.setdefault("active_heading", heading)
        if document_scope_block_index is not None:
            metadata.setdefault(
                "document_scope_block_index",
                str(document_scope_block_index),
            )
        if document_scope_heading:
            metadata.setdefault("document_scope_heading", document_scope_heading)

        block_index = len(blocks)
        blocks.append(
            SourceBlock(
                index=block_index,
                kind=kind,
                heading=heading,
                text=text,
                metadata=metadata,
            )
        )
        return block_index

    def flush():
        nonlocal current_lines, current_kind

        if not current_lines:
            return

        text = " ".join(
            line.strip()
            for line in current_lines
            if line.strip()
        ).strip()

        if text:
            append_block(current_kind or "paragraph", text)

        current_lines = []
        current_kind = None

    i = 0

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            flush()
            i += 1
            continue

        # Required-document wrappers and horizontal rules are document-scope
        # boundaries, not semantic leaves themselves.
        if stripped.casefold() in {"<required_documents>", "</required_documents>"}:
            flush()
            heading = None
            active_heading_block_index = None
            document_scope_heading = None
            document_scope_block_index = None
            awaiting_document_scope = True
            i += 1
            continue

        if HORIZONTAL_RULE_RE.match(line):
            flush()
            heading = None
            active_heading_block_index = None
            document_scope_heading = None
            document_scope_block_index = None
            awaiting_document_scope = True
            i += 1
            continue

        heading_match = HEADING_RE.match(line)

        if heading_match:
            flush()
            heading_text = heading_match.group(2).strip()
            heading_level = len(heading_match.group(1))
            is_document_scope = (
                awaiting_document_scope
                or document_scope_block_index is None
            )

            # Make the new heading current before constructing its block so
            # descendants can resolve exact source scope consistently.
            heading = heading_text
            metadata = {
                "source_unit_kind": "markdown_heading",
                "heading_level": str(heading_level),
                "heading_scope_role": (
                    "document_scope" if is_document_scope else "section"
                ),
            }
            if not is_document_scope and document_scope_block_index is not None:
                metadata["document_scope_block_index"] = str(
                    document_scope_block_index
                )
            if not is_document_scope and document_scope_heading:
                metadata["document_scope_heading"] = document_scope_heading

            block_index = append_block(
                "heading",
                heading_text,
                metadata=metadata,
                preserve_structural=True,
            )
            if block_index is None:
                raise RuntimeError("Markdown heading unexpectedly produced no source block")

            active_heading_block_index = block_index
            blocks[block_index].metadata.update(
                {
                    "active_heading_block_index": str(block_index),
                    "active_heading": heading_text,
                }
            )
            if is_document_scope:
                document_scope_block_index = block_index
                document_scope_heading = heading_text
                awaiting_document_scope = False
                blocks[block_index].metadata.update(
                    {
                        "document_scope_block_index": str(block_index),
                        "document_scope_heading": heading_text,
                    }
                )
            i += 1
            continue

        first_cells = parse_table_row(line)

        if first_cells is not None:
            flush()
            table_rows = []

            while i < len(lines):
                cells = parse_table_row(lines[i].rstrip())

                if cells is None:
                    break

                table_rows.append(cells)
                i += 1

            headers = None
            data_rows = table_rows

            if (
                len(table_rows) >= 2
                and is_table_separator(table_rows[1])
            ):
                headers = table_rows[0]
                data_rows = table_rows[2:]
            else:
                data_rows = [
                    row
                    for row in table_rows
                    if not is_table_separator(row)
                ]

            for cells in data_rows:
                row_text, row_metadata = table_row_to_block(
                    headers,
                    cells,
                )

                if row_text:
                    append_block(
                        "table_row",
                        row_text,
                        metadata=row_metadata,
                    )

            continue

        list_match = LIST_RE.match(line)

        if list_match:
            flush()
            current_kind = "list_item"
            current_lines = [list_match.group(1)]
            i += 1
            continue

        if current_lines:
            current_lines.append(line)
        else:
            current_kind = "paragraph"
            current_lines = [line]

        i += 1

    flush()
    return blocks


# ============================================================================
# CONSTITUENCY TREE
# ============================================================================

def parse_penn_tree(tree_text: str):
    """
    Parse Stanza's Penn-style constituency string and annotate every
    constituent with [start, end) leaf offsets.
    """

    tokens = re.findall(r"\(|\)|[^\s()]+", tree_text)
    i = 0
    leaf_index = 0

    def parse_node():
        nonlocal i, leaf_index

        if tokens[i] != "(":
            raise ValueError("Expected '('")

        i += 1
        label = tokens[i]
        i += 1

        start = leaf_index
        children = []

        while i < len(tokens) and tokens[i] != ")":
            if tokens[i] == "(":
                children.append(parse_node())
            else:
                leaf_index += 1
                i += 1

        if i >= len(tokens):
            raise ValueError("Malformed constituency tree")

        i += 1

        return Constituent(
            label=label,
            start=start,
            end=leaf_index,
            children=children,
        )

    return parse_node()


def flatten_constituents(root: Constituent):
    result = [root]

    for child in root.children:
        result.extend(flatten_constituents(child))

    return result


def node_contains_word(node: Constituent, word_id: int):
    idx = word_id - 1
    return node.start <= idx < node.end


def ids_from_constituent(node: Constituent | None):
    if node is None:
        return set()

    return set(range(node.start + 1, node.end + 1))


def smallest_enclosing_clause(
    constituents,
    root_word_id: int,
    exclude_word_id: int | None = None,
):
    """
    V5 FIX:
    For subordinate propositions prefer a full S/SINV/SQ constituent.
    Do NOT choose the smallest VP merely because it contains the predicate.

    This preserves:
      "you cannot find relevant information"
      "they do"
      "the user asks ..."
    instead of:
      "find relevant information"
      "do"
      "asks ..."
    """

    clause_labels = {"S", "SINV", "SQ", "SBARQ"}
    root_idx = root_word_id - 1
    exclude_idx = exclude_word_id - 1 if exclude_word_id is not None else None

    choices = []

    for node in constituents:
        if node.label not in clause_labels:
            continue

        if not (node.start <= root_idx < node.end):
            continue

        if exclude_idx is not None and node.start <= exclude_idx < node.end:
            continue

        choices.append((node.end - node.start, node))

    if not choices:
        return None

    choices.sort(key=lambda x: x[0])
    return choices[0][1]


def smallest_phrase_for_root(
    constituents,
    root_word_id: int,
    labels=("VP", "ADJP", "S"),
    exclude_word_id: int | None = None,
):
    root_idx = root_word_id - 1
    exclude_idx = exclude_word_id - 1 if exclude_word_id is not None else None

    choices = []

    for node in constituents:
        if node.label not in labels:
            continue

        if not (node.start <= root_idx < node.end):
            continue

        if exclude_idx is not None and node.start <= exclude_idx < node.end:
            continue

        choices.append((node.end - node.start, node))

    if not choices:
        return None

    choices.sort(key=lambda x: x[0])
    return choices[0][1]


def coordination_container(root: Constituent, head_id: int, conj_id: int):
    """
    Find the LOWEST constituent whose children separate the two coordinated
    predicate/clause roots into different branches.

    For:
        Do not [make up] or [assume] the current time

    this should resolve to the coordination VP whose outside material is:
        prefix: Do not
        suffix: the current time
    """

    head_idx = head_id - 1
    conj_idx = conj_id - 1

    def contains(node, idx):
        return node.start <= idx < node.end

    best = None

    def visit(node):
        nonlocal best

        if not (contains(node, head_idx) and contains(node, conj_idx)):
            return

        head_children = [
            c for c in node.children
            if contains(c, head_idx)
        ]
        conj_children = [
            c for c in node.children
            if contains(c, conj_idx)
        ]

        separated = (
            head_children
            and conj_children
            and head_children[0] is not conj_children[0]
        )

        if separated and node.label in {
            "VP", "S", "SINV", "SQ", "ADJP"
        }:
            # DFS means a deeper valid node will overwrite its ancestor.
            best = (
                node,
                head_children[0],
                conj_children[0],
            )

        for child in node.children:
            if contains(child, head_idx) and contains(child, conj_idx):
                visit(child)

    visit(root)
    return best



# ============================================================================
# V7 STRUCTURAL HELPERS
# ============================================================================

def constituent_contains_id(node: Constituent, word_id: int):
    idx = word_id - 1
    return node.start <= idx < node.end


def immediate_child_containing(node: Constituent, word_id: int):
    for child in node.children:
        if constituent_contains_id(child, word_id):
            return child
    return None


def proposition_like_child(node: Constituent):
    return node.label in {"S", "SINV", "SQ", "VP"}


def find_nary_clause_group(
    root: Constituent,
    head_id: int,
    dep_id: int,
):
    """
    Detect comma/conjunction sequences such as:

        [S A], [S B], [S C], and [VP D]

    before the ordinary binary coordination reconstruction gets a chance to
    copy unrelated sibling clauses into both operands.

    Returns the LOWEST qualifying container plus the distinct immediate
    proposition-like child branches containing head_id and dep_id.
    """

    best = None

    def visit(node):
        nonlocal best

        if not (
            constituent_contains_id(node, head_id)
            and constituent_contains_id(node, dep_id)
        ):
            return

        proposition_children = [
            child
            for child in node.children
            if proposition_like_child(child)
        ]

        if len(proposition_children) >= 3:
            head_branch = next(
                (
                    child
                    for child in proposition_children
                    if constituent_contains_id(child, head_id)
                ),
                None,
            )
            dep_branch = next(
                (
                    child
                    for child in proposition_children
                    if constituent_contains_id(child, dep_id)
                ),
                None,
            )

            if (
                head_branch is not None
                and dep_branch is not None
                and head_branch is not dep_branch
            ):
                best = (
                    node,
                    head_branch,
                    dep_branch,
                    proposition_children,
                )

        for child in node.children:
            if (
                constituent_contains_id(child, head_id)
                and constituent_contains_id(child, dep_id)
            ):
                visit(child)

    visit(root)
    return best


def coordinated_sbar_complement(
    root: Constituent,
    head_id: int,
    dep_id: int,
):
    """
    Detect coordinated WH/SBAR complements under one matrix predicate, e.g.:

        Explain [what the tool does] and [how to use it],
                and [what arguments to provide]

    These are semantic objects of the same matrix predicate, not independent
    graph propositions at this stage.
    """

    best = None

    def visit(node):
        nonlocal best

        if not (
            constituent_contains_id(node, head_id)
            and constituent_contains_id(node, dep_id)
        ):
            return

        if node.label == "SBAR":
            sbar_children = [
                child for child in node.children
                if child.label == "SBAR"
            ]

            if len(sbar_children) >= 2:
                head_branch = next(
                    (
                        child
                        for child in sbar_children
                        if constituent_contains_id(child, head_id)
                    ),
                    None,
                )
                dep_branch = next(
                    (
                        child
                        for child in sbar_children
                        if constituent_contains_id(child, dep_id)
                    ),
                    None,
                )

                if (
                    head_branch is not None
                    and dep_branch is not None
                    and head_branch is not dep_branch
                ):
                    best = node

        for child in node.children:
            if (
                constituent_contains_id(child, head_id)
                and constituent_contains_id(child, dep_id)
            ):
                visit(child)

    visit(root)
    return best


def is_finite_predicate(word):
    feats = word.feats or ""
    xpos = word.xpos or ""

    if "VerbForm=Fin" in feats:
        return True

    if xpos in {
        "VBD", "VBP", "VBZ",
        "MD",
    }:
        return True

    return False


def is_independently_finite_conj(sentence, dep):
    """
    V8 distinction:

    Independent finite conjunct:
        customer is not frustrated, just prefers human
        -> "prefers" owns its own finite predicate.
        -> inherit subject only; do not inherit "is not".

    Bare coordinated imperative/base-form predicate:
        Do not make up or assume the current time
        -> "assume" is VB and depends on the same imperative scope.
        -> inherit "Do not".

    Strong evidence that the conjunct is independently finite:
      * inflected finite POS (VBD/VBP/VBZ/MD), or
      * it has its own subject, or
      * it has its own auxiliary/copula.

    Bare VB alone is NOT treated as independently finite.
    """

    xpos = dep.xpos or ""

    if xpos in {"VBD", "VBP", "VBZ", "MD"}:
        return True

    for word in sentence.words:
        if word.head != dep.id:
            continue

        base = dep_base(word)

        if base in {"nsubj", "csubj"}:
            return True

        if base in {"aux", "cop"}:
            return True

    return False


def temporal_nonfinite_advcl(dep, marker):
    if marker not in {"before", "after", "while", "until", "once"}:
        return False

    feats = dep.feats or ""
    xpos = dep.xpos or ""

    return (
        "VerbForm=Ger" in feats
        or "VerbForm=Part" in feats
        or xpos in {"VBG", "VBN"}
    )


def logic_scope_warning_for_advcl(
    sentence,
    dep,
    marker,
):
    """
    V7 deliberately does NOT solve semantic AST scope here.

    Flag cases such as:
        Do this only if A, and B

    where the conditional's governing predicate also has a coordinated sibling.
    The later logic parser should receive the original source span + syntax.
    """

    if marker not in {"if", "unless"}:
        return None

    for word in sentence.words:
        if (
            dep_base(word) == "conj"
            and word.head == dep.head
        ):
            return (
                "LOGIC_SCOPE: conditional governor also has coordinated "
                "material; preserve original sentence for later AST composition"
            )

    return None


# ============================================================================
# DEPENDENCY / TEXT HELPERS
# ============================================================================

def dep_base(word):
    return word.deprel.split(":")[0]


def build_children(sentence):
    children = {w.id: [] for w in sentence.words}

    for w in sentence.words:
        if w.head != 0:
            children.setdefault(w.head, []).append(w.id)

    return children


def subtree_ids(root_id, children):
    result = {root_id}
    stack = [root_id]

    while stack:
        current = stack.pop()

        for child in children.get(current, []):
            if child in result:
                continue

            result.add(child)
            stack.append(child)

    return result


def clean_text(text):
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?;:%])", r"\1", text)
    text = re.sub(r"([\(\[\{])\s+", r"\1", text)
    text = re.sub(r"\s+([\)\]\}])", r"\1", text)
    text = re.sub(r"\b(\w+)\s+n['’]t\b", r"\1n't", text)
    text = re.sub(
        r"\b(\w+)\s+['’](s|re|ve|ll|d|m)\b",
        r"\1'\2",
        text,
    )
    return text.strip(" ,;:-")


def render_ids(sentence, ids, remove_ids=None):
    remove_ids = remove_ids or set()
    words = {w.id: w for w in sentence.words}

    values = []

    for wid in sorted(ids):
        if wid in remove_ids or wid not in words:
            continue

        values.append(words[wid].text)

    return clean_text(" ".join(values))


def _strip_code_ticks(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"`([^`]*)`", r"\1", normalized).strip()


def classify_terminal_for_retrieval(text: str) -> tuple[str, list[str]]:
    """Classify a final leaf without discarding retrieval-useful literals.

    Natural-language propositions remain ``proposition``.  Literal/path/code
    examples become ``retrieval_only:*``: they retain content/routing/embedding
    signals but do not require proposition-style logic or S/P/O parsing.
    """
    raw = text.strip()
    normalized = re.sub(r"\s+", " ", raw).strip()

    structural_issue = structural_terminal_artifact_issue(normalized)
    if structural_issue is not None:
        return "structural_artifact", [structural_issue]

    stripped = _strip_code_ticks(normalized)
    low = normalized.casefold()

    path_token = r"(?:\.?\.?/)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+"
    pieces = [piece.strip() for piece in stripped.split(",") if piece.strip()]
    if pieces and all(re.fullmatch(path_token, piece) for piece in pieces):
        return "retrieval_only:path_list", ["path_list_only"]

    if re.fullmatch(r"`[^`]+`(?:\s*,\s*`[^`]+`)*[.!]?", normalized):
        return "retrieval_only:code_literal_list", ["code_literal_list"]

    if re.fullmatch(
        r"(?:https?://\S+|[A-Za-z0-9_.-]+\.(?:json|xlsx|csv|txt|md|py))",
        stripped,
    ):
        return "retrieval_only:resource_literal", ["resource_literal"]

    # Parser-damaged snippets that are clearly isolated code-call examples.
    # Pure calls are retrieval-only. Mixed short snippets are downgraded only
    # when they lack ordinary instruction/condition cues, so text such as
    # "Always call foo()" remains a proposition.
    codeish = stripped.strip(" .;:'\"")
    pure_call = bool(
        re.fullmatch(
            r"(?:await\s+)?[A-Za-z_][A-Za-z0-9_.]*\s*\([^)]*\)",
            codeish,
        )
    )
    instruction_cue = bool(
        re.search(
            r"\b(?:if|when|while|because|must|should|can|will|is|are|was|were|"
            r"always|never|call|use|invoke|execute|run|return|write|read|get|"
            r"create|update|delete|send|ask|give|open|close)\b",
            low,
        )
    )
    if pure_call or (
        len(codeish.split()) <= 5
        and re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(", codeish)
        and not instruction_cue
    ):
        return "retrieval_only:code_fragment", ["code_call_fragment"]

    return "proposition", []


def asymmetric_comparative_reconstruction_issue(
    parent_text: str,
    left: str,
    right: str,
) -> tuple[int | None, str | None]:
    """Reject malformed asymmetric comparison reconstructions.

    Shared-argument propagation can be safe for symmetric ``and``/``or``
    coordination, but not for ``rather than`` / ``instead of`` /
    ``as opposed to``.  The observed production failure reconstructed
    ``it directly emitting ...`` from ``execute it directly rather than
    emitting ...``.  In that family we retain the immediate parent instead.
    """
    parent_low = re.sub(r"\s+", " ", parent_text).strip().casefold()
    if not any(
        phrase in parent_low
        for phrase in ("rather than", "instead of", "as opposed to")
    ):
        return None, None

    for index, child in enumerate((left, right)):
        text = re.sub(r"\s+", " ", child).strip()
        low = text.casefold()
        if re.match(
            r"^(?:it|they|he|she|this|that)\s+(?:\w+ly\s+){0,3}\w+ing\b",
            low,
        ):
            return index, (
                "asymmetric comparative produced pronoun+nonfinite child: "
                f"{text!r}"
            )
        if low.startswith(("rather than ", "instead of ", "as opposed to ")):
            return index, f"stranded asymmetric comparative child: {text!r}"

    return None, None


def semantic_closure_issue(text: str):
    """Return a conservative reason when a proposed unit is not useful alone.

    This gate does NOT attempt coreference resolution or rewriting.  Its job is
    only to stop Stanza from materializing obviously incomplete retrieval units.
    When either side of a proposed split fails, the relation pair is suppressed.
    After all sentence-level proposals are known, the bad unit is resolved to
    the smallest accepted enclosing constituent when one exists; only otherwise
    do we retain the full source sentence.

    The rules are intentionally narrow and target structures observed in the
    Rho policy, such as:
      - "they do"                       (pro-verb ellipsis)
      - "you absolutely have to"       (missing infinitival complement)
      - "Do this" / "using it"         (deictic-only argument)
      - "processing" / "calling"       (single-word subordinate fragment)
      - "you can unlock"               (short modal predicate with shared object)

    Longer clauses are left alone even when they contain pronouns.  For example,
    "Do this only if you absolutely have to" remains eligible because it still
    carries the operative rule rather than being a bare ellipsis fragment.
    """

    normalized = clean_text(text).strip(" .!?;:").lower()
    tokens = re.findall(r"[a-zA-Z_]+(?:'[a-z]+)?", normalized)

    if not tokens:
        return "no lexical content"

    # Bare subordinate actions such as "processing", "calling", "proceeding",
    # and "searching" are too underspecified to become retrieval nodes.
    if len(tokens) == 1:
        return "single-word fragment"

    # Classic pro-verb ellipsis.  The semantic content lives in prior context.
    if re.fullmatch(
        r"(?:they|he|she|we|you|it|this|that|these|those)\s+"
        r"(?:do|does|did)",
        normalized,
    ):
        return "pro-verb ellipsis"

    # Elided infinitival complement: "you absolutely have to".  Limit this to
    # short units so a useful rule such as "Do this only if you absolutely have
    # to" is not rejected.
    if (
        len(tokens) <= 5
        and re.search(
            r"\b(?:have|has|had|need|needs|needed|want|wants|wanted|ought)\s+to$",
            normalized,
        )
    ):
        return "stranded infinitival complement"

    # An action whose only argument is a demonstrative/pronoun is normally the
    # exact fragment we want to merge back rather than rewrite generatively.
    if re.fullmatch(
        r"(?:do|does|did|doing|use|uses|used|using)\s+"
        r"(?:it|this|that|them|these|those)",
        normalized,
    ):
        return "deictic/pronominal-only argument"

    # Coordinated predicates sometimes inherit an object that Stanza leaves in
    # the sibling branch: "you can unlock", "you can use", "you couldn't find".
    # Reject only very short modal predicates; clauses with an expressed object
    # ("you can help them") continue to pass.
    if len(tokens) <= 4 and tokens[0] in {
        "i", "you", "we", "they", "he", "she", "it",
    }:
        modal_tokens = {
            "can", "cannot", "can't", "could", "couldn't", "may", "might",
            "must", "should", "shouldn't", "would", "wouldn't", "will", "won't",
        }
        has_modal = any(token in modal_tokens for token in tokens[1:-1])
        if has_modal:
            final = tokens[-1]
            expressed_pronominal_object = final in {
                "it", "them", "him", "her", "this", "that", "these", "those",
                "me", "us", "you",
            }
            copular = any(token in {"are", "is", "was", "were", "be"} for token in tokens)
            if not expressed_pronominal_object and not copular:
                return "short modal predicate lacks expressed argument"

    # Complementizers are a strong signal that the returned text is a dependent
    # complement rather than a standalone proposition.
    if tokens[0] in {"that", "whether"}:
        return "subordinate complement fragment"

    return None


def suppress_if_reconstruction_invalid(
    *,
    sentence,
    block: SourceBlock,
    global_sentence_index: int,
    syntax_type: str,
    stanza_deprel: str,
    marker: str | None,
    unit1: str,
    unit2: str,
    reconstruction_notes: list[str],
):
    """Suppress a split when deterministic reconstruction is structurally unsafe.

    Unlike semantic closure, this catches cases where the source material is
    meaningful but our attempt to redistribute shared coordination material is
    not trustworthy.  The caller then falls back hierarchically to an accepted
    enclosing unit, or to the original source sentence when no such parent exists.
    """

    issue1 = None
    issue2 = None

    for note in reconstruction_notes:
        if note.startswith("RECONSTRUCTION_QUALITY_BOTH:"):
            reason = note.split(":", 1)[1].strip()
            issue1 = f"unsafe reconstruction: {reason}"
            issue2 = f"unsafe reconstruction: {reason}"
        elif note.startswith("RECONSTRUCTION_QUALITY_UNIT1:"):
            reason = note.split(":", 1)[1].strip()
            issue1 = f"unsafe reconstruction: {reason}"
        elif note.startswith("RECONSTRUCTION_QUALITY_UNIT2:"):
            reason = note.split(":", 1)[1].strip()
            issue2 = f"unsafe reconstruction: {reason}"

    if issue1 is None and issue2 is None:
        return False

    SUPPRESSION_STATS["reconstruction_quality"] += 1
    if issue1:
        SUPPRESSION_STATS[f"reconstruction_quality:{issue1}"] += 1
    if issue2 and issue2 != issue1:
        SUPPRESSION_STATS[f"reconstruction_quality:{issue2}"] += 1

    MERGED_SPLITS.append(
        SuppressedSplit(
            block_index=block.index,
            block_kind=block.kind,
            heading=block.heading,
            sentence_index=global_sentence_index,
            sentence=sentence.text,
            syntax_type=syntax_type,
            stanza_deprel=stanza_deprel,
            marker=marker,
            unit1=unit1,
            unit2=unit2,
            unit1_issue=issue1,
            unit2_issue=issue2,
            retained_text=sentence.text,
        )
    )

    return True


def suppress_if_semantically_open(
    *,
    sentence,
    block: SourceBlock,
    global_sentence_index: int,
    syntax_type: str,
    stanza_deprel: str,
    marker: str | None,
    unit1: str,
    unit2: str,
):
    """Record and suppress a split if either proposed unit is semantically open.

    ``retained_text`` is provisional here.  Once every relation proposal for the
    sentence is known, ``resolve_hierarchical_retained_texts`` replaces it with
    the smallest accepted enclosing unit when possible.
    """

    issue1 = semantic_closure_issue(unit1)
    issue2 = semantic_closure_issue(unit2)

    if issue1 is None and issue2 is None:
        return False

    SUPPRESSION_STATS["semantic_closure"] += 1
    if issue1:
        SUPPRESSION_STATS[f"semantic_closure:{issue1}"] += 1
    if issue2:
        SUPPRESSION_STATS[f"semantic_closure:{issue2}"] += 1

    MERGED_SPLITS.append(
        SuppressedSplit(
            block_index=block.index,
            block_kind=block.kind,
            heading=block.heading,
            sentence_index=global_sentence_index,
            sentence=sentence.text,
            syntax_type=syntax_type,
            stanza_deprel=stanza_deprel,
            marker=marker,
            unit1=unit1,
            unit2=unit2,
            unit1_issue=issue1,
            unit2_issue=issue2,
            retained_text=sentence.text,
        )
    )

    return True


def _closure_tokens(text: str):
    return re.findall(
        r"[a-zA-Z_]+(?:'[a-z]+)?",
        clean_text(text).strip(" .!?;:").lower(),
    )


def _contains_token_sequence(container_text: str, child_text: str):
    """Return True when child tokens occur contiguously inside container."""

    container = _closure_tokens(container_text)
    child = _closure_tokens(child_text)

    if not child or len(child) >= len(container):
        return False

    width = len(child)
    return any(
        container[i : i + width] == child
        for i in range(len(container) - width + 1)
    )


def resolve_hierarchical_retained_texts(candidates, suppressed_splits):
    """Resolve rejected nested splits to the smallest accepted parent unit.

    Relation extraction discovers several structures from the same source
    sentence independently.  A bad nested proposal therefore must not force the
    entire sentence to remain unsplit when another accepted relation already
    exposes a larger, semantically closed constituent containing the bad unit.

    Example:
      accepted:   "Do this only if you absolutely have to"
      rejected:   "you absolutely have to"
      fallback:   accepted parent above, NOT the complete source sentence.

    When no accepted endpoint contains every semantically-open side of the
    rejected proposal, the source sentence remains the conservative fallback.
    """

    endpoints_by_sentence = {}

    for candidate in candidates:
        key = (candidate.block_index, candidate.sentence_index)
        bucket = endpoints_by_sentence.setdefault(key, [])

        for text in (candidate.unit1, candidate.unit2):
            normalized = clean_text(text)
            if not normalized:
                continue
            bucket.append((normalized, candidate.pair_id))

    for item in suppressed_splits:
        bad_units = []
        if item.unit1_issue:
            bad_units.append(item.unit1)
        if item.unit2_issue:
            bad_units.append(item.unit2)

        if not bad_units:
            continue

        key = (item.block_index, item.sentence_index)
        possible_parents = []

        for text, pair_id in endpoints_by_sentence.get(key, []):
            if all(
                _contains_token_sequence(text, bad_unit)
                for bad_unit in bad_units
            ):
                possible_parents.append((text, pair_id))

        if possible_parents:
            # The shortest accepted enclosing unit is the closest available
            # approximation to the immediate semantic parent.
            possible_parents.sort(
                key=lambda pair: (
                    len(_closure_tokens(pair[0])),
                    len(pair[0]),
                    pair[1],
                )
            )
            parent_text, parent_pair_id = possible_parents[0]
            item.retained_text = parent_text
            item.retained_resolution = "accepted_parent_unit"
            item.retained_parent_pair_id = parent_pair_id
            SUPPRESSION_STATS[
                "semantic_closure_retained:accepted_parent_unit"
            ] += 1
        else:
            item.retained_text = item.sentence
            item.retained_resolution = "source_sentence"
            item.retained_parent_pair_id = None
            SUPPRESSION_STATS[
                "semantic_closure_retained:source_sentence"
            ] += 1


def build_final_retained_units(candidates, suppressed_splits):
    """Build the deduplicated retrieval-unit set implied by this experiment."""

    records = {}

    def add_unit(
        *,
        block_index,
        block_kind,
        heading,
        sentence_index,
        sentence,
        text,
        origin,
        pair_id=None,
        reason=None,
    ):
        normalized_text = clean_text(text)
        normalized_key = normalized_text.strip(" .!?;:").lower()
        if not normalized_key:
            return

        key = (block_index, sentence_index, normalized_key)
        record = records.get(key)

        if record is None:
            record = {
                "block_index": block_index,
                "block_kind": block_kind,
                "heading": heading,
                "sentence_index": sentence_index,
                "source_sentence": sentence,
                "text": normalized_text,
                "origins": [],
                "pair_ids": [],
                "suppression_reasons": [],
            }
            records[key] = record

        if origin not in record["origins"]:
            record["origins"].append(origin)
        if pair_id is not None and pair_id not in record["pair_ids"]:
            record["pair_ids"].append(pair_id)
        if reason and reason not in record["suppression_reasons"]:
            record["suppression_reasons"].append(reason)

    for candidate in candidates:
        for text in (candidate.unit1, candidate.unit2):
            add_unit(
                block_index=candidate.block_index,
                block_kind=candidate.block_kind,
                heading=candidate.heading,
                sentence_index=candidate.sentence_index,
                sentence=candidate.sentence,
                text=text,
                origin="accepted_relation_endpoint",
                pair_id=candidate.pair_id,
            )

    for item in suppressed_splits:
        reasons = [
            reason
            for reason in (item.unit1_issue, item.unit2_issue)
            if reason
        ]
        add_unit(
            block_index=item.block_index,
            block_kind=item.block_kind,
            heading=item.heading,
            sentence_index=item.sentence_index,
            sentence=item.sentence,
            text=item.retained_text,
            origin=(
                "semantic_closure_parent"
                if item.retained_resolution == "accepted_parent_unit"
                else "semantic_closure_source_sentence"
            ),
            pair_id=item.retained_parent_pair_id,
            reason="; ".join(reasons),
        )

    return sorted(
        records.values(),
        key=lambda record: (
            record["block_index"],
            record["sentence_index"],
            record["text"].lower(),
        ),
    )


def direct_connective(sentence, relation_root_id, dep_name):
    """
    Only direct cc/mark children of the relation root.
    Nested markers are deliberately ignored.
    """

    roots = []

    for w in sentence.words:
        if w.head == relation_root_id and dep_base(w) == dep_name:
            roots.append(w)

    if not roots:
        return None, set()

    components = []
    marker_ids = set()

    for root in sorted(roots, key=lambda x: x.id):
        component = [(root.id, root.text)]
        marker_ids.add(root.id)

        for w in sentence.words:
            if w.head == root.id and dep_base(w) == "fixed":
                component.append((w.id, w.text))
                marker_ids.add(w.id)

        component.sort()
        components.append(" ".join(x[1] for x in component).lower())

    return " ".join(components), marker_ids


def fallback_head_span(sentence, root_id, excluded_subtree):
    children = build_children(sentence)
    return subtree_ids(root_id, children) - excluded_subtree


# ============================================================================
# EXPLICIT RELATIONS + DIRECTION
# ============================================================================


# Strong discourse-level advcl markers admitted in V5.
#
# Bare infinitival/manner/internal adjunct markers such as "to", "by", "as",
# "on", and markerless gerunds are deliberately suppressed at this stage.
# They can still remain inside an atomic proposition; we simply do not create a
# graph relation from every internal advcl dependency.
STRONG_ADVCL_MARKERS = {
    "if",
    "unless",
    "provided",
    "provided that",
    "providing",
    "assuming",
    "assuming that",
    "although",
    "though",
    "even though",
    "whereas",
    "before",
    "after",
    "until",
    "once",
    "because",
    "without",
    "when",
    "while",
    "since",
    "so that",
    "in order that",
}


def should_emit_advcl(marker: str | None):
    """
    Conservative V4 gate.

    Stanza's ``advcl`` label is broader than "independent discourse
    proposition". In policy text it also appears on infinitival purpose,
    manner, and gerundive structures that should usually remain inside one
    proposition.

    V5 therefore emits an advcl relation only when an explicit discourse
    connective is present and belongs to a small high-confidence inventory.
    """

    if not marker:
        return False, "markerless advcl suppressed"

    m = marker.lower().strip()

    if m in STRONG_ADVCL_MARKERS:
        return True, None

    return False, f"weak/predicate-internal advcl marker suppressed: {m}"


def explicit_relation(syntax_type, marker):
    if not marker:
        return None, None, None

    m = marker.lower().strip()

    if syntax_type == "conj":
        if m in {"and", "&"}:
            return (
                "conjunction",
                "explicit coordination: cc=and",
                "symmetric/operator",
            )

        if m in {"or", "nor"}:
            return (
                "alternation",
                f"explicit coordination: cc={m}",
                "symmetric/operator",
            )

        if m == "but":
            return (
                "contrast",
                "explicit coordination: cc=but",
                "symmetric/contrastive",
            )

        return None, None, None

    if syntax_type == "advcl":
        if m == "if":
            return (
                "condition",
                "explicit conditional marker: if",
                "unit1(condition) -> unit2(effect)",
            )

        if m == "unless":
            return (
                "condition",
                "explicit conditional marker: unless; condition polarity negated",
                "NOT unit1(condition) -> unit2(effect)",
            )

        if m in {
            "provided",
            "provided that",
            "providing",
            "assuming",
            "assuming that",
        }:
            return (
                "condition",
                f"explicit conditional marker: {m}",
                "unit1(condition) -> unit2(effect)",
            )

        if m in {"although", "though", "even though"}:
            return (
                "concession",
                f"explicit concessive marker: {m}",
                "unit1(concession-context) <-> unit2(main)",
            )

        if m == "whereas":
            return (
                "contrast",
                "explicit contrast marker: whereas",
                "unit1 <-> unit2",
            )

        if m == "before":
            return (
                "temporal",
                "explicit temporal marker: before",
                "unit1 occurs before unit2",
            )

        if m == "after":
            return (
                "temporal",
                "explicit temporal marker: after",
                "unit2 occurs before unit1",
            )

        if m == "until":
            return (
                "temporal",
                "explicit temporal marker: until",
                "unit2 continues until unit1",
            )

        if m == "once":
            return (
                "temporal",
                "explicit temporal marker: once",
                "unit1 precedes/enables unit2",
            )

        if m == "because":
            return (
                "causal",
                "explicit causal marker: because",
                "unit1(cause) -> unit2(effect)",
            )

        if m in {"so that", "in order that"}:
            return (
                "purpose",
                f"explicit purpose marker: {m}",
                "unit2(action) -> unit1(purpose)",
            )

        if m == "without":
            return (
                "condition",
                "negative prerequisite marker: without",
                "NOT unit1 -> unit2",
            )

        # Ambiguous on purpose: while, when, since, as, by
        return None, None, None

    return None, None, None


# ============================================================================
# V5 CLAUSE RECONSTRUCTION
# ============================================================================

def reconstruct_advcl(
    sentence,
    dep,
    constituents,
    marker=None,
):
    """
    V7:
      * Default: subordinate unit = smallest full clause containing advcl root;
        governing unit = smallest full clause containing the governor while
        excluding the advcl root.
      * Temporal non-finite adjuncts such as "after searching" are different:
        their immediate dependency head may be only one predicate inside a
        larger finite coordination ("you cannot verify or find ...").
        In that case, use the enclosing finite clause and subtract the temporal
        adjunct subtree instead of falling back to the local predicate.
    """

    children = build_children(sentence)

    dep_subtree = subtree_ids(dep.id, children)

    dep_clause = smallest_enclosing_clause(
        constituents,
        dep.id,
        dep.head,
    )

    notes = []

    if temporal_nonfinite_advcl(dep, marker):
        governing_clause = smallest_enclosing_clause(
            constituents,
            dep.head,
            None,
        )

        if governing_clause is not None:
            unit1_ids = (
                ids_from_constituent(dep_clause)
                if dep_clause is not None
                else dep_subtree
            )

            unit2_ids = (
                ids_from_constituent(governing_clause)
                - dep_subtree
            )

            notes.append(
                "temporal non-finite adjunct: governing unit expanded to "
                f"enclosing {governing_clause.label} and adjunct subtree "
                "subtracted"
            )

            if dep_clause is not None:
                notes.append(
                    f"unit1 selected enclosing {dep_clause.label}"
                )

            return unit1_ids, unit2_ids, notes

    head_clause = smallest_enclosing_clause(
        constituents,
        dep.head,
        dep.id,
    )

    unit1_ids = (
        ids_from_constituent(dep_clause)
        if dep_clause is not None
        else dep_subtree
    )

    unit2_ids = (
        ids_from_constituent(head_clause)
        if head_clause is not None
        else fallback_head_span(
            sentence,
            dep.head,
            dep_subtree,
        )
    )

    if dep_clause is not None:
        notes.append(
            f"unit1 selected enclosing {dep_clause.label} "
            "instead of predicate-only VP"
        )

    if head_clause is not None:
        notes.append(
            f"unit2 selected enclosing {head_clause.label}"
        )

    return unit1_ids, unit2_ids, notes



def coordination_scope_material(
    sentence,
    head,
    dep,
    unit1_ids,
    unit2_ids,
):
    """
    Recover only high-confidence material that syntactically scopes over the
    coordinated predicates but sits outside the lowest constituency container.

    V5 safety rule:
      If the second conjunct introduces its OWN subject, it is an independent
      clause. Do not copy the first clause's subject, auxiliaries/modals, copula,
      or negation into it.

    This specifically prevents errors such as:
      "the user would like ... AND the knowledge base has ..."
         -> "would the knowledge base has ..."   [WRONG]

    For shared-predicate coordination with no second subject, we may copy only:
      subject, auxiliary/modal, copula, negation, and negative "never/not".
    No object inheritance heuristic is used.
    """

    children = build_children(sentence)
    words = {w.id: w for w in sentence.words}
    notes = []

    dep_subjects = [
        w for w in sentence.words
        if w.head == dep.id and dep_base(w) in {"nsubj", "csubj"}
    ]

    if dep_subjects:
        notes.append(
            "second conjunct has its own subject; governing subject/modal/"
            "negation scope was NOT propagated"
        )
        return unit1_ids, unit2_ids, notes

    direct_head_children = [
        words[cid]
        for cid in children.get(head.id, [])
        if cid in words
    ]

    dep_has_aux = any(
        w.head == dep.id and dep_base(w) in {"aux", "cop"}
        for w in sentence.words
    )

    dep_has_neg = any(
        w.head == dep.id
        and (
            dep_base(w) == "neg"
            or (
                dep_base(w) == "advmod"
                and (w.lemma or w.text).lower() in {"not", "never"}
            )
        )
        for w in sentence.words
    )

    dep_is_independently_finite = is_independently_finite_conj(
        sentence,
        dep,
    )

    scope_ids = set()

    for child in direct_head_children:
        base = dep_base(child)
        lemma = (child.lemma or child.text).lower()
        allowed = False

        if base in {"nsubj", "csubj"}:
            allowed = True
        elif dep_is_independently_finite:
            # V8: an independently finite sibling predicate such as
            #   customer [is not frustrated], just [prefers human]
            # shares the subject, but owns its own tense/copula/negation.
            allowed = False
        elif base in {"aux", "cop"}:
            allowed = not dep_has_aux
        elif base == "neg":
            allowed = not dep_has_neg
        elif base == "advmod" and lemma in {"not", "never"}:
            allowed = not dep_has_neg

        if allowed:
            scope_ids |= subtree_ids(child.id, children)

    if dep_is_independently_finite:
        notes.append(
            "second conjunct is independently finite; only subject scope may "
            "be inherited (aux/copula/negation kept local)"
        )
    else:
        notes.append(
            "second conjunct is not independently finite; governing "
            "auxiliary/negation scope may be inherited"
        )

    if scope_ids:
        before1 = set(unit1_ids)
        before2 = set(unit2_ids)
        unit1_ids |= scope_ids
        unit2_ids |= scope_ids

        notes.append(
            "governing subject/modal/negation scope copied to shared "
            f"coordination: {render_ids(sentence, scope_ids)!r}"
        )

        added1 = unit1_ids - before1
        added2 = unit2_ids - before2

        if added1:
            notes.append(
                "  added to unit1: "
                f"{render_ids(sentence, added1)!r}"
            )

        if added2:
            notes.append(
                "  added to unit2: "
                f"{render_ids(sentence, added2)!r}"
            )

    return unit1_ids, unit2_ids, notes


def direct_subjects(sentence, root_id):
    return [
        w for w in sentence.words
        if w.head == root_id and dep_base(w) in {"nsubj", "csubj"}
    ]


def coordination_is_proposition_level(
    sentence,
    head,
    dep,
    unit1_ids,
    unit2_ids,
):
    """
    V5 conservative gate for coordinated predicates.

    A `conj` dependency is NOT automatically two propositions.  In particular,
    embedded infinitival/gerundive coordinations such as:

        "you need to [answer ...] and [determine ...]"
        "plan on [giving ...] and [using ...]"

    are suppressed unless the coordination has enough clause-level syntax to
    stand on its own.

    We emit when at least one of these is true:
      1. the first conjunct is the sentence root (top-level imperative/finite);
      2. the first conjunct has an explicit subject;
      3. the second conjunct has its own explicit subject.

    This keeps relative/finite clauses such as "you can unlock and use" while
    suppressing bare embedded VPs such as "answer and determine".
    """

    head_subjects = direct_subjects(sentence, head.id)
    dep_subjects = direct_subjects(sentence, dep.id)

    if head.head == 0:
        return True, "top-level coordination: first conjunct is sentence root"

    if head_subjects:
        return (
            True,
            "clause-level coordination: first conjunct has explicit subject "
            f"{render_ids(sentence, {w.id for w in head_subjects})!r}",
        )

    if dep_subjects:
        return (
            True,
            "clause-level coordination: second conjunct introduces explicit "
            f"subject {render_ids(sentence, {w.id for w in dep_subjects})!r}",
        )

    parent = next(
        (w for w in sentence.words if w.id == head.head),
        None,
    )
    parent_desc = (
        f"parent={parent.text!r}/{head.deprel}"
        if parent is not None
        else f"head_deprel={head.deprel!r}"
    )

    return (
        False,
        "embedded coordination lacks its own clause-level subject/root; "
        f"{parent_desc}",
    )


def reconstruct_coordination(
    sentence,
    head,
    dep,
    tree_root,
    constituents,
    marker_ids,
):
    """
    Primary V5 reconstruction:
      1. Find the lowest constituency container that separates the two
         coordinated roots into distinct child branches.
      2. Materialize any tokens in that container that sit OUTSIDE the two
         branches into both propositions.
      3. Keep marker tokens out of proposition text.

    Example:
      [VP Do not [VP make up] or [VP assume] [NP the current time]]

      shared prefix/suffix = "Do not" + "the current time"
      A = "Do not make up the current time"
      B = "Do not assume the current time"

    There is deliberately NO trailing-object dependency heuristic in V5.
    """

    result = coordination_container(
        tree_root,
        head.id,
        dep.id,
    )

    notes = []

    if result is not None:
        container, head_branch, dep_branch = result

        container_ids = ids_from_constituent(container)
        head_branch_ids = ids_from_constituent(head_branch)
        dep_branch_ids = ids_from_constituent(dep_branch)

        shared_ids = (
            container_ids
            - head_branch_ids
            - dep_branch_ids
            - marker_ids
        )

        # Exclude pure punctuation from shared propagation.
        words = {w.id: w for w in sentence.words}
        shared_ids = {
            wid for wid in shared_ids
            if wid in words and words[wid].upos != "PUNCT"
        }

        right_only_ids = set()

        if is_independently_finite_conj(sentence, dep):
            if head_branch_ids and dep_branch_ids:
                left_edge = max(head_branch_ids)
                right_edge = min(dep_branch_ids)

                right_only_ids = {
                    wid
                    for wid in shared_ids
                    if left_edge < wid < right_edge
                    and wid in words
                    and words[wid].upos == "ADV"
                }

        common_shared_ids = shared_ids - right_only_ids

        # V11 reconstruction guard: when a lowest binary coordination container
        # actually contains an additional coordinated branch, that branch must
        # NOT be treated as ordinary shared material.  Doing so produced outputs
        # such as "agent was rude unhelpful" / "agent was slow unhelpful"
        # and "Customer is being abusive threatening".  We conservatively
        # reject this binary split and retain an enclosing source-backed unit.
        coord_children = build_children(sentence)
        extra_conj_ids = set()

        for word in sentence.words:
            if dep_base(word) != "conj":
                continue
            if word.id in head_branch_ids or word.id in dep_branch_ids:
                continue

            branch_ids = subtree_ids(word.id, coord_children)
            leaked_ids = branch_ids & common_shared_ids
            if leaked_ids:
                extra_conj_ids |= leaked_ids

        if extra_conj_ids:
            notes.append(
                "RECONSTRUCTION_QUALITY_BOTH: extra coordinated branch would "
                "be copied as shared material: "
                f"{render_ids(sentence, extra_conj_ids)!r}"
            )

        unit1_ids = head_branch_ids | common_shared_ids
        unit2_ids = dep_branch_ids | common_shared_ids | right_only_ids

        if right_only_ids:
            notes.append(
                "independently finite second predicate: intervening adverb attached only "
                f"to unit2: {render_ids(sentence, right_only_ids)!r}"
            )

        unit1_ids, unit2_ids, scope_notes = coordination_scope_material(
            sentence,
            head,
            dep,
            unit1_ids,
            unit2_ids,
        )

        notes.append(
            f"coordination container={container.label} "
            f"span=[{container.start},{container.end})"
        )
        notes.extend(scope_notes)

        if common_shared_ids:
            notes.append(
                "shared container material copied to both units: "
                f"{render_ids(sentence, common_shared_ids)!r}"
            )
        else:
            notes.append(
                "coordination branches were already self-contained; "
                "no shared material copied"
            )

        return (
            unit1_ids,
            unit2_ids,
            "constituency_coordination_container",
            notes,
        )

    # Conservative fallback if constituency cannot isolate branches.
    children = build_children(sentence)

    dep_ids = subtree_ids(dep.id, children)

    all_conj_subtrees = set()

    for child_id in children.get(head.id, []):
        child = next(
            (w for w in sentence.words if w.id == child_id),
            None,
        )

        if child is not None and dep_base(child) == "conj":
            all_conj_subtrees |= subtree_ids(child_id, children)

    head_ids = subtree_ids(head.id, children) - all_conj_subtrees

    head_ids, dep_ids, scope_notes = coordination_scope_material(
        sentence,
        head,
        dep,
        head_ids,
        dep_ids,
    )

    notes.append(
        "WARNING: constituency container could not separate coordination; "
        "used conservative dependency fallback"
    )
    notes.extend(scope_notes)

    return (
        head_ids,
        dep_ids,
        "dependency_fallback",
        notes,
    )



# ============================================================================
# V6/V7 PARATAXIS / CLAUSE-SIBLING HELPERS
# ============================================================================

FORMAT_ONLY_RE = re.compile(
    r"^\s*(?:IMPORTANT|NOTE|WARNING|CAUTION|REMEMBER|TIP)\s*[.:]?\s*$",
    flags=re.IGNORECASE,
)


def formatting_only_unit(text: str):
    cleaned = clean_text(text).strip()

    if not cleaned:
        return True

    if FORMAT_ONLY_RE.fullmatch(cleaned):
        return True

    words = re.findall(r"[A-Za-z]+", cleaned)

    if (
        1 <= len(words) <= 2
        and cleaned.rstrip(".:").isupper()
        and not re.search(
            r"\b(is|are|do|does|must|should|can|may)\b",
            cleaned,
            re.I,
        )
    ):
        return True

    return False


def lowest_separating_constituent(
    root: Constituent,
    head_id: int,
    dep_id: int,
    allowed_labels=None,
):
    """
    Return the lowest constituency node whose immediate child branches
    separate the two roots.
    """

    allowed_labels = allowed_labels or {
        "S", "SINV", "SQ", "FRAG", "ROOT", "PRN",
    }

    head_idx = head_id - 1
    dep_idx = dep_id - 1
    best = None

    def contains(node, idx):
        return node.start <= idx < node.end

    def visit(node):
        nonlocal best

        if not (contains(node, head_idx) and contains(node, dep_idx)):
            return

        head_children = [
            child for child in node.children
            if contains(child, head_idx)
        ]
        dep_children = [
            child for child in node.children
            if contains(child, dep_idx)
        ]

        if (
            head_children
            and dep_children
            and head_children[0] is not dep_children[0]
            and node.label in allowed_labels
        ):
            best = (
                node,
                head_children[0],
                dep_children[0],
            )

        for child in node.children:
            if contains(child, head_idx) and contains(child, dep_idx):
                visit(child)

    visit(root)
    return best


def reconstruct_parataxis(
    sentence,
    dep,
    tree_root,
    constituents,
):
    """
    Prefer non-overlapping sibling constituency branches for comma/parataxis
    clauses.  This avoids Unit1 swallowing later sibling clauses.
    """

    children = build_children(sentence)
    notes = []

    if tree_root is not None:
        separated = lowest_separating_constituent(
            tree_root,
            dep.head,
            dep.id,
        )

        if separated is not None:
            container, head_branch, dep_branch = separated

            head_ids = ids_from_constituent(head_branch)
            dep_ids = ids_from_constituent(dep_branch)

            notes.append(
                f"parataxis container={container.label} "
                f"span=[{container.start},{container.end})"
            )
            notes.append(
                f"sibling branches={head_branch.label}/{dep_branch.label}"
            )

            return (
                head_ids,
                dep_ids,
                "constituency_parataxis_siblings",
                notes,
            )

    dep_clause = smallest_enclosing_clause(
        constituents,
        dep.id,
        dep.head,
    )

    head_clause = smallest_enclosing_clause(
        constituents,
        dep.head,
        dep.id,
    )

    if dep_clause is not None and head_clause is not None:
        head_ids = ids_from_constituent(head_clause)
        dep_ids = ids_from_constituent(dep_clause)

        if not (head_ids & dep_ids):
            notes.append(
                "used two non-overlapping enclosing clause constituents"
            )
            return (
                head_ids,
                dep_ids,
                "enclosing_clause_parataxis",
                notes,
            )

    dep_ids = subtree_ids(dep.id, children)
    head_ids = subtree_ids(dep.head, children) - dep_ids

    notes.append("WARNING: used dependency parataxis fallback")

    return (
        head_ids,
        dep_ids,
        "dependency_parataxis_fallback",
        notes,
    )


def block_context_text(block: SourceBlock):
    """
    DeDisCo may see structural metadata as context, while Stanza receives only
    the natural-language block text.
    """

    meta_parts = []

    if block.heading:
        meta_parts.append(f"Section: {block.heading}")

    for key, value in block.metadata.items():
        if value:
            meta_parts.append(f"{key}: {value}")

    if not meta_parts:
        return block.text

    return " | ".join(meta_parts) + "\n" + block.text


# ============================================================================
# CANDIDATE EXTRACTION
# ============================================================================

def extract_candidates_from_sentence(
    sentence,
    block: SourceBlock,
    global_sentence_index: int,
    next_pair_id: int,
):
    candidates = []
    words = {w.id: w for w in sentence.words}
    children = build_children(sentence)

    try:
        tree_root = parse_penn_tree(str(sentence.constituency))
        constituents = flatten_constituents(tree_root)
    except Exception:
        tree_root = None
        constituents = []

    # ----------------------------------------------------------------
    # ADVCL
    # ----------------------------------------------------------------

    for dep in sentence.words:
        if dep_base(dep) != "advcl":
            continue

        if dep.head == 0 or dep.head not in words:
            continue

        marker, marker_ids = direct_connective(
            sentence,
            dep.id,
            "mark",
        )

        emit_advcl, suppress_reason = should_emit_advcl(marker)

        if not emit_advcl:
            # V5 keeps weak/predicate-internal adjunct structure inside the
            # proposition instead of promoting it to a graph relation.
            SUPPRESSION_STATS["weak_advcl"] += 1
            continue

        unit1_ids, unit2_ids, notes = reconstruct_advcl(
            sentence,
            dep,
            constituents,
            marker=marker,
        )

        logic_scope_warning = logic_scope_warning_for_advcl(
            sentence,
            dep,
            marker,
        )

        if logic_scope_warning:
            notes.append(logic_scope_warning)
            SUPPRESSION_STATS["logic_scope_flagged"] += 1

        unit1_raw = render_ids(
            sentence,
            unit1_ids,
            remove_ids=marker_ids,
        )

        unit2_raw = render_ids(
            sentence,
            unit2_ids,
        )

        if not unit1_raw or not unit2_raw:
            continue

        if suppress_if_semantically_open(
            sentence=sentence,
            block=block,
            global_sentence_index=global_sentence_index,
            syntax_type="advcl",
            stanza_deprel=dep.deprel,
            marker=marker,
            unit1=unit1_raw,
            unit2=unit2_raw,
        ):
            continue

        relation, note, direction = explicit_relation(
            "advcl",
            marker,
        )

        candidates.append(
            RelationCandidate(
                pair_id=next_pair_id,
                block_index=block.index,
                block_kind=block.kind,
                heading=block.heading,
                sentence_index=global_sentence_index,
                sentence=sentence.text,
                constituency=str(sentence.constituency),
                syntax_type="advcl",
                stanza_deprel=dep.deprel,
                marker=marker,
                unit1_raw=unit1_raw,
                unit2_raw=unit2_raw,
                unit1=unit1_raw,
                unit2=unit2_raw,
                reconstruction_method="enclosing_clause_S",
                reconstruction_notes=notes,
                deterministic_relation=relation,
                deterministic_note=note,
                deterministic_direction=direction,
            )
        )

        next_pair_id += 1

    # ----------------------------------------------------------------
    # COORDINATION
    # ----------------------------------------------------------------

    for dep in sentence.words:
        if dep_base(dep) != "conj":
            continue

        if dep.head == 0 or dep.head not in words:
            continue

        if dep.upos not in {"VERB", "AUX", "ADJ"}:
            continue

        head = words[dep.head]

        marker, marker_ids = direct_connective(
            sentence,
            dep.id,
            "cc",
        )

        if (
            tree_root is not None
            and coordinated_sbar_complement(
                tree_root,
                head.id,
                dep.id,
            ) is not None
        ):
            SUPPRESSION_STATS["coordinated_sbar_complement"] += 1
            continue

        nary_group = (
            find_nary_clause_group(
                tree_root,
                head.id,
                dep.id,
            )
            if tree_root is not None
            else None
        )

        if nary_group is not None:
            (
                nary_container,
                head_branch,
                dep_branch,
                proposition_children,
            ) = nary_group

            unit1_ids = ids_from_constituent(head_branch)
            unit2_ids = ids_from_constituent(dep_branch)

            method = "nary_clause_group_branches"
            notes = [
                "V7 n-ary clause group detected before binary coordination",
                f"container={nary_container.label} "
                f"members={len(proposition_children)}",
                f"branch labels={head_branch.label}/{dep_branch.label}",
            ]

            # V11 reconstruction guard: a finite VP branch inside an n-ary
            # clause list may inherit its subject from a sibling clause.  The
            # branch text alone is then not a safe proposition (e.g.
            # "and now demands human" -> "demands human").  Do not guess
            # the subject; reject this lower-level pair and keep its parent.
            if (
                head_branch.label == "VP"
                and is_finite_predicate(head)
                and not direct_subjects(sentence, head.id)
            ):
                notes.append(
                    "RECONSTRUCTION_QUALITY_UNIT1: finite n-ary VP branch "
                    "lacks an explicit subject"
                )

            if (
                dep_branch.label == "VP"
                and is_finite_predicate(dep)
                and not direct_subjects(sentence, dep.id)
            ):
                notes.append(
                    "RECONSTRUCTION_QUALITY_UNIT2: finite n-ary VP branch "
                    "lacks an explicit subject"
                )

            SUPPRESSION_STATS["nary_clause_pairs"] += 1

        elif tree_root is not None:
            (
                unit1_ids,
                unit2_ids,
                method,
                notes,
            ) = reconstruct_coordination(
                sentence,
                head,
                dep,
                tree_root,
                constituents,
                marker_ids,
            )
        else:
            dep_ids = subtree_ids(dep.id, children)

            unit1_ids = (
                subtree_ids(head.id, children) - dep_ids
            )

            unit2_ids = dep_ids
            method = "dependency_fallback_no_constituency"
            notes = [
                "WARNING: constituency tree unavailable"
            ]

        if method == "nary_clause_group_branches":
            emit_coordination = True
            coord_gate_note = (
                "n-ary clause-group structure supplies proposition-level "
                "evidence"
            )
        else:
            (
                emit_coordination,
                coord_gate_note,
            ) = coordination_is_proposition_level(
                sentence,
                head,
                dep,
                unit1_ids,
                unit2_ids,
            )

        if not emit_coordination:
            SUPPRESSION_STATS["embedded_coordination"] += 1
            continue

        notes.append(coord_gate_note)

        unit1_raw = render_ids(
            sentence,
            unit1_ids,
            remove_ids=marker_ids,
        )

        unit2_raw = render_ids(
            sentence,
            unit2_ids,
            remove_ids=marker_ids,
        )

        if not unit1_raw or not unit2_raw:
            continue

        if suppress_if_reconstruction_invalid(
            sentence=sentence,
            block=block,
            global_sentence_index=global_sentence_index,
            syntax_type="conj",
            stanza_deprel=dep.deprel,
            marker=marker,
            unit1=unit1_raw,
            unit2=unit2_raw,
            reconstruction_notes=notes,
        ):
            continue

        if suppress_if_semantically_open(
            sentence=sentence,
            block=block,
            global_sentence_index=global_sentence_index,
            syntax_type="conj",
            stanza_deprel=dep.deprel,
            marker=marker,
            unit1=unit1_raw,
            unit2=unit2_raw,
        ):
            continue

        relation, note, direction = explicit_relation(
            "conj",
            marker,
        )

        candidates.append(
            RelationCandidate(
                pair_id=next_pair_id,
                block_index=block.index,
                block_kind=block.kind,
                heading=block.heading,
                sentence_index=global_sentence_index,
                sentence=sentence.text,
                constituency=str(sentence.constituency),
                syntax_type="conj",
                stanza_deprel=dep.deprel,
                marker=marker,
                unit1_raw=unit1_raw,
                unit2_raw=unit2_raw,
                unit1=unit1_raw,
                unit2=unit2_raw,
                reconstruction_method=method,
                reconstruction_notes=notes,
                deterministic_relation=relation,
                deterministic_note=note,
                deterministic_direction=direction,
            )
        )

        next_pair_id += 1

    # ----------------------------------------------------------------
    # PARATAXIS
    # ----------------------------------------------------------------

    for dep in sentence.words:
        if dep_base(dep) != "parataxis":
            continue

        if dep.head == 0 or dep.head not in words:
            continue

        (
            head_ids,
            dep_ids,
            reconstruction_method,
            reconstruction_notes,
        ) = reconstruct_parataxis(
            sentence,
            dep,
            tree_root,
            constituents,
        )

        unit1 = render_ids(sentence, head_ids)
        unit2 = render_ids(sentence, dep_ids)

        if not unit1 or not unit2:
            continue

        if formatting_only_unit(unit1) or formatting_only_unit(unit2):
            SUPPRESSION_STATS["formatting_parataxis"] += 1
            continue

        if clean_text(unit1).lower() == clean_text(unit2).lower():
            SUPPRESSION_STATS["duplicate_parataxis"] += 1
            continue

        if suppress_if_semantically_open(
            sentence=sentence,
            block=block,
            global_sentence_index=global_sentence_index,
            syntax_type="parataxis",
            stanza_deprel=dep.deprel,
            marker=None,
            unit1=unit1,
            unit2=unit2,
        ):
            continue

        candidates.append(
            RelationCandidate(
                pair_id=next_pair_id,
                block_index=block.index,
                block_kind=block.kind,
                heading=block.heading,
                sentence_index=global_sentence_index,
                sentence=sentence.text,
                constituency=str(sentence.constituency),
                syntax_type="parataxis",
                stanza_deprel=dep.deprel,
                marker=None,
                unit1_raw=unit1,
                unit2_raw=unit2,
                unit1=unit1,
                unit2=unit2,
                reconstruction_method=reconstruction_method,
                reconstruction_notes=reconstruction_notes,
                deterministic_relation=None,
                deterministic_note=None,
                deterministic_direction=None,
            )
        )

        next_pair_id += 1

    return candidates, next_pair_id


# ============================================================================
# DEDISCO
# ============================================================================

def get_dedisco_system_prompt(path: Path):
    tree = ast.parse(path.read_text())

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue

        for target in node.targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "PROMPT"
            ):
                try:
                    return ast.literal_eval(node.value)
                except Exception:
                    pass

    raise RuntimeError(f"Could not find PROMPT in {path}")


def make_dedisco_prompt(
    tokenizer,
    system_prompt,
    candidate,
    block_text,
    total_sentences,
):
    if total_sentences <= 1:
        pos = 0.5
    else:
        pos = candidate.sentence_index / (total_sentences - 1)

    user_text = f"""## Language:
eng

## Corpus:
custom_policy

## Framework:
unknown

## Same Speaker:
True

## Distance Between Unit1 and Unit2:
0

## Percentage Position of Unit1:
{pos:.3f}

## Percentage Position of Unit2:
{pos:.3f}

## Context:
{block_text}

## Direction:
From Unit1 to Unit2.

## Unit1:
{candidate.unit1}

## Unit2:
{candidate.unit2}"""

    return tokenizer.apply_chat_template(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        add_generation_prompt=True,
        tokenize=False,
        enable_thinking=False,
    )


def _normalize_dedisco_output(output: str) -> str:
    pred = str(output or "").strip().lower()
    return pred.strip(" .,:;\"'`")


def _positive_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ignoring invalid {}={!r}; using {}", name, raw, default)
        return default
    if value < 1:
        logger.warning("Ignoring non-positive {}={}; using {}", name, value, default)
        return default
    return value


def _encode_dedisco_prompt_for_batch(tokenizer, prompt: str):
    # Match mlx_lm.generate's string-tokenization behavior so batch and serial
    # modes receive the same prompt tokens.
    bos_token = getattr(tokenizer, "bos_token", None)
    add_special_tokens = bos_token is None or not prompt.startswith(bos_token)
    return tokenizer.encode(prompt, add_special_tokens=add_special_tokens)


def classify_dedisco_batch(
    items,
    model,
    tokenizer,
    system_prompt,
):
    """Classify independent DeDisCo prompts in bounded MLX batches.

    Each item is ``(candidate, block_text, total_sentences)``. Prompts remain
    completely independent; batching changes only the tensor execution shape.
    Batch size 1 deliberately uses the historical ``generate`` path so local
    A/B comparisons can reproduce the pre-batching inference route exactly.
    """
    if not items:
        return []

    prompts = [
        make_dedisco_prompt(
            tokenizer,
            system_prompt,
            candidate,
            block_text,
            total_sentences,
        )
        for candidate, block_text, total_sentences in items
    ]
    batch_size = _positive_env_int("CUGA_DEDISCO_BATCH_SIZE", 4)
    results: list[tuple[str, float]] = []

    for offset in range(0, len(prompts), batch_size):
        prompt_batch = prompts[offset:offset + batch_size]
        start = time.perf_counter()

        # PyTorch MPS and MLX share Apple's Metal stack. Keep one process-wide
        # Metal owner, but make each ownership interval do useful batched work.
        with serialized_local_accelerator(apple_metal=True):
            if len(prompt_batch) == 1 or batch_generate is None:
                outputs = [
                    generate(
                        model,
                        tokenizer,
                        prompt=prompt,
                        max_tokens=20,
                        verbose=False,
                    )
                    for prompt in prompt_batch
                ]
                backend = "generate" if len(prompt_batch) == 1 else "generate_fallback"
            else:
                encoded_prompts = [
                    _encode_dedisco_prompt_for_batch(tokenizer, prompt)
                    for prompt in prompt_batch
                ]
                response = batch_generate(
                    model,
                    tokenizer,
                    encoded_prompts,
                    max_tokens=20,
                    verbose=False,
                )
                outputs = list(getattr(response, "texts", []))
                if len(outputs) != len(prompt_batch):
                    raise RuntimeError(
                        "DeDisCo batch_generate returned the wrong number of outputs: "
                        f"expected {len(prompt_batch)}, got {len(outputs)}"
                    )
                backend = "batch_generate"

        batch_latency = time.perf_counter() - start
        avg_latency = batch_latency / max(len(prompt_batch), 1)
        logger.debug(
            "DeDisCo inference batch: items={} configured_batch_size={} backend={} "
            "batch_latency_s={:.3f} avg_item_latency_s={:.3f}",
            len(prompt_batch),
            batch_size,
            backend,
            batch_latency,
            avg_latency,
        )
        results.extend(
            (_normalize_dedisco_output(output), avg_latency)
            for output in outputs
        )

    return results


def classify_dedisco(
    candidate,
    block_text,
    total_sentences,
    model,
    tokenizer,
    system_prompt,
):
    return classify_dedisco_batch(
        [(candidate, block_text, total_sentences)],
        model,
        tokenizer,
        system_prompt,
    )[0]

# ============================================================================
# CUGA PRODUCTION ADAPTER
# ============================================================================

import os
import threading

from loguru import logger
from .logging_utils import memory_graph_trace_enabled

from .accelerator_serialization import serialized_local_accelerator

from .schemas import (
    GraphBuildRequest,
    LocalChildStatement,
    LocalDecompositionDecision,
    LocalRelationHint,
    RelationOrigin,
    RelationType,
    SemanticRole,
)

PRODUCTION_PIPELINE_VERSION = "stanza_dedisco_v12_retrieval_only_asymmetric_guard"

_DEDISCO_FALLBACK_SYSTEM_PROMPT = """## Role and Goal:
You are an expert in discourse analysis. Identify the discourse relation between Unit1 and Unit2.

## Guidelines:
Use the supplied language, corpus, framework, context, direction, speaker, distance, and position information. Choose exactly one label from the provided set and output only that label, with no explanation.

## Labels:
contrast, condition, mode, organization, frame, temporal, concession, reformulation, comment, query, attribution, alternation, purpose, explanation, elaboration, causal, conjunction
"""

_STANZA_PIPELINE = None
_STANZA_LOCK = threading.Lock()
_DEDISCO_MODEL = None
_DEDISCO_TOKENIZER = None
_DEDISCO_PROMPT = None
_DEDISCO_LOCK = threading.Lock()


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _stanza_device() -> str:
    configured = os.environ.get("CUGA_STANZA_DEVICE", "auto").strip().lower()
    if configured != "auto":
        return configured
    try:
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _get_stanza_pipeline():
    global _STANZA_PIPELINE
    if _STANZA_PIPELINE is not None:
        return _STANZA_PIPELINE
    with _STANZA_LOCK:
        if _STANZA_PIPELINE is not None:
            return _STANZA_PIPELINE
        device = _stanza_device()
        start = time.perf_counter()
        with serialized_local_accelerator(device=device):
            _STANZA_PIPELINE = stanza.Pipeline(
                lang="en",
                processors="tokenize,pos,lemma,depparse,constituency",
                package="default_accurate",
                device=device,
                download_method=None,
                verbose=False,
            )
        logger.info(
            "Loaded Stanza decomposition pipeline: version={} device={} latency_s={:.2f}",
            PRODUCTION_PIPELINE_VERSION,
            device,
            time.perf_counter() - start,
        )
        return _STANZA_PIPELINE


def _process_stanza_statements(statements: list[str]):
    """Process independent statements in bounded Stanza document batches.

    Stanza's bulk mode preserves document boundaries. Batch size 1 intentionally
    uses the historical ``nlp(statement)`` route for exact A/B rollback.
    """
    if not statements:
        return []

    nlp = _get_stanza_pipeline()
    device = _stanza_device()
    batch_size = _positive_env_int("CUGA_STANZA_BATCH_SIZE", 8)
    docs = []

    for offset in range(0, len(statements), batch_size):
        statement_batch = statements[offset:offset + batch_size]
        start = time.perf_counter()
        with serialized_local_accelerator(device=device):
            if len(statement_batch) == 1:
                batch_docs = [nlp(statement_batch[0])]
                backend = "single"
            else:
                # ``bulk_process`` wraps each input as its own stanza.Document
                # and uses processor bulk APIs without concatenating semantic
                # context across documents.
                batch_docs = list(nlp.bulk_process(statement_batch))
                backend = "bulk_process"

        if len(batch_docs) != len(statement_batch):
            raise RuntimeError(
                "Stanza bulk_process returned the wrong number of documents: "
                f"expected {len(statement_batch)}, got {len(batch_docs)}"
            )
        latency = time.perf_counter() - start
        logger.debug(
            "Stanza decomposition batch: items={} configured_batch_size={} "
            "device={} backend={} batch_latency_s={:.3f}",
            len(statement_batch),
            batch_size,
            device,
            backend,
            latency,
        )
        docs.extend(batch_docs)

    return docs


def _candidate_model_paths() -> list[Path]:
    configured = os.environ.get("CUGA_DEDISCO_MODEL_PATH")
    if configured:
        return [Path(configured).expanduser()]

    relative = Path("models/dedisco-qwen3-4b-4bit")
    paths = [relative]
    cwd = Path.cwd()
    for root in [cwd, *list(cwd.parents)[:5]]:
        paths.append(root / relative)
    result = []
    seen = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _resolve_dedisco_model_path() -> Path:
    for path in _candidate_model_paths():
        if (path / "config.json").exists() and (path / "model.safetensors").exists():
            return path
    attempted = ", ".join(str(p) for p in _candidate_model_paths())
    raise RuntimeError(
        "DeDisCo is enabled but the local MLX model was not found. "
        "Set CUGA_DEDISCO_MODEL_PATH to models/dedisco-qwen3-4b-4bit. "
        f"Attempted: {attempted}"
    )


def _resolve_dedisco_prompt() -> str:
    configured = os.environ.get("CUGA_DEDISCO_DECODER_PATH")
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(Path("/tmp/dedisco_decoder.py"))

    for path in candidates:
        if not path.exists():
            continue
        try:
            return get_dedisco_system_prompt(path)
        except Exception as exc:
            logger.warning(
                "Could not read DeDisCo PROMPT from {}: {}; using bundled fallback",
                path,
                exc,
            )
    return _DEDISCO_FALLBACK_SYSTEM_PROMPT


def _get_dedisco():
    global _DEDISCO_MODEL, _DEDISCO_TOKENIZER, _DEDISCO_PROMPT
    if not _env_flag("CUGA_DEDISCO_ENABLED", True):
        return None, None, None
    if _DEDISCO_MODEL is not None:
        return _DEDISCO_MODEL, _DEDISCO_TOKENIZER, _DEDISCO_PROMPT
    with _DEDISCO_LOCK:
        if _DEDISCO_MODEL is not None:
            return _DEDISCO_MODEL, _DEDISCO_TOKENIZER, _DEDISCO_PROMPT
        model_path = _resolve_dedisco_model_path()
        start = time.perf_counter()
        with serialized_local_accelerator(apple_metal=True):
            _DEDISCO_MODEL, _DEDISCO_TOKENIZER = load(str(model_path))
        _DEDISCO_PROMPT = _resolve_dedisco_prompt()
        logger.info(
            "Loaded DeDisCo relation classifier: path={} latency_s={:.2f}",
            model_path,
            time.perf_counter() - start,
        )
        return _DEDISCO_MODEL, _DEDISCO_TOKENIZER, _DEDISCO_PROMPT


def _normalized_text(text: str) -> str:
    return clean_text(text).strip().casefold()


def _routing_text(text: str) -> str:
    return clean_text(text)


def _infer_semantic_role(
    text: str,
    *,
    force_condition: bool = False,
) -> SemanticRole:
    if force_condition:
        return SemanticRole.CONDITION

    normalized = _normalized_text(text)
    if re.match(r"^(?:do\s+not|don't|never|must\s+not|cannot|can't)\b", normalized):
        return SemanticRole.PROHIBITION
    if re.search(r"\b(?:may|is allowed to|are allowed to|can)\b", normalized):
        return SemanticRole.PERMISSION
    if re.search(r"\b(?:must|should|need to|required to|have to)\b", normalized):
        return SemanticRole.REQUIREMENT
    if normalized.startswith((
        "ask ", "use ", "invoke ", "verify ", "search ", "transfer ",
        "tell ", "let ", "be ", "do ", "never ", "always ",
    )):
        return SemanticRole.PROCEDURE
    return SemanticRole.FACT


def _sentence_children(doc) -> list[LocalChildStatement] | None:
    sentences = [clean_text(sentence.text) for sentence in doc.sentences if clean_text(sentence.text)]
    if len(sentences) <= 1:
        return None
    return [
        LocalChildStatement(
            content=text,
            source_text=text,
            semantic_role=_infer_semantic_role(text),
        )
        for text in sentences
    ]


_ELIGIBILITY_SCOPE_RE = re.compile(
    r"\b(?:eligib(?:ility|le)|qualification|qualifying|requirement|pre[- ]?application)\b",
    flags=re.IGNORECASE,
)
_EXPLICIT_REQUIREMENT_RE = re.compile(
    r"\b(?:required|requires?|must|needs?\s+to|have\s+to)\b",
    flags=re.IGNORECASE,
)


def _block_is_explicit_eligibility_requirement(block: SourceBlock) -> bool:
    """Whether a source block explicitly states an eligibility prerequisite.

    The relation is intentionally conservative: the enclosing heading must
    identify eligibility/requirements and the block itself must contain an
    explicit requirement cue.  This avoids turning ordinary terms such as APR
    or annual fee into prerequisites merely because a source groups them under
    a broad "Eligibility and Key Terms" section.
    """
    active_heading = str(block.metadata.get("active_heading") or block.heading or "")
    return bool(
        _ELIGIBILITY_SCOPE_RE.search(active_heading)
        and _EXPLICIT_REQUIREMENT_RE.search(block.text)
    )


def _block_decomposition(statement: str) -> LocalDecompositionDecision | None:
    """Return deterministic Markdown/source-block decomposition with scope edges."""
    blocks = build_source_blocks(statement)
    if not blocks:
        return None

    cleaned_statement = _normalized_text(statement)
    cleaned_blocks = [
        _normalized_text(block.text)
        for block in blocks
        if _normalized_text(block.text)
    ]
    if (
        len(cleaned_blocks) == 1
        and cleaned_blocks[0] == cleaned_statement
        and blocks[0].kind != "heading"
    ):
        return None

    children: list[LocalChildStatement] = []
    child_index_by_block_index: dict[int, int] = {}
    for block in blocks:
        text = clean_text(block.text)
        if not text:
            continue
        child_index_by_block_index[block.index] = len(children)
        child_metadata = dict(block.metadata)
        child_metadata.setdefault("source_block_kind", block.kind)
        children.append(
            LocalChildStatement(
                content=text,
                source_text=block.text.strip(),
                semantic_role=_infer_semantic_role(text),
                metadata=child_metadata,
            )
        )

    if not children:
        return None

    local_relations: list[LocalRelationHint] = []
    seen_relations: set[tuple[int, int, RelationType]] = set()
    for block in blocks:
        if block.kind == "heading":
            continue
        if not _block_is_explicit_eligibility_requirement(block):
            continue

        raw_scope_index = block.metadata.get("document_scope_block_index")
        if raw_scope_index is None:
            continue
        try:
            scope_block_index = int(raw_scope_index)
        except (TypeError, ValueError):
            continue

        source_child_index = child_index_by_block_index.get(block.index)
        target_child_index = child_index_by_block_index.get(scope_block_index)
        if source_child_index is None or target_child_index is None:
            continue
        if source_child_index == target_child_index:
            continue

        key = (source_child_index, target_child_index, RelationType.ENABLES)
        if key in seen_relations:
            continue
        seen_relations.add(key)
        local_relations.append(
            LocalRelationHint(
                source_child_index=source_child_index,
                target_child_index=target_child_index,
                relation=RelationType.ENABLES,
                origin=RelationOrigin.SOURCE_EXPLICIT,
                evidence_text=block.text.strip(),
                confidence=1.0,
                metadata={
                    "construction": "markdown_heading_scope",
                    "scope_relation": "eligibility_requirement_enables_document_scope",
                    "active_heading": str(
                        block.metadata.get("active_heading") or block.heading or ""
                    ),
                    "document_scope_heading": str(
                        block.metadata.get("document_scope_heading") or ""
                    ),
                },
            )
        )

    return LocalDecompositionDecision(
        kind="composite",
        routing_text=_routing_text(statement),
        children=children,
        local_relations=local_relations,
    )


def _block_children(statement: str) -> list[LocalChildStatement] | None:
    """Backwards-compatible child-only view of deterministic block splitting."""
    decision = _block_decomposition(statement)
    return None if decision is None else list(decision.children)


def _token_multiset_coverage(parent: str, left: str, right: str) -> float:
    parent_tokens = _closure_tokens(parent)
    if not parent_tokens:
        return 0.0
    child_tokens = _closure_tokens(left) + _closure_tokens(right)
    remaining = list(child_tokens)
    matched = 0
    for token in parent_tokens:
        try:
            idx = remaining.index(token)
        except ValueError:
            continue
        matched += 1
        remaining.pop(idx)
    return matched / len(parent_tokens)


def _candidate_rank(candidate: RelationCandidate, parent: str) -> tuple[float, int, int, int]:
    coverage = _token_multiset_coverage(parent, candidate.unit1, candidate.unit2)
    relation_bonus = int(candidate.deterministic_relation is not None)
    syntax_bonus = {"conj": 3, "advcl": 2, "parataxis": 1}.get(candidate.syntax_type, 0)
    size = len(_closure_tokens(candidate.unit1)) + len(_closure_tokens(candidate.unit2))
    return (coverage, relation_bonus, syntax_bonus, size)


def _pick_direct_candidate(candidates: list[RelationCandidate], parent: str) -> RelationCandidate | None:
    if not candidates:
        return None
    return max(candidates, key=lambda c: _candidate_rank(c, parent))


def _dedisco_labels(
    items: list[tuple[RelationCandidate, str]],
) -> list[tuple[str | None, float | None]]:
    if not items:
        return []

    results: list[tuple[str | None, float | None] | None] = [None] * len(items)
    pending: list[tuple[int, RelationCandidate, str]] = []
    for index, (candidate, context) in enumerate(items):
        if candidate.deterministic_relation:
            results[index] = (candidate.deterministic_relation, None)
        else:
            pending.append((index, candidate, context))

    if pending:
        model, tokenizer, prompt = _get_dedisco()
        if model is None:
            for index, _, _ in pending:
                results[index] = (None, None)
        else:
            predictions = classify_dedisco_batch(
                [(candidate, context, 1) for _, candidate, context in pending],
                model,
                tokenizer,
                prompt,
            )
            for (index, candidate, _), (pred, latency) in zip(
                pending, predictions, strict=True
            ):
                if pred not in VALID_LABELS:
                    logger.warning(
                        "Ignoring invalid DeDisCo output: pred={!r} unit1={!r} unit2={!r}",
                        pred,
                        candidate.unit1,
                        candidate.unit2,
                    )
                    results[index] = (None, latency)
                else:
                    results[index] = (pred, latency)

    return [
        result if result is not None else (None, None)
        for result in results
    ]


def _dedisco_label(candidate: RelationCandidate, context: str) -> tuple[str | None, float | None]:
    return _dedisco_labels([(candidate, context)])[0]


def _relation_hint(
    candidate: RelationCandidate,
    label: str | None,
    *,
    evidence_text: str,
) -> LocalRelationHint | None:
    if not label:
        return None

    source_index = 0
    target_index = 1
    relation = RelationType.RELATED_TO

    if label == "causal" and candidate.marker == "because":
        relation = RelationType.CAUSES
    elif label == "temporal":
        marker = (candidate.marker or "").lower()
        if marker in {"before", "once"}:
            relation = RelationType.PRECEDES
        elif marker == "after":
            relation = RelationType.PRECEDES
            source_index, target_index = 1, 0
    elif label == "purpose" and (candidate.marker or "").lower() in {"so that", "in order that"}:
        relation = RelationType.SERVES_GOAL
        source_index, target_index = 1, 0

    return LocalRelationHint(
        source_child_index=source_index,
        target_child_index=target_index,
        relation=relation,
        origin=RelationOrigin.SOURCE_EXPLICIT,
        evidence_text=evidence_text,
        confidence=1.0 if candidate.deterministic_relation else 0.90,
        metadata={
            "discourse_relation": label,
            "discourse_classifier": (
                "explicit_syntax" if candidate.deterministic_relation else "dedisco_qwen3_4b"
            ),
            "stanza_syntax_type": candidate.syntax_type,
            "stanza_deprel": candidate.stanza_deprel,
            "stanza_marker": candidate.marker,
            "reconstruction_method": candidate.reconstruction_method,
            "reconstruction_notes": list(candidate.reconstruction_notes),
            "dedisco_latency_s": candidate.dedisco_latency_s,
        },
    )


def _decision_from_candidate(
    *,
    request: GraphBuildRequest,
    statement: str,
    candidate: RelationCandidate,
    left: str,
    right: str,
    label: str | None,
    latency: float | None,
) -> LocalDecompositionDecision:
    if not candidate.deterministic_relation:
        candidate.dedisco_relation = label
        candidate.dedisco_valid = label in VALID_LABELS if label else None
    candidate.dedisco_latency_s = latency
    candidate.chosen_relation = label
    candidate.chosen_by = "explicit syntax" if candidate.deterministic_relation else "DeDisCo"

    is_condition = label == "condition"
    children = [
        LocalChildStatement(
            content=left,
            source_text=left,
            semantic_role=_infer_semantic_role(
                left,
                force_condition=is_condition and candidate.syntax_type == "advcl",
            ),
        ),
        LocalChildStatement(
            content=right,
            source_text=right,
            semantic_role=_infer_semantic_role(right),
        ),
    ]

    hint = _relation_hint(candidate, label, evidence_text=statement)
    if memory_graph_trace_enabled():
        logger.debug(
            "Stanza decomposition: source_id={} depth={} syntax={} marker={!r} "
            "relation={} left={!r} right={!r}",
            request.source_id,
            request.metadata.get("decomposition_depth", 0),
            candidate.syntax_type,
            candidate.marker,
            label,
            left[:160],
            right[:160],
        )
    return LocalDecompositionDecision(
        kind="composite",
        routing_text=_routing_text(statement),
        children=children,
        local_relations=[hint] if hint is not None else [],
    )


def call_prompt_decomposition_models(
    requests: list[GraphBuildRequest],
) -> list[LocalDecompositionDecision]:
    """Batch independent local decomposition requests without changing semantics.

    Deterministic Markdown/source-block splitting is still evaluated per request.
    Remaining independent statements are sent through Stanza's document-bulk
    interface. Only candidates that lack an explicit deterministic discourse
    label are then sent through MLX-LM ``batch_generate``. Output order exactly
    matches input order.
    """
    if not requests:
        return []

    decisions: list[LocalDecompositionDecision | None] = [None] * len(requests)
    stanza_pending: list[tuple[int, GraphBuildRequest, str]] = []

    for index, request in enumerate(requests):
        statement = request.content.strip()
        if not statement:
            raise ValueError("Stanza decomposition received an empty statement")

        if request.metadata.get("source_unit_kind") == "markdown_heading":
            decisions[index] = LocalDecompositionDecision(
                kind="atomic",
                routing_text=_routing_text(statement),
            )
            continue

        block_decision = _block_decomposition(statement)
        if block_decision is not None:
            decisions[index] = block_decision
            continue

        stanza_pending.append((index, request, statement))

    if stanza_pending:
        docs = _process_stanza_statements(
            [statement for _, _, statement in stanza_pending]
        )

        dedisco_pending: list[
            tuple[int, GraphBuildRequest, str, RelationCandidate, str, str]
        ] = []

        for (index, request, statement), doc in zip(
            stanza_pending, docs, strict=True
        ):
            sentence_children = _sentence_children(doc)
            if sentence_children:
                decisions[index] = LocalDecompositionDecision(
                    kind="composite",
                    routing_text=_routing_text(statement),
                    children=sentence_children,
                    local_relations=[],
                )
                continue

            if not doc.sentences:
                decisions[index] = LocalDecompositionDecision(
                    kind="atomic",
                    routing_text=_routing_text(statement),
                )
                continue

            sentence = doc.sentences[0]
            block = SourceBlock(
                index=0,
                kind="statement",
                heading=None,
                text=statement,
                metadata={},
            )

            before_suppressed = len(MERGED_SPLITS)
            candidates, _ = extract_candidates_from_sentence(
                sentence,
                block,
                global_sentence_index=0,
                next_pair_id=0,
            )
            newly_suppressed = MERGED_SPLITS[before_suppressed:]

            candidate = _pick_direct_candidate(candidates, statement)
            if candidate is None:
                if newly_suppressed and memory_graph_trace_enabled():
                    logger.debug(
                        "Stanza split suppressed; retaining parent atom: source_id={} depth={} "
                        "reasons={} preview={!r}",
                        request.source_id,
                        request.metadata.get("decomposition_depth", 0),
                        [item.unit1_issue or item.unit2_issue for item in newly_suppressed],
                        statement[:220],
                    )
                decisions[index] = LocalDecompositionDecision(
                    kind="atomic",
                    routing_text=_routing_text(statement),
                )
                continue

            left = clean_text(candidate.unit1)
            right = clean_text(candidate.unit2)
            parent_key = _normalized_text(statement)
            if (
                not left
                or not right
                or _normalized_text(left) == parent_key
                or _normalized_text(right) == parent_key
                or _normalized_text(left) == _normalized_text(right)
            ):
                decisions[index] = LocalDecompositionDecision(
                    kind="atomic",
                    routing_text=_routing_text(statement),
                )
                continue

            asym_child_index, asym_issue = asymmetric_comparative_reconstruction_issue(
                statement,
                left,
                right,
            )
            if asym_issue is not None:
                SUPPRESSION_STATS["asymmetric_comparative_reconstruction"] += 1
                MERGED_SPLITS.append(
                    SuppressedSplit(
                        block_index=candidate.block_index,
                        block_kind=candidate.block_kind,
                        heading=candidate.heading,
                        sentence_index=candidate.sentence_index,
                        sentence=statement,
                        syntax_type=candidate.syntax_type,
                        stanza_deprel=candidate.stanza_deprel,
                        marker=candidate.marker,
                        unit1=left,
                        unit2=right,
                        unit1_issue=(asym_issue if asym_child_index == 0 else None),
                        unit2_issue=(asym_issue if asym_child_index == 1 else None),
                        retained_text=statement,
                        retained_resolution="immediate_parent_statement",
                    )
                )
                if memory_graph_trace_enabled():
                    logger.debug(
                        "Stanza asymmetric-comparison split suppressed; retaining parent atom: "
                        "source_id={} depth={} reason={} preview={!r}",
                        request.source_id,
                        request.metadata.get("decomposition_depth", 0),
                        asym_issue,
                        statement[:220],
                    )
                decisions[index] = LocalDecompositionDecision(
                    kind="atomic",
                    routing_text=_routing_text(statement),
                )
                continue

            dedisco_pending.append(
                (index, request, statement, candidate, left, right)
            )

        if dedisco_pending:
            labels = _dedisco_labels(
                [(candidate, statement) for _, _, statement, candidate, _, _ in dedisco_pending]
            )
            for (
                index,
                request,
                statement,
                candidate,
                left,
                right,
            ), (label, latency) in zip(dedisco_pending, labels, strict=True):
                decisions[index] = _decision_from_candidate(
                    request=request,
                    statement=statement,
                    candidate=candidate,
                    left=left,
                    right=right,
                    label=label,
                    latency=latency,
                )

    missing = [index for index, decision in enumerate(decisions) if decision is None]
    if missing:
        raise RuntimeError(
            "Batched decomposition failed to produce decisions for indices: "
            + ", ".join(str(index) for index in missing)
        )

    logger.debug(
        "Local decomposition request batch complete: requests={} stanza_candidates={} ",
        len(requests),
        len(stanza_pending),
    )
    return [decision for decision in decisions if decision is not None]


def call_prompt_decomposition_model(
    request: GraphBuildRequest,
) -> LocalDecompositionDecision:
    """Backwards-compatible single-request adapter over the batched path."""
    return call_prompt_decomposition_models([request])[0]
