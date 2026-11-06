"""SDK surface for knowledge citations: InvokeResult.sources + enable_citations."""

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from cuga.backend.knowledge.sources import _reset_all_ledgers_for_tests, get_ledger
from cuga.sdk import InvokeResult
import pytest

pytestmark = pytest.mark.unit


def setup_function():
    _reset_all_ledgers_for_tests()


def test_invoke_result_sources_default_and_roundtrip():
    assert InvokeResult().sources == []
    r = InvokeResult(answer="x [1]", sources=[{"n": 1, "cite_id": "s1", "filename": "f.pdf"}])
    assert r.sources[0]["filename"] == "f.pdf"
    assert str(r) == "x [1]"


def test_enable_citations_param_stored():
    from cuga.sdk import CugaAgent

    agent = CugaAgent(enable_citations=False)
    assert agent._enable_citations is False


def test_enable_citations_defaults_to_none():
    from cuga.sdk import CugaAgent

    agent = CugaAgent()
    assert agent._enable_citations is None


class _StubGraph:
    """Minimal stand-in for the compiled LangGraph graph in invoke()."""

    def __init__(self, result: dict, on_ainvoke=None):
        self._result = result
        # Optional side effect run when the graph executes — models the searches
        # a real turn performs (which register cite_ids for THIS turn).
        self._on_ainvoke = on_ainvoke

    async def ainvoke(self, *_args, **_kwargs):
        if self._on_ainvoke is not None:
            self._on_ainvoke()
        return self._result

    async def aget_state(self, *_args, **_kwargs):
        # values=None → invoke() finds no existing state; next=() → not interrupted.
        return SimpleNamespace(values=None, next=())

    def get_state(self, *_args, **_kwargs):
        raise AssertionError("invoke() must read state via aget_state")


def test_invoke_empty_answer_fallback_resolves_markers_and_overrides_stale_sources(monkeypatch):
    """When final_answer is empty, invoke() falls back to the last AI chat
    message. That text bypassed FinalAnswerNode resolution, so the SDK must
    resolve [sN] markers locally AND its freshly-resolved sources must
    supersede the stale ``sources`` riding the graph result."""
    from cuga.sdk import CugaAgent

    agent = CugaAgent(auto_load_policies=False)

    async def _noop_initialized():
        return None

    monkeypatch.setattr(agent, "_ensure_initialized", _noop_initialized)

    thread_id = "sdk-fallback-thread"
    chunk = SimpleNamespace(
        text="chunk text", filename="report.pdf", page=4, scope="agent", score=0.9, section_path=""
    )

    # invoke() starts a NEW turn and resets the citation scope, so [s1] resolves
    # only if it was retrieved THIS turn. Model that: the graph's search this
    # turn registers the chunk (content-keyed -> s1, now in-turn scope).
    def _search_registers_this_turn():
        assert get_ledger(thread_id).register(chunk, query="q") == "s1"

    # invoke() reads the compiled graph via the ``graph`` property; a
    # pre-set _compiled_graph short-circuits graph construction entirely.
    agent._compiled_graph = _StubGraph(
        {
            "final_answer": "",
            "chat_messages": [AIMessage(content="answer [s1]")],
            "sources": [{"n": 9, "cite_id": "stale"}],
            "variables_storage": {},
            "tool_calls": [],
        },
        on_ainvoke=_search_registers_this_turn,
    )

    result = asyncio.run(agent.invoke("question", thread_id=thread_id))

    assert result.answer == "answer [1]"
    # Fallback-resolved sources override the stale result sources.
    assert len(result.sources) == 1
    assert result.sources[0]["cite_id"] == "s1"
    assert result.sources[0]["n"] == 1
    assert result.sources[0]["filename"] == "report.pdf"


def test_invoke_empty_answer_fallback_checks_citations_like_the_final_answer(monkeypatch):
    """The fallback text bypassed FinalAnswerNode, so it must get the same citation check."""
    import json

    from cuga.backend.cuga_graph.nodes.answer import final_answer
    from cuga.backend.knowledge import sources
    from cuga.sdk import CugaAgent

    agent = CugaAgent(auto_load_policies=False)

    async def _noop_initialized():
        return None

    async def _supported(prompt: str) -> str:
        return json.dumps({"1": "supported"})

    monkeypatch.setattr(agent, "_ensure_initialized", _noop_initialized)
    monkeypatch.setattr(final_answer, "_citation_check_llm", lambda: SimpleNamespace(generate=_supported))
    sources.set_agent_citation_check_lookup(lambda: "llm")
    thread_id = "sdk-fallback-check"
    chunk = SimpleNamespace(
        text="Invoice INV-9 was issued to Contoso.",
        filename="inv.txt",
        page=1,
        scope="agent",
        score=0.9,
        section_path="",
    )
    agent._compiled_graph = _StubGraph(
        {
            "final_answer": "",
            "chat_messages": [AIMessage(content="Issued to Contoso [s1].")],
            "variables_storage": {},
            "tool_calls": [],
        },
        on_ainvoke=lambda: get_ledger(thread_id).register(chunk, query="q"),
    )
    try:
        result = asyncio.run(agent.invoke("question", thread_id=thread_id))
    finally:
        sources.set_agent_citation_check_lookup(None)

    assert result.answer == "Issued to Contoso [1]."
    assert [(s["filename"], s["support"]) for s in result.sources] == [("inv.txt", "supported")]
