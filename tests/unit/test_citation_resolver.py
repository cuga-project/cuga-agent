# tests/unit/test_citation_resolver.py
from types import SimpleNamespace

from cuga.backend.knowledge.sources import (
    SourceLedger,
    has_citation_markers,
    resolve_citations,
)


def _ledger_with(n=3):
    ledger = SourceLedger()
    for i in range(n):
        ledger.register(
            SimpleNamespace(
                text=f"chunk text {i}", filename=f"f{i}.pdf", page=i + 1,
                scope="agent", score=0.8, section_path="",
            ),
            query=f"query {i}",
        )
    return ledger


def test_has_markers_detection():
    assert has_citation_markers("answer [s1] end")
    assert has_citation_markers("multi [s1, s12]")
    assert has_citation_markers("upper [S2]")
    assert not has_citation_markers("plain [1] and [note] text")


def test_renumbers_by_first_appearance():
    text, sources = resolve_citations("b is [s2]. a is [s1]. b again [s2].", _ledger_with())
    assert text == "b is [1]. a is [2]. b again [1]."
    assert [s["n"] for s in sources] == [1, 2]
    assert sources[0]["cite_id"] == "s2"
    assert sources[0]["filename"] == "f1.pdf"
    assert sources[0]["snippet"] == "chunk text 1"


def test_comma_list_expands_to_adjacent_numbers():
    text, sources = resolve_citations("fact [s1, s3].", _ledger_with())
    assert text == "fact [1][2]."
    assert len(sources) == 2


def test_unknown_ids_are_stripped():
    text, sources = resolve_citations("real [s1] fake [s9].", _ledger_with())
    assert text == "real [1] fake ."
    assert len(sources) == 1


def test_mixed_known_unknown_in_one_bracket():
    text, sources = resolve_citations("x [s1, s9].", _ledger_with())
    assert text == "x [1]."
    assert len(sources) == 1


def test_code_fences_and_inline_code_untouched():
    raw = "use [s1].\n```py\nprint(arr[s1])\n```\nand `x[s2]` inline [s2]."
    text, sources = resolve_citations(raw, _ledger_with())
    assert "print(arr[s1])" in text
    assert "`x[s2]`" in text
    assert text.startswith("use [1].")
    assert text.endswith("inline [2].")
    assert len(sources) == 2


def test_marks_records_cited():
    ledger = _ledger_with()
    resolve_citations("cite [s1]", ledger)
    assert ledger.get("s1").cited is True
    assert ledger.get("s2").cited is False


def test_no_markers_returns_text_unchanged_and_empty_sources():
    text, sources = resolve_citations("no citations here", _ledger_with())
    assert text == "no citations here"
    assert sources == []


def test_none_ledger_strips_all_markers():
    text, sources = resolve_citations("orphan [s1]", None)
    assert text == "orphan "
    assert sources == []


# --- fix 2: code-fence guard extended ----------------------------------------

def test_unterminated_fence_protects_marker():
    """Truncated LLM output: unterminated ``` fence — [s2] inside must survive."""
    raw = "intro [s1]\n```py\nprint(arr[s2])\n# never closed"
    text, sources = resolve_citations(raw, _ledger_with())
    assert "arr[s2]" in text          # protected — not resolved
    assert text.startswith("intro [1]")
    assert len(sources) == 1


def test_double_backtick_inline_protects_marker():
    """Double-backtick inline span — [s2] inside must survive."""
    raw = "``x[s2]`` inline [s1]"
    text, sources = resolve_citations(raw, _ledger_with())
    assert "x[s2]" in text            # protected
    assert "[1]" in text              # [s1] resolved
    assert len(sources) == 1


# --- fix 9: whitespace separator in marker list ------------------------------

def test_space_separated_marker_list():
    text, sources = resolve_citations("x [s1 s3].", _ledger_with())
    assert text == "x [1][2]."
    assert len(sources) == 2
