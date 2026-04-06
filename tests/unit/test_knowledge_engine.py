"""Unit tests for the knowledge engine core components."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.metadata import MetadataDB


@pytest_asyncio.fixture
async def meta_db():
    tmpdir = tempfile.mkdtemp()
    db = MetadataDB(Path(tmpdir) / "test_meta.db")
    yield db
    await db.close()


class TestMetadataDB:
    @pytest.mark.asyncio
    async def test_add_and_list_documents(self, meta_db):
        await meta_db.add_document("col1", "file.pdf", 10)
        docs = await meta_db.list_documents("col1")
        assert len(docs) == 1
        assert docs[0]["filename"] == "file.pdf"
        assert docs[0]["chunk_count"] == 10
        assert docs[0]["status"] == "indexed"

    @pytest.mark.asyncio
    async def test_list_documents_hides_deleting(self, meta_db):
        await meta_db.add_document("col1", "a.pdf", 5)
        await meta_db.add_document("col1", "b.pdf", 3)
        await meta_db.mark_deleting("col1", "a.pdf")
        docs = await meta_db.list_documents("col1")
        assert len(docs) == 1
        assert docs[0]["filename"] == "b.pdf"

    @pytest.mark.asyncio
    async def test_document_exists(self, meta_db):
        assert not await meta_db.document_exists("col1", "a.pdf")
        await meta_db.add_document("col1", "a.pdf", 5)
        assert await meta_db.document_exists("col1", "a.pdf")
        await meta_db.mark_deleting("col1", "a.pdf")
        assert not await meta_db.document_exists("col1", "a.pdf")

    @pytest.mark.asyncio
    async def test_mark_deleting_returns_false_for_missing(self, meta_db):
        assert not await meta_db.mark_deleting("col1", "nonexistent.pdf")

    @pytest.mark.asyncio
    async def test_remove_document(self, meta_db):
        await meta_db.add_document("col1", "a.pdf", 5)
        await meta_db.remove_document("col1", "a.pdf")
        assert not await meta_db.document_exists("col1", "a.pdf")

    @pytest.mark.asyncio
    async def test_get_deleting_documents(self, meta_db):
        await meta_db.add_document("col1", "a.pdf", 5)
        await meta_db.add_document("col2", "b.pdf", 3)
        await meta_db.mark_deleting("col1", "a.pdf")
        deleting = await meta_db.get_deleting_documents()
        assert len(deleting) == 1
        assert deleting[0]["filename"] == "a.pdf"
        assert deleting[0]["collection"] == "col1"

    @pytest.mark.asyncio
    async def test_create_and_get_task(self, meta_db):
        file_tasks = {"report.pdf": {"filename": "report.pdf", "status": "pending"}}
        task = await meta_db.create_task("t1", "col1", 1, file_tasks)
        assert task["task_id"] == "t1"
        assert task["status"] == "pending"
        assert task["total_files"] == 1
        assert task["file_tasks"]["report.pdf"]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_update_task(self, meta_db):
        await meta_db.create_task("t1", "col1", 1, {"f.pdf": {"filename": "f.pdf", "status": "pending"}})
        await meta_db.update_task("t1", status="running", processed_files=1)
        task = await meta_db.get_task("t1")
        assert task["status"] == "running"
        assert task["processed_files"] == 1

    @pytest.mark.asyncio
    async def test_list_tasks_by_collection(self, meta_db):
        await meta_db.create_task("t1", "col1", 1, {})
        await meta_db.create_task("t2", "col2", 1, {})
        tasks = await meta_db.list_tasks("col1")
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == "t1"

    @pytest.mark.asyncio
    async def test_recover_stale_tasks(self, meta_db):
        await meta_db.create_task("t1", "col1", 1, {"f.pdf": {"filename": "f.pdf", "status": "processing"}})
        await meta_db.update_task("t1", status="running")
        count = await meta_db.recover_stale_tasks()
        assert count == 1
        task = await meta_db.get_task("t1")
        assert task["status"] == "failed"
        assert task["file_tasks"]["f.pdf"]["status"] == "failed"
        assert "restart" in task["file_tasks"]["f.pdf"]["error"]

    @pytest.mark.asyncio
    async def test_purge_old_tasks(self, meta_db):
        await meta_db.create_task("t1", "col1", 1, {})
        purged = await meta_db.purge_old_tasks(max_age_days=7)
        assert purged == 0

    @pytest.mark.asyncio
    async def test_get_task_returns_none_for_missing(self, meta_db):
        assert await meta_db.get_task("nonexistent") is None

    @pytest.mark.asyncio
    async def test_set_and_get_collection_config(self, meta_db):
        await meta_db.set_collection_config("col1", "huggingface", "all-MiniLM-L6-v2", 384)
        cfg = await meta_db.get_collection_config("col1")
        assert cfg["embedding_provider"] == "huggingface"
        assert cfg["embedding_model"] == "all-MiniLM-L6-v2"
        assert cfg["embedding_dim"] == 384

    @pytest.mark.asyncio
    async def test_set_collection_config_ignores_duplicate(self, meta_db):
        await meta_db.set_collection_config("col1", "huggingface", "model-a", 384)
        await meta_db.set_collection_config("col1", "openai", "model-b", 1536)
        cfg = await meta_db.get_collection_config("col1")
        assert cfg["embedding_provider"] == "huggingface"

    @pytest.mark.asyncio
    async def test_delete_collection_metadata(self, meta_db):
        await meta_db.add_document("col1", "a.pdf", 5)
        await meta_db.create_task("t1", "col1", 1, {})
        await meta_db.set_collection_config("col1", "hf", "m", 384)
        await meta_db.delete_collection_metadata("col1")
        assert await meta_db.list_documents("col1") == []
        assert await meta_db.list_tasks("col1") == []
        assert await meta_db.get_collection_config("col1") is None

    @pytest.mark.asyncio
    async def test_settings(self, meta_db):
        await meta_db.set_setting("chunk_size", "500")
        assert await meta_db.get_setting("chunk_size") == "500"
        assert await meta_db.get_setting("missing", "default") == "default"

    @pytest.mark.asyncio
    async def test_get_all_settings(self, meta_db):
        await meta_db.set_setting("a", "1")
        await meta_db.set_setting("b", "2")
        all_s = await meta_db.get_all_settings()
        assert all_s == {"a": "1", "b": "2"}


class TestKnowledgeConfig:
    def test_defaults(self):
        cfg = KnowledgeConfig()
        assert cfg.enabled is False
        assert cfg.chunk_size == 1000
        assert cfg.embedding_provider == "fastembed"
        assert cfg.metric_type == "COSINE"

    def test_from_settings_empty(self):
        cfg = KnowledgeConfig.from_settings({})
        assert cfg.enabled is False
        assert cfg.chunk_size == 1000


class TestEngineHelpers:
    def test_sanitize_collection(self):
        from cuga.backend.knowledge.engine import _sanitize_collection

        assert _sanitize_collection("kb_agent_default") == "kb_agent_default"
        assert _sanitize_collection("kb-sess-abc/123") == "kb_sess_abc_123"

    def test_sanitize_filename(self):
        from cuga.backend.knowledge.engine import _sanitize_filename

        assert _sanitize_filename("report.pdf") == "report.pdf"
        assert _sanitize_filename("my file (1).pdf") == "my file (1).pdf"

    def test_sanitize_filename_rejects_traversal(self):
        from cuga.backend.knowledge.engine import _sanitize_filename

        with pytest.raises(ValueError, match="traversal"):
            _sanitize_filename("../etc/passwd")

    def test_page_from_docling_dl_meta(self):
        from cuga.backend.knowledge.engine import _page_from_docling_dl_meta

        assert _page_from_docling_dl_meta(None) is None
        assert _page_from_docling_dl_meta({}) is None
        assert _page_from_docling_dl_meta({"doc_items": []}) is None
        meta = {
            "doc_items": [
                {
                    "label": "text",
                    "prov": [{"page_no": 5, "charspan": [0, 1]}],
                },
                {
                    "label": "text",
                    "prov": [{"page_no": 2, "charspan": [0, 1]}],
                },
            ],
        }
        assert _page_from_docling_dl_meta(meta) == 2

    def test_validate_url_rejects_private(self):
        from cuga.backend.knowledge.engine import KnowledgeEngine

        engine = object.__new__(KnowledgeEngine)
        with pytest.raises(ValueError, match="Private"):
            engine._validate_url("http://192.168.1.1/doc.pdf")

    def test_validate_url_rejects_credentials(self):
        from cuga.backend.knowledge.engine import KnowledgeEngine

        engine = object.__new__(KnowledgeEngine)
        with pytest.raises(ValueError, match="credentials"):
            engine._validate_url("http://user:pass@example.com/doc.pdf")

    def test_validate_url_rejects_blocked_hostname(self):
        from cuga.backend.knowledge.engine import KnowledgeEngine

        engine = object.__new__(KnowledgeEngine)
        with pytest.raises(ValueError, match="Blocked"):
            engine._validate_url("http://localhost/doc.pdf")

    def test_validate_url_rejects_bad_port(self):
        from cuga.backend.knowledge.engine import KnowledgeEngine

        engine = object.__new__(KnowledgeEngine)
        with pytest.raises(ValueError, match="Port"):
            engine._validate_url("http://example.com:9999/doc.pdf")

    def test_translate_document_load_error_for_password_protected_pdf(self):
        from cuga.backend.knowledge.engine import _translate_document_load_error

        try:
            try:
                raise RuntimeError("Failed to load document (PDFium: Incorrect password error).")
            except RuntimeError as cause:
                raise ValueError("Input document secret.pdf is not valid.") from cause
        except ValueError as exc:
            translated = _translate_document_load_error(Path("secret.pdf"), exc)

        assert isinstance(translated, ValueError)
        assert "password-protected" in str(translated)


class TestExceptions:
    def test_ingestion_queue_full_error(self):
        from cuga.backend.knowledge.engine import IngestionQueueFullError

        err = IngestionQueueFullError(10)
        assert err.max_pending == 10
        assert "10" in str(err)

    def test_document_exists_error(self):
        from cuga.backend.knowledge.engine import DocumentExistsError

        err = DocumentExistsError("file.pdf")
        assert err.filename == "file.pdf"

    def test_file_too_large_error(self):
        from cuga.backend.knowledge.engine import FileTooLargeError

        err = FileTooLargeError(200, 100)
        assert err.size == 200
        assert err.max_size == 100
