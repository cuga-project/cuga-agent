"""Graceful degradation when docling isn't installed (the slim Docker image).

``_load_document`` handles missing Docling at the lazy imports in both
Docling loading paths. These tests simulate that by blocking the
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
    assert "full CUGA image" in msg
    assert "cuga[docling]" not in msg
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


@pytest.mark.parametrize("suffix", [".md", ".pdf", ".xyz123"])
@pytest.mark.parametrize("stage", ["chunker", "converter", "loader_init", "loader_load"])
@pytest.mark.parametrize("error_type", [ImportError, ModuleNotFoundError])
def test_docling_runtime_import_errors_are_preserved(tmp_path, suffix, stage, error_type):
    """Runtime import failures must not become missing-stack errors or Markdown fallback."""
    eng = _make_engine()
    file_path = tmp_path / f"document{suffix}"
    file_path.write_text("Document content")
    error = error_type("parser runtime dependency failed")

    with (
        patch("langchain_docling.DoclingLoader") as loader_cls,
        patch.object(eng, "_build_docling_chunker", return_value=None) as chunker,
        patch.object(eng, "_get_docling_converter", return_value=object()) as converter,
        patch.object(eng, "_load_plain_text") as plain_text,
    ):
        failing_call = {
            "chunker": chunker,
            "converter": converter,
            "loader_init": loader_cls,
            "loader_load": loader_cls.return_value.load,
        }[stage]
        failing_call.side_effect = error

        with pytest.raises(error_type) as exc_info:
            eng._load_document(file_path)

        assert exc_info.value is error
        plain_text.assert_not_called()


def test_unrecognized_extension_keeps_unsupported_format_error(tmp_path):
    """Ordinary parser failures for unknown extensions keep the existing error."""
    eng = _make_engine()
    file_path = tmp_path / "document.xyz123"
    file_path.write_text("Document content")

    with (
        patch("langchain_docling.DoclingLoader") as loader_cls,
        patch.object(eng, "_build_docling_chunker", return_value=None),
        patch.object(eng, "_get_docling_converter", return_value=object()),
    ):
        loader_cls.return_value.load.side_effect = ValueError("parser rejected format")
        with pytest.raises(ValueError, match="Unsupported file format: .xyz123"):
            eng._load_document(file_path)
