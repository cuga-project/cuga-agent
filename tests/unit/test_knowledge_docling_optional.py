"""Graceful degradation when docling isn't installed (the slim Docker image).

``_load_document`` has exactly one place that needs docling to be absent
without crashing: the ``_DOCLING_FORMATS`` branch's ``except (ImportError,
ModuleNotFoundError)`` clause. These tests simulate that by blocking the
``langchain_docling`` import (docling itself stays installed in this dev
venv; blocking the import is the actual failure mode in the slim image,
which never installs the package at all).

Coverage:
  - ``.md`` degrades to the plain-text splitter instead of raising —
    Markdown is already plain text, so it doesn't need docling's
    layout/table parsing (this was the gap PR review caught: ".md still
    requires Docling and fails in slim").
  - ``.pdf`` (and any other real ``_DOCLING_FORMATS`` member) still raises
    the clear, actionable error — document parsing genuinely isn't
    available without docling, by design.
  - An unrecognized extension (the final ``else`` branch, Docling-as-last-
    resort) raises the same clear error too, not the misleading
    "Unsupported file format" message that bare ``except Exception`` used
    to produce for *any* failure there, import errors included.
  - Regression guard: ``.md`` still goes through Docling when it IS
    installed — the fallback must only kick in on ImportError, not
    unconditionally for every suffix in ``_DOCLING_FORMATS``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine

pytestmark = pytest.mark.unit


def _make_engine() -> KnowledgeEngine:
    # provider="openai" routes chunking to tiktoken (cl100k_base) instead of
    # fastembed's real ONNX tokenizer — fast and deterministic, same pattern
    # as test_knowledge_text_splitter_tiktoken_canary.py.
    tmp = tempfile.mkdtemp(prefix="cuga-docling-optional-test-")
    cfg = KnowledgeConfig(
        enabled=True,
        persist_dir=Path(tmp),
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
    )
    return KnowledgeEngine(cfg)


class _BlockLangchainDocling:
    """Context manager that makes ``import langchain_docling`` (and
    submodules) raise ImportError, leaving every other import untouched."""

    def __enter__(self):
        self._real_import = __import__

        def _blocked(name, *args, **kwargs):
            if name == "langchain_docling" or name.startswith("langchain_docling."):
                raise ImportError("langchain_docling not installed (simulated)")
            return self._real_import(name, *args, **kwargs)

        import builtins

        self._patcher = patch.object(builtins, "__import__", _blocked)
        self._patcher.start()
        return self

    def __exit__(self, *exc_info):
        self._patcher.stop()


def test_markdown_falls_back_to_plain_text_without_docling(tmp_path):
    """.md must degrade to the plain-text splitter, not raise, when docling
    isn't installed — the slim image's whole point for Markdown."""
    eng = _make_engine()
    md_path = tmp_path / "note.md"
    md_path.write_text("# Title\n\nSome **markdown** content for the slim image.\n")

    with _BlockLangchainDocling():
        docs = eng._load_document(md_path)

    assert len(docs) >= 1
    assert "markdown" in docs[0].page_content
    assert "content for the slim image" in docs[0].page_content


def test_pdf_raises_clear_error_without_docling(tmp_path):
    """.pdf (a real _DOCLING_FORMATS member, not plain text) must still
    raise the actionable error — document parsing isn't available without
    docling, by design, and the server must say so clearly."""
    eng = _make_engine()
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake content for the test\n")

    with _BlockLangchainDocling():
        with pytest.raises(ImportError) as exc_info:
            eng._load_document(pdf_path)

    msg = str(exc_info.value)
    assert "docling" in msg
    assert "easyocr" in msg
    assert "torch" in msg
    assert 'cuga[docling]' in msg
    assert "report.pdf" in msg


def test_unrecognized_extension_raises_clear_error_not_unsupported_format(tmp_path):
    """An extension outside both _DOCLING_FORMATS and the plain-text list
    falls to the final ``else`` branch, which also tries Docling as a last
    resort. Without docling installed, that must raise the same clear error
    — not the misleading 'Unsupported file format' message a bare
    ``except Exception`` would have produced for an ImportError too."""
    eng = _make_engine()
    odd_path = tmp_path / "data.xyz123"
    odd_path.write_bytes(b"some bytes of an unrecognized format\n")

    with _BlockLangchainDocling():
        with pytest.raises(ImportError) as exc_info:
            eng._load_document(odd_path)

    msg = str(exc_info.value)
    assert "docling" in msg
    assert "Unsupported file format" not in msg


def test_markdown_still_uses_docling_when_available(tmp_path):
    """Regression guard: the plain-text fallback must only trigger on
    ImportError, not unconditionally — with docling available (the full
    image, and this dev venv), .md keeps going through DoclingLoader."""
    eng = _make_engine()
    md_path = tmp_path / "note.md"
    md_path.write_text("# Title\n\nSome content.\n")

    mock_instance = MagicMock()
    mock_instance.load.return_value = [MagicMock(page_content="mocked", metadata={})]
    with patch("langchain_docling.DoclingLoader", return_value=mock_instance) as mock_loader_cls:
        eng._load_document(md_path)

    mock_loader_cls.assert_called_once()
    mock_instance.load.assert_called_once()
