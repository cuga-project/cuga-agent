"""Deterministic, exact-span source partitioning for graph decomposition."""

from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger


@dataclass(frozen=True)
class DecompositionSourceChunk:
    """One exact, contiguous source slice for semantic decomposition.

    ``text`` is always exactly ``source[start:end]``. Structural chunking never
    paraphrases or drops source text; it only chooses deterministic boundaries.
    """

    text: str
    start: int
    end: int


_DEFAULT_SOURCE_CHUNK_MAX_CHARS = 2500
_DEFAULT_SOURCE_CHUNK_TARGET_CHARS = 2000
_MIN_HARD_SPLIT_FRACTION = 0.60

_MARKDOWN_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+\S")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(```+|~~~+)")


def split_source_for_decomposition(
    text: str,
    *,
    max_chars: int = _DEFAULT_SOURCE_CHUNK_MAX_CHARS,
    target_chars: int = _DEFAULT_SOURCE_CHUNK_TARGET_CHARS,
) -> list[DecompositionSourceChunk]:
    """Deterministically partition a long source into exact semantic-work chunks.

    This is structural segmentation only; it performs no semantic summarization
    or rewriting.

    Boundary preference:
    1. Markdown heading sections.
    2. Paragraph / blank-line blocks.
    3. Line boundaries, useful for lists and tables.
    4. Sentence-like punctuation boundaries.
    5. Whitespace-aware hard cuts as a final fallback.

    The returned chunks form a lossless, ordered partition of ``text``.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if target_chars <= 0:
        raise ValueError("target_chars must be positive")
    if target_chars > max_chars:
        raise ValueError("target_chars cannot exceed max_chars")
    if not text:
        return []

    if len(text) <= max_chars:
        return [DecompositionSourceChunk(text=text, start=0, end=len(text))]

    chunks: list[DecompositionSourceChunk] = []
    for section_start, section_end in _markdown_section_spans(text):
        if section_end - section_start <= max_chars:
            chunks.append(
                DecompositionSourceChunk(
                    text=text[section_start:section_end],
                    start=section_start,
                    end=section_end,
                )
            )
            continue

        for chunk_start, chunk_end in _split_large_span(
            text,
            start=section_start,
            end=section_end,
            max_chars=max_chars,
            target_chars=target_chars,
        ):
            chunks.append(
                DecompositionSourceChunk(
                    text=text[chunk_start:chunk_end],
                    start=chunk_start,
                    end=chunk_end,
                )
            )

    chunks = _merge_small_adjacent_chunks(
        text,
        chunks,
        max_chars=max_chars,
        target_chars=target_chars,
    )

    _validate_source_chunk_partition(text, chunks, max_chars=max_chars)

    logger.debug(
        "Deterministic source chunking complete: chars={} chunks={} max_chars={} target_chars={} sizes={}",
        len(text),
        len(chunks),
        max_chars,
        target_chars,
        [chunk.end - chunk.start for chunk in chunks],
    )
    return chunks


def _markdown_section_spans(text: str) -> list[tuple[int, int]]:
    """Split at Markdown headings while ignoring heading-like text in fences."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return [(0, len(text))]

    heading_starts: list[int] = []
    offset = 0
    fence_marker: str | None = None

    for line in lines:
        stripped_line = line.rstrip("\r\n")
        fence_match = _FENCE_RE.match(stripped_line)

        if fence_match is not None:
            marker_char = fence_match.group(1)[0]
            if fence_marker is None:
                fence_marker = marker_char
            elif marker_char == fence_marker:
                fence_marker = None
        elif fence_marker is None and _MARKDOWN_HEADING_RE.match(stripped_line):
            heading_starts.append(offset)

        offset += len(line)

    starts = [0]
    starts.extend(start for start in heading_starts if start != 0)
    starts = sorted(set(starts))

    spans: list[tuple[int, int]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        if start < end:
            spans.append((start, end))

    return spans or [(0, len(text))]


def _split_large_span(
    text: str,
    *,
    start: int,
    end: int,
    max_chars: int,
    target_chars: int,
) -> list[tuple[int, int]]:
    """Split one oversized structural section without changing source text."""
    paragraph_spans = _paragraph_spans(text, start=start, end=end)
    atomic_spans: list[tuple[int, int]] = []

    for paragraph_start, paragraph_end in paragraph_spans:
        if paragraph_end - paragraph_start <= max_chars:
            atomic_spans.append((paragraph_start, paragraph_end))
        else:
            atomic_spans.extend(
                _split_oversized_block(
                    text,
                    start=paragraph_start,
                    end=paragraph_end,
                    max_chars=max_chars,
                    target_chars=target_chars,
                )
            )

    return _pack_contiguous_spans(
        atomic_spans,
        max_chars=max_chars,
        target_chars=target_chars,
    )


def _paragraph_spans(
    text: str,
    *,
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    """Return exact paragraph-like spans, retaining blank-line separators."""
    segment = text[start:end]
    boundaries = [start]
    for match in re.finditer(r"\r?\n[ \t]*\r?\n+", segment):
        boundaries.append(start + match.end())
    boundaries.append(end)
    boundaries = sorted(set(boundaries))

    spans = [
        (boundaries[index], boundaries[index + 1])
        for index in range(len(boundaries) - 1)
        if boundaries[index] < boundaries[index + 1]
    ]
    return spans or [(start, end)]


def _split_oversized_block(
    text: str,
    *,
    start: int,
    end: int,
    max_chars: int,
    target_chars: int,
) -> list[tuple[int, int]]:
    """Prefer line boundaries, then sentences, then whitespace-aware hard cuts."""
    line_spans = _line_spans(text, start=start, end=end)

    if len(line_spans) > 1:
        expanded: list[tuple[int, int]] = []
        for line_start, line_end in line_spans:
            if line_end - line_start <= max_chars:
                expanded.append((line_start, line_end))
            else:
                expanded.extend(
                    _sentence_or_hard_spans(
                        text,
                        start=line_start,
                        end=line_end,
                        max_chars=max_chars,
                        target_chars=target_chars,
                    )
                )
        return _pack_contiguous_spans(
            expanded,
            max_chars=max_chars,
            target_chars=target_chars,
        )

    return _sentence_or_hard_spans(
        text,
        start=start,
        end=end,
        max_chars=max_chars,
        target_chars=target_chars,
    )


def _line_spans(
    text: str,
    *,
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    """Return exact line spans with newline characters retained."""
    segment = text[start:end]
    spans: list[tuple[int, int]] = []
    cursor = start

    for line in segment.splitlines(keepends=True):
        line_end = cursor + len(line)
        spans.append((cursor, line_end))
        cursor = line_end

    if cursor < end:
        spans.append((cursor, end))
    return spans or [(start, end)]


def _sentence_or_hard_spans(
    text: str,
    *,
    start: int,
    end: int,
    max_chars: int,
    target_chars: int,
) -> list[tuple[int, int]]:
    """Split an oversized single-line block on sentence-like boundaries."""
    segment = text[start:end]
    boundaries = [start]

    for match in re.finditer(r"""[.!?](?:["')\]]+)?[ \t]+""", segment):
        boundaries.append(start + match.end())

    boundaries.append(end)
    boundaries = sorted(set(boundaries))
    candidate_spans = [
        (boundaries[index], boundaries[index + 1])
        for index in range(len(boundaries) - 1)
        if boundaries[index] < boundaries[index + 1]
    ]

    if len(candidate_spans) > 1:
        expanded: list[tuple[int, int]] = []
        for candidate_start, candidate_end in candidate_spans:
            if candidate_end - candidate_start <= max_chars:
                expanded.append((candidate_start, candidate_end))
            else:
                expanded.extend(
                    _hard_split_span(
                        text,
                        start=candidate_start,
                        end=candidate_end,
                        max_chars=max_chars,
                    )
                )
        return _pack_contiguous_spans(
            expanded,
            max_chars=max_chars,
            target_chars=target_chars,
        )

    return _hard_split_span(
        text,
        start=start,
        end=end,
        max_chars=max_chars,
    )


def _hard_split_span(
    text: str,
    *,
    start: int,
    end: int,
    max_chars: int,
) -> list[tuple[int, int]]:
    """Final fallback: exact whitespace-aware cuts bounded by ``max_chars``."""
    spans: list[tuple[int, int]] = []
    cursor = start
    min_cut_distance = max(1, int(max_chars * _MIN_HARD_SPLIT_FRACTION))

    while end - cursor > max_chars:
        hard_limit = cursor + max_chars
        search_floor = cursor + min_cut_distance
        cut = hard_limit

        whitespace_matches = list(re.finditer(r"\s+", text[search_floor:hard_limit]))
        if whitespace_matches:
            cut = search_floor + whitespace_matches[-1].end()
        if cut <= cursor:
            cut = hard_limit

        spans.append((cursor, cut))
        cursor = cut

    if cursor < end:
        spans.append((cursor, end))
    return spans


def _pack_contiguous_spans(
    spans: list[tuple[int, int]],
    *,
    max_chars: int,
    target_chars: int,
) -> list[tuple[int, int]]:
    """Greedily pack adjacent exact spans without crossing ``max_chars``."""
    if not spans:
        return []

    packed: list[tuple[int, int]] = []
    current_start, current_end = spans[0]

    for next_start, next_end in spans[1:]:
        if next_start != current_end:
            raise ValueError("Cannot pack non-contiguous decomposition source spans")

        combined_length = next_end - current_start
        current_length = current_end - current_start

        if combined_length <= max_chars and current_length < target_chars:
            current_end = next_end
        else:
            packed.append((current_start, current_end))
            current_start, current_end = next_start, next_end

    packed.append((current_start, current_end))
    return packed


def _merge_small_adjacent_chunks(
    text: str,
    chunks: list[DecompositionSourceChunk],
    *,
    max_chars: int,
    target_chars: int,
) -> list[DecompositionSourceChunk]:
    """Merge tiny neighboring structural chunks when safely bounded."""
    if len(chunks) <= 1:
        return chunks

    merged: list[DecompositionSourceChunk] = []
    current = chunks[0]

    for next_chunk in chunks[1:]:
        if current.end != next_chunk.start:
            raise ValueError("Source chunks must be contiguous before merging")

        combined_length = next_chunk.end - current.start
        current_length = current.end - current.start
        next_length = next_chunk.end - next_chunk.start

        should_merge = combined_length <= max_chars and (
            current_length < target_chars // 2 or next_length < target_chars // 2
        )

        if should_merge:
            current = DecompositionSourceChunk(
                text=text[current.start : next_chunk.end],
                start=current.start,
                end=next_chunk.end,
            )
        else:
            merged.append(current)
            current = next_chunk

    merged.append(current)
    return merged


def _validate_source_chunk_partition(
    text: str,
    chunks: list[DecompositionSourceChunk],
    *,
    max_chars: int,
) -> None:
    """Assert that chunking is an exact, gap-free partition of the source."""
    if not chunks:
        raise ValueError("Non-empty source produced no decomposition chunks")

    cursor = 0
    reconstructed: list[str] = []

    for index, chunk in enumerate(chunks):
        if chunk.start != cursor:
            raise ValueError(
                f"Source chunk gap/overlap before chunk[{index}]: "
                f"expected start={cursor}, actual={chunk.start}"
            )
        if chunk.end <= chunk.start:
            raise ValueError(f"Source chunk[{index}] must have positive length")
        if chunk.text != text[chunk.start : chunk.end]:
            raise ValueError(f"Source chunk[{index}] text does not match its source span")
        if chunk.end - chunk.start > max_chars:
            raise ValueError(f"Source chunk[{index}] exceeds max_chars={max_chars}")

        reconstructed.append(chunk.text)
        cursor = chunk.end

    if cursor != len(text):
        raise ValueError("Source chunk partition does not reach the end of the source")
    if "".join(reconstructed) != text:
        raise ValueError("Source chunk partition does not reconstruct the source exactly")
