"""``cuga knowledge ingest`` and ``cuga knowledge search`` against a faked server."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from cuga.cli.knowledge_cmds import knowledge_app

pytestmark = pytest.mark.unit


class _Response:
    def __init__(self, payload: dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._payload


def _fake_post(calls: list, payload_for):
    def _post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(payload_for(url, kwargs))

    return _post


def test_ingest_uploads_in_batches_and_reports_each_file(tmp_path, monkeypatch):
    paths = []
    for i in range(12):  # more than one request's worth
        p = tmp_path / f"doc_{i}.txt"
        p.write_text(f"document {i}")
        paths.append(str(p))
    calls: list = []

    def payload(url, kwargs):
        names = [name for _field, (name, _data) in kwargs["files"]]
        status = {
            n: {"status": "failed", "error": "unsupported"} if n == "doc_3.txt" else {"status": "indexed"}
            for n in names
        }
        return {"tasks": [{"file_tasks": status}]}

    monkeypatch.setenv("CUGA_API_BASE", "http://kb.test")
    monkeypatch.setattr("cuga.cli.knowledge_cmds.httpx.post", _fake_post(calls, payload))

    result = CliRunner().invoke(knowledge_app, ["ingest", *paths, "--agent-id", "sales"])

    assert [len(kwargs["files"]) for _url, kwargs in calls] == [10, 2]
    assert all(url == "http://kb.test/api/knowledge/documents" for url, _ in calls)
    assert calls[0][1]["headers"] == {"X-Agent-ID": "sales"}
    assert calls[0][1]["data"] == {"scope": "agent", "wait": "true"}
    assert "indexed  doc_0.txt" in result.output and "FAILED   doc_3.txt: unsupported" in result.output
    assert result.exit_code == 1  # one file failed


def test_search_prints_ranked_results(monkeypatch):
    calls: list = []
    payload = {
        "results": [
            {
                "filename": "invoice_07.txt",
                "page": 1,
                "score": 0.81,
                "text": "Invoice INV-2026-0007 was issued to Contoso.",
            },
            {"filename": "invoice_11.txt", "page": None, "score": 0.42, "text": "Invoice INV-2026-0011 ..."},
        ]
    }
    monkeypatch.setenv("CUGA_API_BASE", "http://kb.test/")
    monkeypatch.setattr("cuga.cli.knowledge_cmds.httpx.post", _fake_post(calls, lambda u, k: payload))

    result = CliRunner().invoke(knowledge_app, ["search", "INV-2026-0007", "--limit", "2"])

    assert result.exit_code == 0, result.output
    url, kwargs = calls[0]
    assert url == "http://kb.test/api/knowledge/search"
    assert kwargs["json"] == {"query": "INV-2026-0007", "scope": "agent", "limit": 2}
    assert kwargs["headers"] == {"X-Agent-ID": "cuga-default"}
    assert "1. invoice_07.txt p.1  score 0.810" in result.output
    assert "2. invoice_11.txt  score 0.420" in result.output


def test_search_defaults_to_the_configured_demo_port(monkeypatch):
    from cuga.config import settings

    calls: list = []
    monkeypatch.delenv("CUGA_API_BASE", raising=False)
    monkeypatch.setattr("cuga.cli.knowledge_cmds.httpx.post", _fake_post(calls, lambda u, k: {"results": []}))

    result = CliRunner().invoke(knowledge_app, ["search", "anything"])

    assert calls[0][0] == f"http://127.0.0.1:{settings.server_ports.demo}/api/knowledge/search"
    assert "No results." in result.output


def test_ingest_reports_the_servers_reason_for_a_rejected_upload(tmp_path, monkeypatch):
    """A file that fails to convert comes back as HTTP 400 with a reason; show it, exit 1."""
    p = tmp_path / "broken.pdf"
    p.write_text("not a pdf")

    def _post(url, **kwargs):
        return _Response({"detail": "Conversion failed for: broken.pdf"}, status_code=400)

    monkeypatch.setenv("CUGA_API_BASE", "http://kb.test")
    monkeypatch.setattr("cuga.cli.knowledge_cmds.httpx.post", _post)

    result = CliRunner().invoke(knowledge_app, ["ingest", str(p)])

    assert "FAILED   broken.pdf: Conversion failed for: broken.pdf" in result.output
    assert result.exit_code == 1
