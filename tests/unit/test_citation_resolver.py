# tests/unit/test_citation_resolver.py
from types import SimpleNamespace

import pytest

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
                text=f"chunk text {i}",
                filename=f"f{i}.pdf",
                page=i + 1,
                scope="agent",
                score=0.8,
                section_path="",
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


@pytest.mark.parametrize("marker", ["[s1]", "［s1］", "【s1】", "〔s1〕"])
def test_square_bracket_family_resolves_to_ascii(marker):
    """Models drift from ASCII [sN]; the whole square-bracket family (ASCII,
    fullwidth ［］, lenticular 【】, tortoise-shell 〔〕) must detect + resolve and
    rewrite to ASCII [n] so the frontend chip injector works. Regression: a chat
    answer used 【s1】 → citations weren't clickable and no sources showed."""
    assert has_citation_markers(f"open {marker} done")
    text, sources = resolve_citations(f"open {marker} done", _ledger_with())
    assert text == "open [1] done"
    assert sources[0]["cite_id"] == "s1"


def test_unsupported_bracket_style_is_logged_not_silent(caplog):
    """A cite_id in a bracket style we do NOT resolve, e.g. (s1), must emit a
    WARNING so silent model-format drift is visible (the failure mode the 【】 bug
    had). It is left as-is, not rendered."""
    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="cuga.backend.knowledge.sources"):
        text, sources = resolve_citations("per the goal (s1) holds", _ledger_with())
    assert "(s1)" in text  # not rewritten
    assert sources == []
    assert "unsupported bracket style" in caplog.text


def test_unsupported_marker_inside_code_is_not_warned(caplog):
    """A foo(s1)-style call inside inline code must NOT trip the drift canary."""
    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="cuga.backend.knowledge.sources"):
        resolve_citations("real [s1] then `foo(s1)` call", _ledger_with())
    assert "unsupported bracket style" not in caplog.text


def test_strip_mode_does_not_warn(caplog):
    """fix 4: strip-mode (ledger is None, feature off) removes markers silently —
    no per-marker 'not in ledger' warnings that would mask real misses."""
    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="cuga.backend.knowledge.sources"):
        text, sources = resolve_citations("orphan [s1] and [s2]", None)
    assert text == "orphan  and "
    assert sources == []
    assert "not in ledger" not in caplog.text


def test_real_ledger_miss_still_warns(caplog):
    """fix 4: a genuine miss (hallucinated/evicted id with a live ledger) still warns."""
    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="cuga.backend.knowledge.sources"):
        resolve_citations("real [s1] fake [s9]", _ledger_with())
    assert "not in ledger" in caplog.text


# --- fix 2: code-fence guard extended ----------------------------------------


def test_unterminated_fence_protects_marker():
    """Truncated LLM output: unterminated ``` fence — [s2] inside must survive."""
    raw = "intro [s1]\n```py\nprint(arr[s2])\n# never closed"
    text, sources = resolve_citations(raw, _ledger_with())
    assert "arr[s2]" in text  # protected — not resolved
    assert text.startswith("intro [1]")
    assert len(sources) == 1


def test_double_backtick_inline_protects_marker():
    """Double-backtick inline span — [s2] inside must survive."""
    raw = "``x[s2]`` inline [s1]"
    text, sources = resolve_citations(raw, _ledger_with())
    assert "x[s2]" in text  # protected
    assert "[1]" in text  # [s1] resolved
    assert len(sources) == 1


# --- fix 9: whitespace separator in marker list ------------------------------


def test_space_separated_marker_list():
    text, sources = resolve_citations("x [s1 s3].", _ledger_with())
    assert text == "x [1][2]."
    assert len(sources) == 2


# --- turn scoping: a citation must point at THIS turn's retrieval ------------
#
# Regression for the mis-attribution bug: the model was handed a stripped-of-
# provenance answer plus stale cite_ids from earlier questions in the same
# thread, and cited [s1] (an AppWorld-benchmark chunk) for a scholarship
# answer. A cite_id from an earlier turn must resolve like a ledger miss:
# stripped, so the answer renders correct-and-uncited, never confidently wrong.


def _chunk(text, filename="f.pdf", page=1):
    return SimpleNamespace(text=text, filename=filename, page=page, scope="agent", score=0.8, section_path="")


