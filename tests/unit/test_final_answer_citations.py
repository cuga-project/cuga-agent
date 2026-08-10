from types import SimpleNamespace

from cuga.backend.cuga_graph.nodes.answer.final_answer import (
    FinalAnswerNode,
    _harmony_stripping_enabled as _REAL_GATE,
)
from cuga.backend.knowledge.sources import get_ledger, _reset_all_ledgers_for_tests
import pytest

pytestmark = pytest.mark.unit


# The harmony strip is gated on advanced_features.strip_harmony_control_tokens,
# whose "auto" default resolves against the configured final-answer model. Pin it
# for the strip tests so they assert the filter, not whichever model the ambient
# settings happen to name (the gate itself is covered separately below).
@pytest.fixture(autouse=True)
def _force_harmony_stripping(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.answer.final_answer._harmony_stripping_enabled",
        lambda: True,
    )


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


def test_chat_history_copy_is_sanitized(monkeypatch):
    """The chat-history copy is appended before apply_citation_resolution runs,
    so it must be stripped of control tokens independently."""
    import asyncio
    import json as _json
    from unittest.mock import AsyncMock, MagicMock

    from langchain_core.messages import AIMessage

    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.answer.final_answer.settings",
        SimpleNamespace(features=SimpleNamespace(chat=True)),
    )
    state = _make_agent_state(chat_agent_messages=[AIMessage(content="")])
    payload = _json.dumps({"thoughts": [], "final_answer": "The total is 42<|return|>"})
    agent = MagicMock()
    agent.run = AsyncMock(return_value=AIMessage(content=payload))

    asyncio.run(FinalAnswerNode._generate_final_answer(state, agent, "FinalAnswerAgent"))

    assert "<|" not in state.chat_agent_messages[-1].content
    assert "The total is 42" in state.chat_agent_messages[-1].content
    assert state.final_answer == "The total is 42"


def test_control_tokens_stripped_from_uncited_answer():
    state = _state("The total is 42<|return|>")
    FinalAnswerNode.apply_citation_resolution(state)
    assert "<|" not in state.final_answer
    assert state.final_answer == "The total is 42"


def test_indentation_after_a_token_is_preserved():
    """The filter removes tokens and nothing else: a token directly before an
    indented block must not take that block's indentation with it."""
    state = _state("<|message|>    def foo():\n        return 1")
    FinalAnswerNode.apply_citation_resolution(state)
    assert state.final_answer == "    def foo():\n        return 1"


def test_legitimate_special_token_text_is_preserved():
    """Only the harmony protocol vocabulary is stripped — an answer that
    legitimately discusses <|...|>-style markers must pass through untouched."""
    text = "Custom markers like <|custom|> or <|im_end|> delimit segments."
    state = _state(text)
    FinalAnswerNode.apply_citation_resolution(state)
    assert state.final_answer == text


# ── The gate: which runs the filter applies to (review request, #558) ────────


def _reload_gate():
    """The real gate function, captured at import time so the autouse
    ``_force_harmony_stripping`` fixture (which replaces the module attribute)
    doesn't shadow the thing these tests are meant to exercise."""
    return _REAL_GATE


def test_gate_auto_enables_only_for_harmony_models(monkeypatch):
    """auto = strip for gpt-oss, leave every other provider alone."""
    gate = _reload_gate()
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.answer.final_answer.settings",
        SimpleNamespace(
            advanced_features=SimpleNamespace(strip_harmony_control_tokens="auto"),
            agent=SimpleNamespace(
                final_answer=SimpleNamespace(model=SimpleNamespace(model_name="openai/gpt-oss-120b"))
            ),
        ),
    )
    assert gate() is True

    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.answer.final_answer.settings",
        SimpleNamespace(
            advanced_features=SimpleNamespace(strip_harmony_control_tokens="auto"),
            agent=SimpleNamespace(final_answer=SimpleNamespace(model=SimpleNamespace(model_name="gpt-4o"))),
        ),
    )
    assert gate() is False


def test_gate_can_be_forced_on_or_off(monkeypatch):
    gate = _reload_gate()
    for value, expected in (("false", False), ("true", True), (False, False), (True, True)):
        monkeypatch.setattr(
            "cuga.backend.cuga_graph.nodes.answer.final_answer.settings",
            SimpleNamespace(
                advanced_features=SimpleNamespace(strip_harmony_control_tokens=value),
                agent=SimpleNamespace(
                    final_answer=SimpleNamespace(model=SimpleNamespace(model_name="openai/gpt-oss-120b"))
                ),
            ),
        )
        assert gate() is expected, f"{value!r} should resolve to {expected}"


def test_non_harmony_run_leaves_tokens_untouched(monkeypatch):
    """End-to-end: with the gate closed the answer passes through byte-identical,
    so a non-gpt-oss provider's output is never altered by this filter."""
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.answer.final_answer._harmony_stripping_enabled",
        lambda: False,
    )
    text = "The total is 42<|return|>"
    state = _state(text)
    FinalAnswerNode.apply_citation_resolution(state)
    assert state.final_answer == text


def test_control_tokens_stripped_from_cited_answer():
    _seed_ledger()
    state = _state("answer [s1] done<|channel|>")
    FinalAnswerNode.apply_citation_resolution(state)
    assert "<|" not in state.final_answer
    assert state.final_answer == "answer [1] done"
    assert state.sources[0]["filename"] == "f.pdf"


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


def test_idempotent_keeps_sources_on_already_resolved_text():
    """MAJ-2: the supervisor callback can re-enter apply_citation_resolution with
    an already-resolved last_planner_answer ([n] chips, no [sN]). The second call
    must KEEP the sources the first produced, not clear them."""
    _seed_ledger()
    state = _state("answer [s1] done")
    FinalAnswerNode.apply_citation_resolution(state)
    assert state.final_answer == "answer [1] done"
    assert len(state.sources) == 1
    first = state.sources

    # Re-enter on the already-resolved text (has [1], no [sN]).
    FinalAnswerNode.apply_citation_resolution(state)
    assert state.final_answer == "answer [1] done"  # unchanged
    assert state.sources == first  # NOT clobbered


def test_uncited_answer_still_clears_stale_sources():
    """The keep-sources guard must not leak: an answer with NO markers at all
    (fresh, or stale prior-turn sources) still clears."""
    _seed_ledger()
    state = _state("a plain answer with no citations")
    state.sources = [{"n": 1, "cite_id": "s1", "filename": "stale.pdf"}]  # stale
    FinalAnswerNode.apply_citation_resolution(state)
    assert state.sources == []
