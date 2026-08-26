"""Markdown/text ingest must not load the Docling PDF/OCR stack.

Uploading a few 100 KB .md files was jumping the agent server from ~2 GB to
~6 GB RSS that never returned. Root cause: .md went through DoclingLoader,
which imported torch and built a PDF DocumentConverter (layout + optional
EasyOCR) on first ingest, and warmup() also initialized InputFormat.PDF.
Native-text formats now use the token-aware splitter instead.
"""

from __future__ import annotations

import inspect
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine


def _engine(tmp_path: Path) -> KnowledgeEngine:
    return KnowledgeEngine(
        KnowledgeConfig(
            enabled=True,
            persist_dir=tmp_path / "kb",
            embedding_provider="openai",
            embedding_model="text-embedding-3-small",
            embedding_api_key="sk-test",
            chunk_size=200,
            chunk_overlap=20,
        )
    )


@pytest.mark.unit
def test_markdown_and_csv_are_plain_text_not_docling():
    assert ".md" in KnowledgeEngine._TEXT_FORMATS
    assert ".markdown" in KnowledgeEngine._TEXT_FORMATS
    assert ".csv" in KnowledgeEngine._TEXT_FORMATS
    assert ".md" not in KnowledgeEngine._DOCLING_FORMATS
    assert ".csv" not in KnowledgeEngine._DOCLING_FORMATS
    assert ".pdf" in KnowledgeEngine._DOCLING_FORMATS
    assert ".docx" in KnowledgeEngine._DOCLING_FORMATS


@pytest.mark.unit
def test_engine_does_not_import_docling_loader_at_module_level():
    import cuga.backend.knowledge.engine as engine_mod

    assert not hasattr(engine_mod, "DoclingLoader")


@pytest.mark.unit
def test_markdown_load_never_calls_docling(tmp_path: Path):
    eng = _engine(tmp_path)
    md = tmp_path / "notes.md"
    md.write_text("# Title\n\n" + ("paragraph of markdown text.\n\n" * 40))

    eng._load_with_docling = MagicMock(side_effect=AssertionError("Docling must not run for .md"))
    docs = eng._load_document(md)

    eng._load_with_docling.assert_not_called()
    assert docs
    assert all(d.page_content.strip() for d in docs)
    assert all(d.metadata.get("page") for d in docs)


@pytest.mark.unit
def test_csv_load_never_calls_docling(tmp_path: Path):
    eng = _engine(tmp_path)
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("a,b\n1,2\n3,4\n")

    eng._load_with_docling = MagicMock(side_effect=AssertionError("Docling must not run for .csv"))
    docs = eng._load_document(csv_path)

    eng._load_with_docling.assert_not_called()
    assert docs


@pytest.mark.unit
@pytest.mark.asyncio
async def test_warmup_does_not_build_docling_converter(tmp_path: Path):
    eng = _engine(tmp_path)
    eng._ensure_embeddings = lambda: None
    eng._default_embeddings = object()
    eng._get_docling_converter = MagicMock(
        side_effect=AssertionError("warmup must not build the Docling PDF pipeline")
    )

    result = await eng.warmup()

    eng._get_docling_converter.assert_not_called()
    assert result["docling_prewarmed"] is False
    assert result["embeddings_initialized"] is True


@pytest.mark.unit
def test_docling_converter_construction_is_serialized():
    tmp = Path(tempfile.mkdtemp(prefix="cuga-docling-lock-"))
    eng = _engine(tmp)
    builds: list[int] = []
    release = threading.Event()
    sentinel = object()

    def slow_build(*_a, **_k):
        builds.append(1)
        assert release.wait(timeout=2.0)
        return sentinel

    eng._build_docling_converter = slow_build
    results: list[object] = []

    def worker() -> None:
        results.append(eng._get_docling_converter())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    release.set()
    for t in threads:
        t.join(timeout=3.0)

    assert builds == [1], f"expected one converter build under the lock, got {builds}"
    assert results == [sentinel, sentinel]


@pytest.mark.unit
def test_config_update_during_converter_build_does_not_repopulate_stale_cache():
    """commit_knowledge_update must clear the converter cache under the same
    lock as construction. Otherwise an in-flight build stores the old
    converter after the clear (same cache key when only use_gpu flipped).
    """
    tmp = Path(tempfile.mkdtemp(prefix="cuga-docling-invalidate-"))
    eng = _engine(tmp)
    started = threading.Event()
    release = threading.Event()
    stale = object()

    def slow_build(*_a, **_k):
        started.set()
        assert release.wait(timeout=2.0)
        return stale

    eng._build_docling_converter = slow_build

    getter_result: list[object] = []

    def getter() -> None:
        getter_result.append(eng._get_docling_converter())

    getter_thread = threading.Thread(target=getter)
    getter_thread.start()
    assert started.wait(timeout=2.0)

    commit_attempted_lock = threading.Event()
    inner_lock = eng._docling_converter_lock

    class _AcquireSignalLock:
        def acquire(self, blocking: bool = True, timeout: float = -1):
            commit_attempted_lock.set()
            return inner_lock.acquire(blocking, timeout)

        def release(self):
            return inner_lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_exc):
            self.release()

    eng._docling_converter_lock = _AcquireSignalLock()

    commit_done = threading.Event()

    def commit() -> None:
        result = eng.apply_knowledge_config({"use_gpu": False})
        assert result["docling_changed"] is True
        commit_done.set()

    commit_thread = threading.Thread(target=commit)
    commit_thread.start()
    assert commit_attempted_lock.wait(timeout=2.0)
    release.set()
    getter_thread.join(timeout=3.0)
    commit_thread.join(timeout=3.0)

    assert getter_result == [stale]
    assert commit_done.is_set()
    assert eng._docling_converters == {}
    rebuilt = object()
    eng._build_docling_converter = lambda *_a, **_k: rebuilt
    assert eng._get_docling_converter() is rebuilt
    assert list(eng._docling_converters.values()) == [rebuilt]


@pytest.mark.unit
def test_get_docling_converter_uses_lock():
    src = inspect.getsource(KnowledgeEngine._get_docling_converter)
    assert "_docling_converter_lock" in src
    assert "_build_docling_converter" in src
    commit_src = inspect.getsource(KnowledgeEngine.commit_knowledge_update)
    assert "_docling_converter_lock" in commit_src
