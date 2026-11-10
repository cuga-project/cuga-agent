"""An answer that cites sources while no search ran this turn is sent back to search once (#1124)."""

from types import SimpleNamespace

import pytest

from cuga.backend.knowledge import sources

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_ledgers():
    sources._reset_all_ledgers_for_tests()
    yield
    sources._reset_all_ledgers_for_tests()


def _chunk(text):
    return SimpleNamespace(text=text, filename="leave_policy.txt", page=None, scope="session")


def test_citing_with_nothing_retrieved_asks_for_one_search_per_turn():
    answer = "Up to five days can be carried over [s1]."
    assert sources.needs_search_before_citing(answer, "t1") is True
    assert sources.needs_search_before_citing(answer, "t1") is False, "once per turn"
    sources.begin_ledger_turn("t1")
    assert sources.needs_search_before_citing(answer, "t1") is True, "allowed again next turn"


def test_citing_what_was_retrieved_this_turn_needs_nothing():
    cite_id = sources.get_ledger("t2").register(
        _chunk("Unused leave carries over, at most five days."), query="leave"
    )
    assert sources.needs_search_before_citing(f"Five days [{cite_id}].", "t2") is False


@pytest.mark.parametrize("answer", ["No citation here.", "```\nprint('[s1]')\n```"])
def test_no_citation_in_prose_needs_nothing(answer):
    assert sources.needs_search_before_citing(answer, "t3") is False


def test_citations_off_needs_nothing(monkeypatch):
    monkeypatch.setattr(sources, "effective_citations_enabled", lambda thread_id: False)
    assert sources.needs_search_before_citing("Five days [s1].", "t4") is False


def test_citing_an_earlier_turn_s_source_leaves_it_to_the_citation_check():
    """A follow-up answered from the previous turn's results cites ids still in the ledger. The
    citation check verifies those and keeps the supported ones, which costs one check instead of
    another model turn and a search; only markers that resolve to nothing need the search."""
    cite_id = sources.get_ledger("t5").register(
        _chunk("Unused leave carries over, at most five days."), query="leave"
    )
    sources.begin_ledger_turn("t5")  # the follow-up turn
    assert sources.needs_search_before_citing(f"Five days [{cite_id}].", "t5") is False
    assert sources.needs_search_before_citing(f"Five days [{cite_id}][s99].", "t5") is True