def test_marker_from_earlier_turn_is_stripped(caplog):
    import logging

    ledger = SourceLedger()
    ledger.register(_chunk("turn-1 benchmark text"), query="benchmarks")  # s1
    ledger.begin_turn()
    ledger.register(
        _chunk("turn-2 scholarship text", filename="milga.pdf", page=13), query="scholarship"
    )  # s2

    with caplog.at_level(logging.WARNING):
        text, sources = resolve_citations("you have 5 installments [s1].", ledger)

    assert text == "you have 5 installments ."  # stale marker stripped (surrounding space kept)
    assert sources == []
    assert any("s1" in r.message and "not from this turn" in r.message for r in caplog.records)


def test_current_turn_marker_still_resolves():
    ledger = SourceLedger()
    ledger.register(_chunk("turn-1 benchmark text"), query="benchmarks")  # s1
    ledger.begin_turn()
    ledger.register(
        _chunk("turn-2 scholarship text", filename="milga.pdf", page=13), query="scholarship"
    )  # s2

    text, sources = resolve_citations("you have 5 installments [s2]", ledger)

    assert text == "you have 5 installments [1]"
    assert len(sources) == 1
    assert sources[0]["filename"] == "milga.pdf"


def test_chunk_re_retrieved_this_turn_resolves():
    """Same content pulled again this turn is legitimately current-turn evidence."""
    ledger = SourceLedger()
    first = ledger.register(_chunk("stable content", page=3), query="q1")  # s1
    ledger.begin_turn()
    again = ledger.register(_chunk("stable content", page=3), query="q2")  # same key -> s1
    assert first == again == "s1"

    text, sources = resolve_citations("fact [s1]", ledger)
    assert text == "fact [1]"
    assert len(sources) == 1


def test_begin_turn_alone_scopes_out_prior_ids():
    ledger = _ledger_with(2)  # s1, s2 registered "last turn"
    ledger.begin_turn()  # new turn, no new retrieval
    text, sources = resolve_citations("a [s1]. b [s2].", ledger)
    assert text == "a . b ."
    assert sources == []


# --- HITL resume must NOT wipe this turn's scope (the review's blocker) -------
#
# A tool-approval / clarifying-question resume re-enters the stream but is the
# SAME logical turn; the pre-interrupt search does not re-run. So begin_turn()
# must fire only on a genuinely new turn, never on resume — else the answer
# composed after resume loses its legitimate citations.


def test_scope_survives_across_resolves_without_new_begin_turn():
    """Registered ids keep resolving until the NEXT begin_turn (= next new turn).
    A resume, which does not call begin_turn, therefore preserves the scope."""
    ledger = SourceLedger()
    ledger.begin_turn()  # new turn
    ledger.register(_chunk("pre-interrupt search result"), query="q")  # s1

    # First resolve (e.g. a partial), then a second resolve after a HITL resume.
    # No begin_turn() in between (resume path returns early) -> still resolves.
    for _ in range(2):
        text, sources = resolve_citations("answer [s1]", ledger)
        assert text == "answer [1]"
        assert len(sources) == 1


def test_a_new_begin_turn_would_have_stripped_it():
    """Guard: proves the previous test is meaningful — calling begin_turn()
    between register and resolve (the bug we avoid on resume) DOES strip."""
    ledger = SourceLedger()
    ledger.begin_turn()
    ledger.register(_chunk("pre-interrupt search result"), query="q")  # s1
    ledger.begin_turn()  # wrongful reset (what a resume must NOT do)
    text, sources = resolve_citations("answer [s1]", ledger)
    assert text == "answer "
    assert sources == []


# --- begin_ledger_turn module helper (SDK + server share it) -----------------


def test_begin_ledger_turn_resets_scope_for_thread():
    from cuga.backend.knowledge.sources import (
        _reset_all_ledgers_for_tests,
        begin_ledger_turn,
        get_ledger,
    )

    _reset_all_ledgers_for_tests()
    ledger = get_ledger("thread-A")
    ledger.register(_chunk("last turn"), query="q")  # s1, in scope

    begin_ledger_turn("thread-A")  # new turn boundary

    text, sources = resolve_citations("stale [s1]", ledger)
    assert text == "stale "  # scope was reset -> stripped
    assert sources == []
    _reset_all_ledgers_for_tests()


def test_begin_ledger_turn_is_noop_without_ledger():
    from cuga.backend.knowledge.sources import begin_ledger_turn, get_ledger

    # Must not create a ledger for a thread that has none (citations off / chit-chat).
    begin_ledger_turn("never-seen-thread")
    assert get_ledger("never-seen-thread", create=False) is None
