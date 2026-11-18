"""Where documents are parsed: Docling in CUGA, a docling-serve service, or a function the
host brings. CUGA chunks every backend's DoclingDocument the same way, so chunks and
citations never depend on where parsing ran, and only operators (settings.toml / env / SDK)
choose the backend: the settings API can neither change it nor read the service's key."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from docling_core.types.doc import DoclingDocument, TableCell, TableData

from cuga.backend.knowledge import engine as engine_module
from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine

pytestmark = pytest.mark.unit

KEY = "sekret"  # pragma: allowlist secret


def _sample_doc() -> DoclingDocument:
    doc = DoclingDocument(name="report")
    doc.add_heading("Quarterly results")
    doc.add_text(label="text", text="Revenue grew 12% in the third quarter, led by services.")
    doc.add_heading("Costs", level=2)
    doc.add_text(label="text", text="Operating costs stayed flat at 4.1 million.")
    cells = [
        TableCell(
            text=text,
            start_row_offset_idx=r,
            end_row_offset_idx=r + 1,
            start_col_offset_idx=c,
            end_col_offset_idx=c + 1,
            column_header=r == 0,
        )
        for r, row in enumerate([["Item", "Cost"], ["Staff", "2.0"], ["Cloud", "1.1"]])
        for c, text in enumerate(row)
    ]
    doc.add_table(data=TableData(num_rows=3, num_cols=2, table_cells=cells))
    return doc


@pytest.fixture
def backend_settings(monkeypatch):
    """Stands in for [knowledge.docling] in settings.toml / env."""
    values: dict = {}
    monkeypatch.setattr(engine_module, "_docling_backend_settings", lambda: values)
    return values


def _engine(tmp_path: Path, document_processor=None) -> KnowledgeEngine:
    # provider="openai" chunks with tiktoken: fast, no model download.
    cfg = KnowledgeConfig(
        enabled=True,
        persist_dir=tmp_path / "kb",
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
    )
    return KnowledgeEngine(cfg, document_processor=document_processor)


@pytest.fixture
def pdf(tmp_path) -> Path:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    return path


def _pairs(docs) -> list[tuple[str, dict]]:
    return [(d.page_content, d.metadata) for d in docs]


def test_chunks_match_what_doclingloader_built(tmp_path, backend_settings, pdf):
    from langchain_docling import DoclingLoader
    from langchain_docling.loader import ExportType

    eng = _engine(tmp_path)
    doc = _sample_doc()
    converter = SimpleNamespace(convert=lambda source: SimpleNamespace(document=doc))
    chunk_size = eng._get_effective_chunk_settings()[0]
    before = DoclingLoader(
        file_path=str(pdf),
        export_type=ExportType.DOC_CHUNKS,
        converter=converter,
        chunker=eng._build_docling_chunker(chunk_size),
    ).load()

    with patch.object(eng, "_get_docling_converter", return_value=converter):
        after = eng._load_document(pdf)

    assert len(after) >= 2
    assert _pairs(after) == _pairs(before)


def test_a_document_sent_as_json_chunks_the_same(tmp_path, backend_settings, pdf):
    """What a service returns (the document as JSON) chunks exactly like the object."""
    doc = _sample_doc()
    local = _engine(tmp_path / "a", document_processor=lambda path: doc)._load_document(pdf)
    remote = _engine(
        tmp_path / "b", document_processor=lambda path: DoclingDocument.model_validate(doc.export_to_dict())
    )._load_document(pdf)
    assert _pairs(remote) == _pairs(local)


def test_a_processor_returning_something_else_fails_clearly(tmp_path, backend_settings, pdf):
    eng = _engine(tmp_path, document_processor=lambda path: "plain text")
    with pytest.raises(TypeError, match="returned str, not a DoclingDocument"):
        eng._load_document(pdf)


def test_a_processor_names_its_own_reason_for_an_unknown_file_type(tmp_path, backend_settings):
    """Only local Docling's failure becomes "Unsupported file format"; a processor may
    support the type, so its own error reaches the user."""

    def parse(path):
        raise ValueError("my parser needs a password for this archive")

    odd = tmp_path / "data.xyz123"
    odd.write_bytes(b"bytes")
    with pytest.raises(ValueError, match="needs a password"):
        _engine(tmp_path, document_processor=parse)._load_document(odd)


@pytest.fixture
def test_processor_module(monkeypatch):
    module = types.ModuleType("cuga_test_processor")
    module.parse = lambda path: _sample_doc()
    monkeypatch.setitem(sys.modules, "cuga_test_processor", module)
    return module


def test_local_is_the_default(tmp_path, backend_settings):
    eng = _engine(tmp_path)
    assert eng._parse_backend == "local"
    assert eng._docling_service is None


def test_a_url_selects_the_service(tmp_path, backend_settings):
    backend_settings.update(url="http://docling.test:5001/", api_key=KEY, timeout_s=60)
    eng = _engine(tmp_path)
    assert eng._parse_backend == "service"
    assert eng._docling_service.name == "http://docling.test:5001"
    assert eng._docling_service._timeout_s == 60.0


def test_a_processor_setting_wins_over_the_url(tmp_path, backend_settings, test_processor_module, pdf):
    backend_settings.update(url="http://docling.test:5001", processor="cuga_test_processor:parse")
    eng = _engine(tmp_path)
    assert eng._parse_backend == "custom"
    assert eng._docling_service is None
    assert [d.page_content for d in eng._load_document(pdf)] == [
        d.page_content
        for d in _engine(tmp_path / "x", document_processor=lambda p: _sample_doc())._load_document(pdf)
    ]


def test_the_sdk_processor_wins_over_settings(tmp_path, backend_settings, test_processor_module):
    backend_settings.update(processor="cuga_test_processor:parse")

    def mine(path):
        return _sample_doc()

    eng = _engine(tmp_path, document_processor=mine)
    assert eng._document_processor is mine


def test_a_bad_processor_fails_before_the_engine_takes_its_lock(tmp_path, backend_settings):
    backend_settings.update(processor="no_such_module_xyz:parse")
    with pytest.raises(ValueError, match="knowledge.docling.processor"):
        _engine(tmp_path)
    backend_settings.clear()
    _engine(tmp_path)  # same persist dir: the failed start held no lock


def test_the_settings_api_cannot_choose_the_backend(tmp_path, backend_settings):
    eng = _engine(tmp_path)
    eng.apply_knowledge_config(
        {
            "docling_url": "http://evil.test:5001",
            "docling_api_key": "x",
            "docling_processor": "os:system",
            "url": "http://evil.test:5001",
            "processor": "os:system",
            "docling_pdf_mode": "fast",
        }
    )
    assert eng._config.docling_pdf_mode == "fast"  # a real setting still applies
    assert eng._parse_backend == "local"
    assert eng._docling_service is None and eng._document_processor is None


def test_the_service_key_is_never_returned(tmp_path, backend_settings):
    backend_settings.update(url="http://docling.test:5001", api_key=KEY)
    eng = _engine(tmp_path)
    assert KEY not in json.dumps(eng.get_settings(), default=str)
    assert KEY not in json.dumps(eng._config.to_dict(), default=str)


def test_warmup_loads_no_docling_models_when_parsing_runs_elsewhere(tmp_path, backend_settings):
    backend_settings.update(url="http://docling.test:5001")
    eng = _engine(tmp_path)
    eng._docling_service = MagicMock()
    with (
        patch.object(eng, "_ensure_embeddings"),
        patch.object(eng, "_get_docling_converter", side_effect=AssertionError("loaded Docling")),
    ):
        result = asyncio.run(eng.warmup())
    eng._docling_service.health.assert_called_once_with()
    assert result["docling_prewarmed"] is True

    custom = _engine(tmp_path / "custom", document_processor=lambda p: _sample_doc())
    with (
        patch.object(custom, "_ensure_embeddings"),
        patch.object(custom, "_get_docling_converter", side_effect=AssertionError("loaded Docling")),
    ):
        asyncio.run(custom.warmup())


def test_the_convert_time_is_recorded(tmp_path, backend_settings, pdf):
    eng = _engine(tmp_path, document_processor=lambda p: _sample_doc())
    timings: dict = {}
    eng._load_document(pdf, stage_timings=timings)
    assert timings["convert_s"] >= 0


def test_sdk_passes_the_processor_to_the_engine():
    from cuga.sdk import CugaAgent

    def mine(path):
        return _sample_doc()

    agent = CugaAgent(document_processor=mine)
    with (
        patch("cuga.backend.knowledge.engine.KnowledgeEngine") as engine_cls,
        patch("cuga.backend.knowledge.client.KnowledgeClient"),
        patch("cuga.backend.knowledge_llm_bridge.CugaChatGenerator"),
    ):
        agent.knowledge  # noqa: B018 — the property builds the engine
    assert engine_cls.call_args.kwargs["document_processor"] is mine
