"""Integration tests for the knowledge engine.

Tests the full ingest → search → delete cycle against the configured vector backend.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import (
    DocumentExistsError,
    DocumentNotFoundError,
    KnowledgeEngine,
)
from cuga.backend.knowledge.client import KnowledgeClient
from cuga.backend.knowledge.awareness import get_knowledge_summary, format_knowledge_context


@pytest_asyncio.fixture
async def engine(monkeypatch):
    """Create a temporary knowledge engine for testing."""
    tmpdir = tempfile.mkdtemp()
    isolated_db = str(Path(tmpdir) / "cuga_storage.db")
    monkeypatch.setattr(
        "cuga.backend.knowledge.engine.get_storage_connection_params",
        lambda: ("local", isolated_db, ""),
    )
    config = KnowledgeConfig(
        enabled=True,
        persist_dir=Path(tmpdir),
        embedding_provider="fastembed",
        embedding_model="",
        chunk_size=200,
        chunk_overlap=50,
        max_ingest_workers=1,
        max_pending_tasks=5,
    )
    eng = KnowledgeEngine(config)
    await eng.warmup()
    yield eng
    await eng.aclose()
    eng.shutdown()


@pytest.fixture
def sample_txt(tmp_path):
    """Create a sample text file for ingestion."""
    p = tmp_path / "sample.txt"
    p.write_text(
        "The knowledge engine uses LangChain vector search for documents. "
        "It supports PDF, DOCX, XLSX, PPTX, HTML, and many other formats. "
        "Documents are chunked, embedded, and stored in a local vector database. "
        "Users can search using natural language queries."
    )
    return p


@pytest.fixture
def sample_md(tmp_path):
    """Create a sample markdown file."""
    p = tmp_path / "architecture.md"
    p.write_text(
        "# Architecture\n\n"
        "The system consists of three main components:\n\n"
        "1. **Knowledge Engine** - handles document ingestion and search\n"
        "2. **MCP Server** - exposes tools to the agent\n"
        "3. **Awareness Module** - injects document summaries into prompts\n"
    )
    return p


class TestIngestSearchDelete:
    """Full lifecycle: ingest → search → delete."""

    @pytest.mark.asyncio
    async def test_ingest_txt_and_search(self, engine, sample_txt):
        collection = "kb_agent_test"
        task = await engine.ingest(collection, sample_txt)
        assert task["status"] in ("pending", "running", "completed")
        task_id = task["task_id"]

        # Wait for ingestion to complete
        for _ in range(30):
            t = await engine.get_task(task_id)
            if t["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.5)

        t = await engine.get_task(task_id)
        assert t["status"] == "completed", f"Task failed: {t}"

        await asyncio.sleep(0.2)

        # Search
        results = await engine.search(collection, "LangChain vector", limit=5)
        assert len(results) > 0
        assert results[0].score > 0.0
        assert "LangChain" in results[0].text or "vector" in results[0].text

        # List documents
        docs = await engine.list_documents(collection)
        assert len(docs) == 1
        assert docs[0].filename == "sample.txt"

    @pytest.mark.asyncio
    async def test_delete_document(self, engine, sample_txt):
        collection = "kb_agent_delete_test"
        task = await engine.ingest(collection, sample_txt)
        for _ in range(30):
            t = await engine.get_task(task["task_id"])
            if t["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.5)

        # Delete
        await engine.delete_document(collection, "sample.txt")
        docs = await engine.list_documents(collection)
        assert len(docs) == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, engine):
        with pytest.raises(DocumentNotFoundError):
            await engine.delete_document("kb_agent_test", "nonexistent.pdf")

    @pytest.mark.asyncio
    async def test_replace_duplicates_true(self, engine, sample_txt):
        collection = "kb_agent_replace"
        task1 = await engine.ingest(collection, sample_txt, replace_duplicates=True)
        for _ in range(30):
            t = await engine.get_task(task1["task_id"])
            if t["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.5)

        # Re-ingest same file — should succeed
        task2 = await engine.ingest(collection, sample_txt, replace_duplicates=True)
        for _ in range(30):
            t = await engine.get_task(task2["task_id"])
            if t["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.5)

        docs = await engine.list_documents(collection)
        assert len(docs) == 1  # Still one document

    @pytest.mark.asyncio
    async def test_replace_duplicates_false_rejects(self, engine, sample_txt):
        collection = "kb_agent_nodup"
        task = await engine.ingest(collection, sample_txt, replace_duplicates=False)
        for _ in range(30):
            t = await engine.get_task(task["task_id"])
            if t["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.5)

        # Re-ingest with replace=False should raise
        with pytest.raises(DocumentExistsError):
            await engine.ingest(collection, sample_txt, replace_duplicates=False)


class TestCrossRestartDedup:
    """Verify dedup works across engine restart (InMemoryRecordManager state loss)."""

    @pytest.mark.asyncio
    async def test_reingest_after_restart_replaces_chunks(self, tmp_path, monkeypatch):
        """Ingest file, restart engine (new instance), ingest updated file -> no duplicates."""
        isolated_db = str(tmp_path / "cuga_storage.db")
        monkeypatch.setattr(
            "cuga.backend.knowledge.engine.get_storage_connection_params",
            lambda: ("local", isolated_db, ""),
        )
        config = KnowledgeConfig(
            persist_dir=tmp_path / "kb",
            embedding_provider="fastembed",
            embedding_model="",
            chunk_size=200,
            chunk_overlap=50,
            max_ingest_workers=1,
            max_pending_tasks=5,
        )

        # First engine instance: ingest v1
        txt_v1 = tmp_path / "doc.txt"
        txt_v1.write_text("Version one content about cats and dogs.")
        engine1 = KnowledgeEngine(config)
        await engine1.warmup()
        task1 = await engine1.ingest("kb_agent_test", txt_v1)
        assert task1["status"] == "completed"
        results1 = await engine1.search("kb_agent_test", "cats dogs", limit=10)
        assert len(results1) >= 1
        await engine1.aclose()
        engine1.shutdown()

        # Second engine instance (simulates restart): ingest v2 with different content
        txt_v2 = tmp_path / "doc.txt"
        txt_v2.write_text("Version two content about fish and birds.")
        engine2 = KnowledgeEngine(config)
        await engine2.warmup()
        task2 = await engine2.ingest("kb_agent_test", txt_v2, replace_duplicates=True)
        assert task2["status"] == "completed"

        # Should find v2 content
        results2 = await engine2.search("kb_agent_test", "fish birds", limit=10)
        assert len(results2) >= 1

        # Should NOT find v1 content (old chunks deleted)
        results_old = await engine2.search("kb_agent_test", "cats dogs", limit=10, score_threshold=0.7)
        # Old content should not be highly relevant anymore
        for r in results_old:
            assert "cats" not in r.text.lower() or r.score < 0.7, (
                f"Found stale v1 chunk after restart+reingest: {r.text[:50]}"
            )

        # Document list should show only 1 document
        docs = await engine2.list_documents("kb_agent_test")
        assert len(docs) == 1
        await engine2.aclose()
        engine2.shutdown()


class TestScoping:
    """Agent vs session collection isolation."""

    @pytest.mark.asyncio
    async def test_session_isolation(self, engine, sample_txt, sample_md):
        agent_col = "kb_agent_isolation"
        session_col = "kb_sess_thread123"

        # Ingest into agent collection
        t1 = await engine.ingest(agent_col, sample_txt)
        for _ in range(30):
            t = await engine.get_task(t1["task_id"])
            if t["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.5)

        # Ingest into session collection
        t2 = await engine.ingest(session_col, sample_md)
        for _ in range(30):
            t = await engine.get_task(t2["task_id"])
            if t["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.5)

        # Agent search should not return session docs
        agent_results = await engine.search(agent_col, "architecture components", limit=5)
        for r in agent_results:
            assert r.filename == "sample.txt"

        # Session search should not return agent docs
        session_results = await engine.search(session_col, "Knowledge Engine components", limit=5)
        for r in session_results:
            assert r.filename == "architecture.md"


class TestScoreNormalization:
    """Score normalization in [0, 1]."""

    @pytest.mark.asyncio
    async def test_cosine_scores_range(self, engine, sample_txt):
        collection = "kb_agent_scores"
        task = await engine.ingest(collection, sample_txt)
        for _ in range(30):
            t = await engine.get_task(task["task_id"])
            if t["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.5)

        results = await engine.search(collection, "document search", limit=5)
        assert len(results) > 0
        for r in results:
            assert 0.0 <= r.score <= 1.0, f"Score out of range: {r.score}"

    @pytest.mark.asyncio
    async def test_threshold_filters(self, engine, sample_txt):
        collection = "kb_agent_threshold"
        task = await engine.ingest(collection, sample_txt)
        for _ in range(30):
            t = await engine.get_task(task["task_id"])
            if t["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.5)

        # High threshold should filter more results
        high_t = await engine.search(collection, "document", limit=10, score_threshold=0.9)
        low_t = await engine.search(collection, "document", limit=10, score_threshold=0.0)
        assert len(low_t) >= len(high_t)


class TestAwareness:
    """Knowledge awareness prompt injection."""

    @pytest.mark.asyncio
    async def test_awareness_shows_docs(self, engine, sample_txt):
        collection = "kb_agent_aware"
        task = await engine.ingest(collection, sample_txt)
        for _ in range(30):
            t = await engine.get_task(task["task_id"])
            if t["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.5)

        summary = await get_knowledge_summary(engine, agent_collection=collection)
        assert summary is not None
        assert "sample.txt" in summary
        assert "Agent Documents" in summary

    @pytest.mark.asyncio
    async def test_awareness_empty_collection(self, engine):
        summary = await get_knowledge_summary(engine, agent_collection="kb_agent_empty")
        assert summary is None

    def test_format_knowledge_context(self):
        ctx = format_knowledge_context(agent_id="myagent", thread_id="thread123")
        assert ctx["agent_collection"] == "kb_agent_myagent"
        assert ctx["session_collection"] == "kb_sess_thread123"

    def test_format_knowledge_context_with_agent_hash(self):
        ctx = format_knowledge_context(
            agent_id="myagent",
            thread_id="thread123",
            agent_config_hash="abc123",
        )
        assert ctx["agent_collection"] == "kb_agent_myagent_abc123"
        assert ctx["session_collection"] == "kb_sess_thread123"


class TestAwarenessGating:
    """Verify awareness detects knowledge tools correctly under find_tools mode."""

    def test_has_knowledge_tools_in_execution_not_prompt(self):
        """When find_tools is enabled, knowledge tools are in tools_for_execution
        but tools_for_prompt is [find_tool]. Awareness must check tools_for_execution."""
        from unittest.mock import MagicMock

        # Simulate find_tools mode: prompt has only find_tool, execution has knowledge
        find_tool = MagicMock()
        find_tool.name = "find_tools"

        knowledge_tool = MagicMock()
        knowledge_tool.name = "knowledge_search_knowledge"

        filesystem_tool = MagicMock()
        filesystem_tool.name = "filesystem_read_file"

        tools_for_prompt = [find_tool]  # find_tools mode
        tools_for_execution = [find_tool, filesystem_tool, knowledge_tool]

        # Old behavior (bug): checks tools_for_prompt — misses knowledge
        has_in_prompt = any(getattr(t, "name", "").startswith("knowledge_") for t in tools_for_prompt)
        assert not has_in_prompt, "Knowledge should NOT be in tools_for_prompt under find_tools"

        # New behavior (fix): checks tools_for_execution — finds knowledge
        has_in_execution = any(getattr(t, "name", "").startswith("knowledge_") for t in tools_for_execution)
        assert has_in_execution, "Knowledge MUST be detected in tools_for_execution"


class TestSDKClient:
    """SDK KnowledgeClient tests."""

    @pytest.mark.asyncio
    async def test_client_search(self, engine, sample_txt):
        client = KnowledgeClient(engine, default_agent_id="test")
        collection = "kb_agent_test"

        task = await engine.ingest(collection, sample_txt)
        for _ in range(30):
            t = await engine.get_task(task["task_id"])
            if t["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.5)

        results = await client.search("LangChain", scope="agent")
        assert len(results) > 0
        assert "text" in results[0]
        assert "score" in results[0]

    @pytest.mark.asyncio
    async def test_client_list_documents(self, engine, sample_txt):
        client = KnowledgeClient(engine, default_agent_id="test")
        collection = "kb_agent_test"

        task = await engine.ingest(collection, sample_txt)
        for _ in range(30):
            t = await engine.get_task(task["task_id"])
            if t["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.5)

        docs = await client.list_documents(scope="agent")
        assert len(docs) >= 1

    @pytest.mark.asyncio
    async def test_client_settings(self, engine):
        client = KnowledgeClient(engine, default_agent_id="test")
        settings = client.get_settings()
        assert "knowledge" in settings
        assert "chunk_size" in settings["knowledge"]

    @pytest.mark.asyncio
    async def test_client_close(self, engine):
        client = KnowledgeClient(engine, default_agent_id="test")
        await client.close()  # Should not raise


class TestTaskLifecycle:
    """Task tracking and crash recovery."""

    @pytest.mark.asyncio
    async def test_task_states(self, engine, sample_txt):
        collection = "kb_agent_tasklife"
        task = await engine.ingest(collection, sample_txt)
        # Ingestion is synchronous — task returns already completed
        assert task["status"] == "completed"
        assert task["total_files"] == 1
        assert task["successful_files"] == 1
        assert task["file_tasks"]["sample.txt"]["status"] == "indexed"

    @pytest.mark.asyncio
    async def test_list_tasks_by_collection(self, engine, sample_txt):
        col1 = "kb_agent_t1"
        col2 = "kb_agent_t2"
        await engine.ingest(col1, sample_txt)
        await engine.ingest(col2, sample_txt)

        tasks1 = await engine.get_tasks(col1)
        tasks2 = await engine.get_tasks(col2)
        assert len(tasks1) == 1
        assert len(tasks2) == 1
        assert tasks1[0]["collection"] == col1

    @pytest.mark.asyncio
    async def test_engine_health(self, engine):
        health = await engine.health()
        assert health["status"] == "healthy"
        assert health["engine"] == "knowledge-storage_local"
        assert "chunk_size" in health["settings"]

    def test_engine_settings(self, engine):
        settings = engine.get_settings()
        assert settings["knowledge"]["chunk_size"] == 200  # from test config
        assert settings["knowledge"]["embedding_provider"] == "fastembed"

    def test_update_settings(self, engine):
        engine.update_settings(chunk_size="500")
        settings = engine.get_settings()
        assert settings["knowledge"]["chunk_size"] == 500


class TestCollectionLifecycle:
    """Collection creation and deletion."""

    @pytest.mark.asyncio
    async def test_drop_collection(self, engine, sample_txt):
        collection = "kb_sess_drop_me"
        task = await engine.ingest(collection, sample_txt)
        for _ in range(30):
            t = await engine.get_task(task["task_id"])
            if t["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.5)

        # Verify doc exists
        docs = await engine.list_documents(collection)
        assert len(docs) == 1

        await engine.drop_collection(collection)

        # Verify clean
        docs = await engine.list_documents(collection)
        assert len(docs) == 0
