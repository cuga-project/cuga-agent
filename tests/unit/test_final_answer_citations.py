from types import SimpleNamespace

from cuga.backend.cuga_graph.nodes.answer.final_answer import FinalAnswerNode
from cuga.backend.knowledge.sources import get_ledger, _reset_all_ledgers_for_tests


class _State(SimpleNamespace):
    pass


def _state(answer, thread_id="t-x"):
    return _State(final_answer=answer, thread_id=thread_id, sources=[])


def setup_function():
    _reset_all_ledgers_for_tests()


def _seed_ledger(thread_id="t-x"):
    ledger = get_ledger(thread_id)
    ledger.register(
        SimpleNamespace(text="chunk", filename="f.pdf", page=2, scope="agent", score=0.9, section_path=""),
        query="q",
    )


def test_resolves_markers_and_sets_sources():
    _seed_ledger()
    state = _state("answer [s1] done")
    FinalAnswerNode.apply_citation_resolution(state)
    assert state.final_answer == "answer [1] done"
    assert state.sources[0]["filename"] == "f.pdf"


def test_no_markers_sets_empty_sources_without_ledger_creation():
    state = _state("plain answer", thread_id="never-seen-thread")
    FinalAnswerNode.apply_citation_resolution(state)
    assert state.final_answer == "plain answer"
    assert state.sources == []
    assert get_ledger("never-seen-thread", create=False) is None


def test_hallucinated_marker_stripped_even_without_ledger():
    state = _state("fake [s7] claim", thread_id="fresh-thread")
    FinalAnswerNode.apply_citation_resolution(state)
    assert state.final_answer == "fake  claim"
    assert state.sources == []


def test_resolution_errors_never_break_the_answer(monkeypatch):
    _seed_ledger()
    state = _state("answer [s1]")
    monkeypatch.setattr(
        "cuga.backend.knowledge.sources.resolve_citations",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    FinalAnswerNode.apply_citation_resolution(state)  # must not raise
    assert state.final_answer == "answer [s1]"  # untouched on failure
