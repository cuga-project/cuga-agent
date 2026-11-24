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


def test_strict_without_a_checker_is_rejected_when_the_agent_is_built():
    """Agent setup only logs a knowledge error, so this must fail before it, not disable knowledge."""
    from cuga.sdk import CugaAgent

    with pytest.raises(ValueError, match="citation_strict needs citation_check"):
        CugaAgent(citation_strict=True)
    CugaAgent(citation_check="nli", citation_strict=True)


def test_strict_from_the_settings_is_checked_against_the_agent_s_checker(monkeypatch):
    """Strict mode set in the knowledge settings with the agent's checker turned off would fail
    later, in agent setup, and leave the agent without knowledge."""
    from cuga.config import settings
    from cuga.sdk import CugaAgent

    monkeypatch.setitem(settings.get("knowledge"), "citation_strict", True)
    monkeypatch.setitem(settings.get("knowledge"), "citation_check", "llm")
    with pytest.raises(ValueError, match="citation_strict needs citation_check"):
        CugaAgent(citation_check="off")
    CugaAgent(citation_check="off", citation_strict=False)
    CugaAgent()


def test_citation_check_param_rejects_unknown_checkers():
    from cuga.sdk import CugaAgent

    with pytest.raises(ValueError, match="citation_check must be"):
        CugaAgent(citation_check="reranker")


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


def test_invoke_empty_answer_fallback_honours_strict_mode(monkeypatch):
    """Strict mode holds on the fallback path too: a contradicted citation is stripped."""
    import json

    from cuga.backend.cuga_graph.nodes.answer import final_answer
    from cuga.backend.knowledge import sources
    from cuga.sdk import CugaAgent

    agent = CugaAgent(auto_load_policies=False)

    async def _noop_initialized():
        return None

    async def _contradicted(prompt: str) -> str:
        return json.dumps({"1": "contradicted"})

    monkeypatch.setattr(agent, "_ensure_initialized", _noop_initialized)
    monkeypatch.setattr(final_answer, "_citation_check_llm", lambda: SimpleNamespace(generate=_contradicted))
    sources.set_agent_citation_check_lookup(lambda: "llm")
    sources.set_agent_citation_strict_lookup(lambda: True)
    thread_id = "sdk-fallback-strict"
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
            "chat_messages": [AIMessage(content="Issued to Globex [s1].")],
            "variables_storage": {},
            "tool_calls": [],
        },
        on_ainvoke=lambda: get_ledger(thread_id).register(chunk, query="q"),
    )
    try:
        result = asyncio.run(agent.invoke("question", thread_id=thread_id))
    finally:
        sources.set_agent_citation_check_lookup(None)
        sources.set_agent_citation_strict_lookup(None)

    assert (result.answer, result.sources) == ("Issued to Globex .", [])


class _Engine:
    """A knowledge engine stand-in with the live ``config`` the answer path reads."""

    def __init__(self, config):
        self._config = config

    @property
    def config(self):
        return self._config


def _agent_with_knowledge(monkeypatch, **kwargs):
    from cuga.sdk import CugaAgent

    monkeypatch.setattr("cuga.backend.knowledge.engine.KnowledgeEngine", lambda config, **_: _Engine(config))
    monkeypatch.setattr(
        "cuga.backend.knowledge.client.KnowledgeClient",
        lambda engine, **_: SimpleNamespace(_engine=engine, engine=engine),
    )
    agent = CugaAgent(enable_knowledge=True, **kwargs)
    assert agent.knowledge is not None  # builds the agent's engine
    return agent


def _in_run(agent, read):
    """Call ``read()`` inside a graph run configured the way agent.invoke()/stream() configure it."""
    from typing import TypedDict

    from langgraph.graph import END, StateGraph

    class _State(TypedDict):
        out: object

    graph = StateGraph(_State)
    graph.add_node("final_answer", lambda state: {"out": read()})
    graph.set_entry_point("final_answer")
    graph.add_edge("final_answer", END)
    run_config = {"configurable": {}}
    agent._inject_knowledge_to_config(run_config)
    return asyncio.run(graph.compile().ainvoke({"out": None}, config=run_config))["out"]


