"""Markdown parsing and source-block construction for decomposition."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SourceBlock:
    index: int
    kind: str
    heading: str | None
    text: str
    metadata: dict[str, str] = field(default_factory=dict)


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

    return all(bool(TABLE_SEPARATOR_CELL_RE.fullmatch(cell)) for cell in normalized)


def normalize_header_name(value: str):
    return re.sub(r"\s+", " ", value.strip()).strip().lower()


def table_row_to_block(headers, cells):
    """
    Convert table rows to source-backed blocks.

    Column names are structural metadata and are never injected into the
    sentence passed to Stanza.

    The descriptive cell becomes source text; other cells and their column names
    remain in metadata.
    """

    metadata: dict[str, str] = {}

    if headers and len(headers) == len(cells):
        mapping = {
            header.strip(): cell.strip()
            for header, cell in zip(headers, cells)
            if header.strip() and cell.strip()
        }

        normalized = {normalize_header_name(k): (k, v) for k, v in mapping.items()}

        if "when to use" in normalized:
            original_header, prose = normalized["when to use"]

            metadata = {k: v for k, v in mapping.items() if k != original_header}
            metadata["source_column"] = original_header

            return prose.strip(), metadata

        prose_candidates = []

        for header, cell in mapping.items():
            score = len(cell) + 40 * int(bool(re.search(r"[.!?]", cell))) + 20 * int(len(cell.split()) >= 5)
            prose_candidates.append((score, header, cell))

        if prose_candidates:
            _, prose_header, prose = max(prose_candidates)

            metadata = {k: v for k, v in mapping.items() if k != prose_header}
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
# atomic graph leaves. The decomposition classifier also checks them if they
# reach recursion through another path.
STRUCTURAL_TERMINAL_LABELS = {
    "argument",
    "arguments",
    "example",
    "examples",
    "field",
    "fields",
    "input",
    "inputs",
    "note",
    "notes",
    "output",
    "outputs",
    "parameter",
    "parameters",
    "requirement",
    "requirements",
    "response",
    "responses",
    "result",
    "results",
    "return",
    "returns",
    "schema",
    "schemas",
    "tool",
    "tools",
    "variable",
    "variables",
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

        text = " ".join(line.strip() for line in current_lines if line.strip()).strip()

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
            is_document_scope = awaiting_document_scope or document_scope_block_index is None

            # Make the new heading current before constructing its block so
            # descendants can resolve exact source scope consistently.
            heading = heading_text
            metadata = {
                "source_unit_kind": "markdown_heading",
                "heading_level": str(heading_level),
                "heading_scope_role": ("document_scope" if is_document_scope else "section"),
            }
            if not is_document_scope and document_scope_block_index is not None:
                metadata["document_scope_block_index"] = str(document_scope_block_index)
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

            if len(table_rows) >= 2 and is_table_separator(table_rows[1]):
                headers = table_rows[0]
                data_rows = table_rows[2:]
            else:
                data_rows = [row for row in table_rows if not is_table_separator(row)]

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
