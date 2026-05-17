"""E2E tests for the knowledge component.

Two tiers:
  Tier 1 - component-level (no LLM, no graph): exercises KnowledgeEngine's public
            API (ingest -> search -> delete) and the awareness prompt-injection path.
  Tier 2 - graph-level (CaptureChatModel): runs CugaLite with a mock LLM and asserts
            that the knowledge summary is present in the system message sent to the model.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from cuga.backend.knowledge.awareness import format_knowledge_context, get_knowledge_summary
from cuga.backend.knowledge.engine import KnowledgeEngine

from .conftest import (
    CaptureChatModel,
    KnowledgeToolProvider,
    RealSearchKnowledgeToolProvider,
    extract_system_content,
    poll_task,
    write_skill,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_COLLECTION = "kb_agent_e2e_test"
_MARKER = "KNOWLEDGE_E2E_MARKER_XYZ"


def _make_doc(tmp_path: Path, content: str, filename: str = "test_doc.txt") -> Path:
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


async def _ingest_and_wait(engine: KnowledgeEngine, collection: str, file_path: Path) -> dict:
    result = await engine.ingest(collection, file_path)
    task_id = result["task_id"]
    task = await poll_task(engine, task_id)
    assert task["status"] == "completed", f"Ingest failed: {task}"
    return task


# ---------------------------------------------------------------------------
# Tier 1 - KnowledgeEngine lifecycle
# ---------------------------------------------------------------------------


class TestKnowledgeEngineLifecycle:
    @pytest.mark.asyncio
    async def test_ingest_and_search_retrieves_content(
        self, knowledge_engine: KnowledgeEngine, tmp_path: Path
    ) -> None:
        engine = knowledge_engine
        doc = _make_doc(tmp_path, f"{_MARKER}: secrets of the quantum realm")
        await _ingest_and_wait(engine, _COLLECTION, doc)

        results = await engine.search(_COLLECTION, "quantum realm secrets", limit=5)

        assert len(results) > 0, "Search returned no results"
        combined = " ".join(r.text for r in results)
        assert _MARKER in combined, f"Marker not found in search results: {combined[:200]}"

    @pytest.mark.asyncio
    async def test_delete_removes_document(self, knowledge_engine: KnowledgeEngine, tmp_path: Path) -> None:
        engine = knowledge_engine
        doc = _make_doc(tmp_path, f"{_MARKER}: document to be deleted")
        await _ingest_and_wait(engine, _COLLECTION, doc)

        docs_before = await engine.list_documents(_COLLECTION)
        assert any(d.filename == doc.name for d in docs_before)

        await engine.delete_document(_COLLECTION, doc.name)

        docs_after = await engine.list_documents(_COLLECTION)
        assert not any(d.filename == doc.name for d in docs_after)

    @pytest.mark.asyncio
    async def test_replace_duplicate_updates_content(
        self, knowledge_engine: KnowledgeEngine, tmp_path: Path
    ) -> None:
        engine = knowledge_engine
        filename = "replace_me.txt"
        doc_v1 = _make_doc(tmp_path, "old content ALPHA_MARKER", filename=filename)
        await _ingest_and_wait(engine, _COLLECTION, doc_v1)

        doc_v1.write_text("new content BETA_MARKER", encoding="utf-8")
        await _ingest_and_wait(engine, _COLLECTION, doc_v1)

        results = await engine.search(_COLLECTION, "BETA_MARKER", limit=5)
        combined = " ".join(r.text for r in results)
        assert "BETA_MARKER" in combined, "New content not found after replace"
        assert "ALPHA_MARKER" not in combined, "Old chunks not purged on re-ingest"


# ---------------------------------------------------------------------------
# Tier 1 - Knowledge awareness (prompt-injection path)
# ---------------------------------------------------------------------------


class TestKnowledgeAwareness:
    @pytest.mark.asyncio
    async def test_awareness_summary_contains_ingested_document(
        self, knowledge_engine: KnowledgeEngine, tmp_path: Path
    ) -> None:
        engine = knowledge_engine
        doc = _make_doc(tmp_path, f"{_MARKER}: awareness test content", "awareness_doc.txt")
        await _ingest_and_wait(engine, _COLLECTION, doc)

        summary = await get_knowledge_summary(engine, agent_collection=_COLLECTION)

        assert summary is not None, "Expected a non-None knowledge summary"
        assert doc.name in summary, f"Document filename not in summary: {summary[:300]}"

    @pytest.mark.asyncio
    async def test_awareness_summary_is_none_when_collection_empty(
        self, knowledge_engine: KnowledgeEngine
    ) -> None:
        engine = knowledge_engine
        empty_collection = f"kb_agent_empty_{uuid.uuid4().hex[:8]}"

        summary = await get_knowledge_summary(engine, agent_collection=empty_collection)

        assert summary is None, "Expected None for empty collection"

    def test_format_knowledge_context_returns_correct_collection_names(self) -> None:
        ctx = format_knowledge_context(agent_id="my_agent", thread_id="thread_1")

        assert ctx["agent_collection"] == "kb_agent_my_agent"
        assert ctx["session_collection"] == "kb_sess_thread_1"

    def test_format_knowledge_context_sanitizes_special_characters(self) -> None:
        ctx = format_knowledge_context(agent_id="my-agent/v2", thread_id="thread-abc")

        assert ctx["agent_collection"] == "kb_agent_my_agent_v2"
        assert ctx["session_collection"] == "kb_sess_thread_abc"


# ---------------------------------------------------------------------------
# Tier 1 - RAG retrieval path (tool boundary)
# ---------------------------------------------------------------------------


class TestKnowledgeRagPath:
    """Exercises the RAG retrieval path via the search tool interface.

    The awareness path (Tier 1 above) only verifies that document names reach the
    system prompt. The RAG path is the complementary route: the agent actively calls
    knowledge_search_knowledge(query=...) and receives actual chunk content in return.
    These tests verify that the tool wrapping engine.search() returns real ingested
    content — separately from the awareness injection path.
    """

    @pytest.mark.asyncio
    async def test_knowledge_tool_returns_ingested_content(
        self, knowledge_engine: KnowledgeEngine, tmp_path: Path
    ) -> None:
        engine = knowledge_engine
        doc = _make_doc(tmp_path, f"{_MARKER}: rag retrieval test content")
        await _ingest_and_wait(engine, _COLLECTION, doc)

        provider = RealSearchKnowledgeToolProvider(engine, _COLLECTION)
        tools = await provider.get_all_tools()
        knowledge_tool = next(
            (t for t in tools if getattr(t, "name", "") == "knowledge_search_knowledge"),
            None,
        )
        assert knowledge_tool is not None, "knowledge_search_knowledge tool not found"

        result = await knowledge_tool.ainvoke({"query": "rag retrieval test"})

        assert isinstance(result, str), "Tool must return a string"
        assert _MARKER in result, f"Marker not found in tool result: {result[:200]}"

    @pytest.mark.asyncio
    async def test_knowledge_tool_returns_updated_content_after_replace(
        self, knowledge_engine: KnowledgeEngine, tmp_path: Path
    ) -> None:
        engine = knowledge_engine
        filename = "rag_replace_test.txt"
        doc = _make_doc(tmp_path, "old content ALPHA_MARKER", filename=filename)
        await _ingest_and_wait(engine, _COLLECTION, doc)

        doc.write_text("new content BETA_MARKER", encoding="utf-8")
        await _ingest_and_wait(engine, _COLLECTION, doc)

        provider = RealSearchKnowledgeToolProvider(engine, _COLLECTION)
        tools = await provider.get_all_tools()
        knowledge_tool = next(
            (t for t in tools if getattr(t, "name", "") == "knowledge_search_knowledge"),
            None,
        )
        assert knowledge_tool is not None, "knowledge_search_knowledge tool not found"

        result = await knowledge_tool.ainvoke({"query": "BETA_MARKER"})

        assert "BETA_MARKER" in result, f"Updated content not found in tool result: {result[:200]}"
        assert "ALPHA_MARKER" not in result, "Stale chunk still returned after re-ingest"


# ---------------------------------------------------------------------------
# Tier 2 - CugaLite graph integration
# ---------------------------------------------------------------------------


class TestKnowledgeCugaLiteIntegration:
    @pytest.mark.asyncio
    async def test_knowledge_summary_injected_into_cuga_lite_system_prompt(
        self,
        knowledge_engine: KnowledgeEngine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
            CugaLiteState,
            create_cuga_lite_graph,
        )
        from cuga.config import settings

        # The awareness module looks up collection kb_agent_{agent_id}.
        # Ingest into that exact collection so the summary contains our document.
        agent_id = "e2e_test_agent"
        agent_collection = f"kb_agent_{agent_id}"
        engine = knowledge_engine
        doc = _make_doc(tmp_path, f"{_MARKER}: cuga lite injection test content", "injection_doc.txt")
        await _ingest_and_wait(engine, agent_collection, doc)

        monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
        monkeypatch.setattr(settings.policy, "enabled", False)

        capture_model = CaptureChatModel(responses=[AIMessage(content="I have reviewed the knowledge base.")])
        tool_provider = KnowledgeToolProvider()

        graph = create_cuga_lite_graph(
            model=capture_model,
            tool_provider=tool_provider,
            apps_list=[],
        ).compile()

        thread_id = f"e2e_thread_{uuid.uuid4().hex[:8]}"
        state = CugaLiteState(
            chat_messages=[HumanMessage(content="What documents do you have?")],
            thread_id=thread_id,
        )
        config = {
            "configurable": {
                "thread_id": thread_id,
                "agent_id": agent_id,
                "knowledge_engine": engine,
                "apps_list": [],
            }
        }

        await graph.ainvoke(state, config=config)

        assert capture_model.captured_inputs, "CaptureChatModel was never called"
        system_content = extract_system_content(capture_model.captured_inputs[0])
        assert system_content, "No system message found in LLM inputs"
        assert doc.name in system_content, (
            f"Expected '{doc.name}' in system message, got: {system_content[:500]}"
        )
        assert _MARKER in system_content, (
            f"Expected document content (marker) in system message awareness preview, "
            f"got: {system_content[:500]}"
        )

    @pytest.mark.asyncio
    async def test_session_knowledge_injected_into_cuga_lite_system_prompt(
        self,
        knowledge_engine: KnowledgeEngine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
            CugaLiteState,
            create_cuga_lite_graph,
        )
        from cuga.config import settings

        # Session collection is kb_sess_{thread_id}. Ingest directly into it
        # so the graph's awareness block contains the session doc.
        thread_id = f"e2e_sess_{uuid.uuid4().hex[:8]}"
        session_collection = f"kb_sess_{thread_id}"
        engine = knowledge_engine
        doc = _make_doc(tmp_path, f"{_MARKER}: session-scoped content", "session_doc.txt")
        await _ingest_and_wait(engine, session_collection, doc)

        monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
        monkeypatch.setattr(settings.policy, "enabled", False)

        capture_model = CaptureChatModel(responses=[AIMessage(content="I see your session document.")])
        tool_provider = KnowledgeToolProvider()

        graph = create_cuga_lite_graph(
            model=capture_model,
            tool_provider=tool_provider,
            apps_list=[],
        ).compile()

        # agent_id with no documents — only session collection has content.
        agent_id = f"e2e_empty_agent_{uuid.uuid4().hex[:8]}"
        state = CugaLiteState(
            chat_messages=[HumanMessage(content="What session documents do you see?")],
            thread_id=thread_id,
        )
        config = {
            "configurable": {
                "thread_id": thread_id,
                "agent_id": agent_id,
                "knowledge_engine": engine,
                "apps_list": [],
            }
        }

        await graph.ainvoke(state, config=config)

        assert capture_model.captured_inputs, "CaptureChatModel was never called"
        system_content = extract_system_content(capture_model.captured_inputs[0])
        assert system_content, "No system message found in LLM inputs"
        assert doc.name in system_content, (
            f"Expected session doc '{doc.name}' in system message, got: {system_content[:500]}"
        )
        assert _MARKER in system_content, (
            f"Expected session document content (marker) in system message awareness preview, "
            f"got: {system_content[:500]}"
        )

    @pytest.mark.asyncio
    async def test_knowledge_and_skills_both_appear_in_system_prompt(
        self,
        knowledge_engine: KnowledgeEngine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
            CugaLiteState,
            create_cuga_lite_graph,
        )
        from cuga.config import settings

        agent_id = "e2e_combined_agent"
        agent_collection = f"kb_agent_{agent_id}"
        engine = knowledge_engine
        doc = _make_doc(tmp_path, f"{_MARKER}: combined test content", "combined_doc.txt")
        await _ingest_and_wait(engine, agent_collection, doc)

        monkeypatch.chdir(tmp_path)
        write_skill(tmp_path, "combined_skill", "A skill for combined test", "## Combined skill body")

        monkeypatch.setattr(settings.skills, "enabled", True)
        monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
        monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
        monkeypatch.setattr(settings.policy, "enabled", False)
        # Skills block silently cleared at prompt_utils.py:539-541 when enable_shell_tool=False.
        monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)

        capture_model = CaptureChatModel(responses=[AIMessage(content="I see both knowledge and skills.")])
        tool_provider = KnowledgeToolProvider()

        graph = create_cuga_lite_graph(
            model=capture_model,
            tool_provider=tool_provider,
            apps_list=[],
        ).compile()

        thread_id = f"e2e_combined_{uuid.uuid4().hex[:8]}"
        state = CugaLiteState(
            chat_messages=[HumanMessage(content="What do you know?")],
            thread_id=thread_id,
        )
        config = {
            "configurable": {
                "thread_id": thread_id,
                "agent_id": agent_id,
                "knowledge_engine": engine,
                "apps_list": [],
            }
        }

        await graph.ainvoke(state, config=config)

        assert capture_model.captured_inputs, "CaptureChatModel was never called"
        system_content = extract_system_content(capture_model.captured_inputs[0])
        assert system_content, "No system message found in LLM inputs"
        assert doc.name in system_content, (
            f"Expected knowledge doc '{doc.name}' in system message, got: {system_content[:500]}"
        )
        assert _MARKER in system_content, (
            f"Expected knowledge content (marker) in system message awareness preview, "
            f"got: {system_content[:500]}"
        )
        assert "combined_skill" in system_content, (
            f"Expected skill 'combined_skill' in system message, got: {system_content[:500]}"
        )
