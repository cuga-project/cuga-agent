"""The demo knowledge seed reports what the ingest did. A failed ingest does not raise: the
engine records the failure on the task it returns."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from loguru import logger

from cuga.backend.server import demo_manage_setup

pytestmark = pytest.mark.unit

PDF = demo_manage_setup.DEMO_KNOWLEDGE_OOBE_PDF_NAME


class _Engine:
    def __init__(self, task):
        self._task = task
        self.ingested: list[str] = []

    async def list_documents(self, collection):
        return []

    async def ingest(self, collection, path, replace_duplicates, filename):
        self.ingested.append(filename)
        return self._task


def _app_state(engine):
    return SimpleNamespace(
        get_subsystem_status=lambda name: {"state": "ready"},
        knowledge_engine=engine,
        knowledge_config_hash="hash",
        agent_id="cuga-default",
    )


@pytest.fixture
def messages(monkeypatch, tmp_path):
    pdf = tmp_path / PDF
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("CUGA_DEMO_MODE", "knowledge")
    monkeypatch.delenv("CUGA_AGENT_ID", raising=False)
    monkeypatch.setattr(demo_manage_setup, "_resolve_oobe_knowledge_pdf_path", lambda: pdf)
    monkeypatch.setattr(
        "cuga.backend.knowledge.auth.resolve_agent_collection", lambda agent_id, app_state: "kb_agent_demo"
    )
    lines: list[tuple[str, str]] = []
    sink = logger.add(lambda m: lines.append((m.record["level"].name, m.record["message"])), level="INFO")
    yield lines
    logger.remove(sink)


@pytest.mark.asyncio
async def test_a_failed_ingest_is_reported_as_not_ingested(messages):
    error = "Cannot parse sovereign_core_overview.pdf: optional document-processing dependencies are not installed."
    engine = _Engine({"status": "failed", "file_tasks": {PDF: {"status": "failed", "error": error}}})

    await demo_manage_setup.seed_demo_knowledge_oobe_pdf_via_engine_if_needed(_app_state(engine))

    assert engine.ingested == [PDF]
    assert ("WARNING", f"OOBE knowledge PDF {PDF} was not ingested (status=failed): {error}") in messages
    assert not [m for _, m in messages if m.startswith("Ingested OOBE")]


@pytest.mark.asyncio
async def test_a_completed_ingest_is_reported_as_ingested(messages):
    engine = _Engine({"status": "completed", "file_tasks": {PDF: {"status": "indexed"}}})

    await demo_manage_setup.seed_demo_knowledge_oobe_pdf_via_engine_if_needed(_app_state(engine))

    assert ("INFO", f"Ingested OOBE knowledge PDF {PDF}") in messages
    assert not [m for level, m in messages if level == "WARNING"]
