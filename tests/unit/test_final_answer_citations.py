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


def test_disabled_citations_strip_markers_instead_of_resolving():
    from cuga.backend.knowledge.sources import set_session_override_lookup

    _seed_ledger()
    set_session_override_lookup(lambda tid: {"citations_enabled": False})
    try:
        state = _state("answer [s1] done")
        FinalAnswerNode.apply_citation_resolution(state)
        assert state.final_answer == "answer  done"
        assert state.sources == []
    finally:
        set_session_override_lookup(None)


def test_resolution_errors_never_break_the_answer(monkeypatch):
    _seed_ledger()
    state = _state("answer [s1]")
    monkeypatch.setattr(
        "cuga.backend.knowledge.sources.resolve_citations",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    FinalAnswerNode.apply_citation_resolution(state)  # must not raise
    assert state.final_answer == "answer [s1]"  # untouched on failure
    assert state.sources == []


def _make_agent_state(**overrides):
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    defaults = dict(input="test input", url="https://example.com", thread_id="t-x")
    defaults.update(overrides)
    return AgentState(**defaults)


def test_supervisor_empty_fallback_clears_stale_sources():
    import asyncio

    from cuga.backend.cuga_graph.utils.nodes_names import NodeNames

    state = _make_agent_state(
        sender=NodeNames.CUGA_SUPERVISOR,
        final_answer="",
        last_planner_answer="",
        sources=[{"n": 1, "cite_id": "s1", "filename": "old.pdf"}],
    )
    asyncio.run(FinalAnswerNode.node_handler(state, agent=None, name="FinalAnswerAgent", hitl_handler=None))
    assert state.sources == []


def test_hitl_default_fallback_clears_stale_sources():
    """fix: the HITL default-fallback is a terminal path to END, so it must
    resolve citations too — otherwise stale prior-turn sources ride an
    unresolved answer (every other terminal path already guards this)."""
    from cuga.backend.cuga_graph.nodes.answer.final_answer import HumanInTheLoopHandler
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
        ActionResponse,
        ActionType,
    )

    resp = ActionResponse(
        action_id="unrecognized-action",  # not in the handler map -> default fallback
        response_type=ActionType.BUTTON,
        timestamp="2026-01-01T00:00:00Z",
    )
    state = _make_agent_state(
        final_answer="a plain answer with no markers",
        sources=[{"n": 1, "cite_id": "s1", "filename": "old.pdf"}],
        hitl_response=resp,
    )
    HumanInTheLoopHandler().handle_human_response(state, "FinalAnswerAgent")
    assert state.sources == []  # stale prior-turn sources dropped on the fallback