def _citation_settings():
    from cuga.backend.knowledge import sources

    return (
        sources.effective_citations_enabled(None),
        sources.effective_citation_check(),
        sources.effective_citation_strict(),
        sources.effective_citation_search_retry(),
    )


def test_citation_params_apply_to_the_agent_s_own_runs(monkeypatch):
    """CugaAgent(citation_check=..., citation_strict=...) override the knowledge settings for
    this agent's runs, and the "llm" checker uses this agent's model."""
    from cuga.backend.cuga_graph.nodes.answer import final_answer

    agent = _agent_with_knowledge(monkeypatch, citation_check="nli", citation_strict=True)
    agent._model = SimpleNamespace(name="agent's model")

    assert _in_run(agent, _citation_settings) == (True, "nli", True, True)
    assert _in_run(agent, lambda: final_answer._citation_check_llm()._model) is agent._model


def test_two_agents_in_one_process_keep_their_own_citation_settings(monkeypatch):
    """Building a second agent (the server builds one per spawned sub-agent) used to replace the
    first agent's settings, strict mode included, and the server's own lookups."""
    from cuga.backend.knowledge import sources

    sources.set_agent_citation_check_lookup(lambda: "off")  # the server's published setting
    try:
        strict = _agent_with_knowledge(monkeypatch, citation_check="llm", citation_strict=True)
        plain = _agent_with_knowledge(monkeypatch, enable_citations=False, citation_check="off")

        assert _in_run(strict, _citation_settings) == (True, "llm", True, True)
        assert _in_run(plain, _citation_settings) == (False, "off", False, True)
        assert sources.effective_citation_check() == "off"  # outside an SDK run: the server's
    finally:
        sources.set_agent_citation_check_lookup(None)


def test_the_answer_path_reads_the_engines_live_config(monkeypatch):
    """apply_knowledge_config replaces the engine's config; the next run must follow it."""
    from cuga.backend.knowledge.config import KnowledgeConfig

    agent = _agent_with_knowledge(monkeypatch)
    agent.knowledge.engine._config = KnowledgeConfig(
        citation_check="llm", citations_enabled=False, citation_search_retry=False
    )

    assert _in_run(agent, _citation_settings) == (False, "llm", False, False)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"citation_check": "llm", "citation_strict": "false"}, "citation_strict must be a boolean"),
        ({"enable_citations": "yes"}, "citations_enabled must be a boolean"),
        ({"citation_check": "always"}, "citation_check must be one of"),
        ({"citation_check": "reused", "citation_strict": True}, "citation_strict needs citation_check"),
    ],
)
def test_invalid_citation_params_fail_when_the_agent_is_built(kwargs, message):
    """The engine would reject them later, where agent setup only logs the error and the agent
    runs without knowledge."""
    from cuga.sdk import CugaAgent

    with pytest.raises(ValueError, match=message):
        CugaAgent(**kwargs)


def test_the_citation_params_are_keyword_only():
    """Positional callers of the arguments after enable_citations keep their meaning."""
    import inspect

    from cuga.sdk import CugaAgent

    params = inspect.signature(CugaAgent.__init__).parameters
    assert [name for name, p in params.items() if p.kind is p.POSITIONAL_OR_KEYWORD] == [
        "self",
        "tools",
        "tool_provider",
        "model",
        "callbacks",
        "policy_system",
        "special_instructions",
        "cuga_folder",
        "auto_load_policies",
        "reset_policy_storage",
        "filesystem_sync",
        "enable_knowledge",
        "enable_citations",
        "enable_skills",
        "skills_folder",
        "shortlister",
        "final_answer",
        "execution_mode",
        "step_discipline",
    ]
    assert params["citation_check"].kind is params["citation_check"].KEYWORD_ONLY
    assert params["citation_strict"].kind is params["citation_strict"].KEYWORD_ONLY
