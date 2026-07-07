# tests/unit/test_source_ledger.py
from types import SimpleNamespace

from cuga.backend.knowledge.sources import (
    SourceLedger,
    get_ledger,
    drop_ledger,
    _reset_all_ledgers_for_tests,
)


def _chunk(text="alpha beta", filename="a.pdf", page=1, scope="agent", score=0.9, section_path=""):
    return SimpleNamespace(
        text=text, filename=filename, page=page, scope=scope,
        score=score, section_path=section_path,
    )


def setup_function():
    _reset_all_ledgers_for_tests()


def test_register_assigns_sequential_cite_ids():
    ledger = SourceLedger()
    assert ledger.register(_chunk(text="one"), query="q") == "s1"
    assert ledger.register(_chunk(text="two"), query="q") == "s2"


def test_register_is_idempotent_for_same_content():
    ledger = SourceLedger()
    a = ledger.register(_chunk(text="same", page=3), query="q1")
    b = ledger.register(_chunk(text="same", page=3), query="q2")  # later hop/turn
    assert a == b == "s1"
    assert len(ledger) == 1
    # first retrieving query is kept
    assert ledger.get("s1").query == "q1"


def test_different_page_or_file_is_a_different_source():
    ledger = SourceLedger()
    assert ledger.register(_chunk(text="same", page=3), query="q") == "s1"
    assert ledger.register(_chunk(text="same", page=4), query="q") == "s2"
    assert ledger.register(_chunk(text="same", page=3, filename="b.pdf"), query="q") == "s3"


def test_get_unknown_returns_none():
    assert SourceLedger().get("s99") is None


def test_cap_evicts_oldest_uncited_first():
    ledger = SourceLedger(max_records=3)
    ledger.register(_chunk(text="t1"), query="q")   # s1
    ledger.register(_chunk(text="t2"), query="q")   # s2
    ledger.get("s1").cited = True
    ledger.register(_chunk(text="t3"), query="q")   # s3
    ledger.register(_chunk(text="t4"), query="q")   # s4 -> evicts s2 (oldest uncited)
    assert ledger.get("s1") is not None   # cited survives
    assert ledger.get("s2") is None
    assert ledger.get("s4").cite_id == "s4"


def test_re_retrieval_refreshes_recency_before_eviction():
    """Regression: a re-retrieved (still-uncited) chunk must not be evicted
    before newer one-off chunks — register() move_to_end on a content re-hit."""
    ledger = SourceLedger(max_records=3)
    ledger.register(_chunk(text="t1"), query="q")   # s1
    ledger.register(_chunk(text="t2"), query="q")   # s2
    ledger.register(_chunk(text="t3"), query="q")   # s3
    # re-retrieve t1 (same content) — refreshes its recency to newest
    assert ledger.register(_chunk(text="t1"), query="q2") == "s1"
    ledger.register(_chunk(text="t4"), query="q")   # overflow -> evicts oldest uncited
    assert ledger.get("s1") is not None   # survived: refreshed by re-retrieval
    assert ledger.get("s2") is None       # evicted: now the oldest uncited
    assert ledger.get("s4") is not None


def test_thread_registry_isolated_and_droppable():
    l1 = get_ledger("t-1")
    l2 = get_ledger("t-2")
    assert l1 is not l2
    assert get_ledger("t-1") is l1
    l1.register(_chunk(), query="q")
    drop_ledger("t-1")
    assert len(get_ledger("t-1")) == 0


def test_get_ledger_without_create_returns_none_on_miss():
    assert get_ledger("nope", create=False) is None


# --- restore tests -----------------------------------------------------------

def _snapshot(cite_id="s5", filename="f.pdf", page=1, scope="agent",
              snippet="text", query="q", **kwargs):
    return {"cite_id": cite_id, "filename": filename, "page": page,
            "scope": scope, "snippet": snippet, "query": query, **kwargs}


def test_restore_then_register_continues_numbering():
    ledger = SourceLedger()
    ledger.restore(_snapshot(cite_id="s5"))
    assert ledger.get("s5") is not None
    assert ledger.get("s5").cited is True
    # next new content should get s6
    next_id = ledger.register(_chunk(text="brand new content"), query="q")
    assert next_id == "s6"


def test_restore_duplicate_is_noop_but_still_bumps_counter():
    """Regression for fix 1: duplicate key on restore must still advance counter."""
    ledger = SourceLedger()
    snap_a = _snapshot(cite_id="s2", snippet="shared content")
    snap_b = _snapshot(cite_id="s57", snippet="shared content")  # same key, different id
    ledger.restore(snap_a)
    ledger.restore(snap_b)  # duplicate key -> early return, but counter should reach 57
    assert len(ledger) == 1
    next_id = ledger.register(_chunk(text="something totally new"), query="q")
    assert next_id == "s58"


def test_restore_malformed_cite_id_ignored():
    ledger = SourceLedger()
    ledger.restore(_snapshot(cite_id="x9"))   # wrong prefix
    ledger.restore(_snapshot(cite_id=""))     # empty
    assert len(ledger) == 0


def test_thread_id_whitespace_is_canonicalized():
    assert get_ledger(" t-9 ") is get_ledger("t-9")
    drop_ledger("t-9 ")
    assert get_ledger("t-9", create=False) is None
