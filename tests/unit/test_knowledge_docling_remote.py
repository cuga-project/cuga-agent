"""The docling-serve client: CUGA sends the file, polls the task and reads the
DoclingDocument back. Every service failure must reach the user as one clear message
that never carries the API key."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from docling_core.types.doc import DoclingDocument
from docling_core.types.doc.document import CURRENT_VERSION

from cuga.backend.knowledge import docling_remote
from cuga.backend.knowledge.docling_remote import DoclingServeClient, DoclingServiceError, load_processor

pytestmark = pytest.mark.unit

URL = "http://docling.test:5001"
KEY = "sekret"  # pragma: allowlist secret
URL_WITH_CREDENTIALS = "http://user:pw@docling.test:5001"  # pragma: allowlist secret


def _doc() -> DoclingDocument:
    doc = DoclingDocument(name="report")
    doc.add_heading("Results")
    doc.add_text(label="text", text="Revenue grew 12% in the third quarter.")
    return doc


class _Service:
    """A fake docling-serve: answers submit, poll and result like the real one."""

    def __init__(self, statuses=("pending", "started", "success"), result=None, submit_status=200):
        self.statuses = list(statuses)
        self.result = (
            result
            if result is not None
            else {
                "document": {"json_content": _doc().export_to_dict()},
                "status": "success",
                "errors": [],
                "processing_time": 1.5,
            }
        )
        self.submit_status = submit_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/v1/convert/file/async":
            if self.submit_status != 200:
                return httpx.Response(self.submit_status, json={"detail": "nope"})
            return httpx.Response(200, json={"task_id": "t1", "task_status": "pending"})
        if path == "/v1/status/poll/t1":
            status = self.statuses.pop(0)
            body = {"task_id": "t1", "task_status": status}
            if status == "failure":
                body["error_message"] = "PDF is damaged"
            return httpx.Response(200, json=body)
        if path == "/v1/result/t1":
            return httpx.Response(200, json=self.result)
        return httpx.Response(404)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(docling_remote.time, "sleep", lambda s: None)


def _client(service, api_key="", timeout_s=900.0) -> DoclingServeClient:
    return DoclingServeClient(
        URL, api_key=api_key, timeout_s=timeout_s, transport=httpx.MockTransport(service)
    )


@pytest.fixture
def pdf(tmp_path) -> Path:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    return path


def _form(request: httpx.Request) -> str:
    return request.read().decode("latin-1")


def test_convert_polls_until_success_and_returns_the_document(pdf):
    service = _Service()
    doc = _client(service, api_key=KEY).convert(pdf, "balanced")

    assert doc.export_to_dict() == _doc().export_to_dict()
    submit = service.requests[0]
    assert submit.url.path == "/v1/convert/file/async"
    assert submit.headers["X-Api-Key"] == KEY
    assert submit.headers["Accept-Docling-Document-Version"] == CURRENT_VERSION
    body = _form(submit)
    assert 'name="files"; filename="report.pdf"' in body
    assert 'name="to_formats"\r\n\r\njson' in body
    assert [r.url.path for r in service.requests[1:]] == ["/v1/status/poll/t1"] * 3 + ["/v1/result/t1"]


@pytest.mark.parametrize(
    ("pdf_mode", "expected", "absent"),
    [
        ("fast", {"do_ocr": "false", "do_table_structure": "false"}, ["force_ocr"]),
        ("balanced", {"do_ocr": "false", "do_table_structure": "true"}, ["force_ocr"]),
        ("accurate", {"do_ocr": "true", "force_ocr": "true", "do_table_structure": "true"}, []),
    ],
)
def test_pdf_mode_keeps_its_local_meaning(pdf, pdf_mode, expected, absent):
    service = _Service()
    _client(service).convert(pdf, pdf_mode)

    body = _form(service.requests[0])
    for name, value in expected.items():
        assert f'name="{name}"\r\n\r\n{value}' in body
    for name in absent:
        assert f'name="{name}"' not in body
    assert 'name="include_images"\r\n\r\nfalse' in body


def test_pdf_mode_applies_to_pdfs_only_like_local_parsing(tmp_path):
    """Locally, pdf_mode configures the PDF pipeline only: a scanned image is still OCRed."""
    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG test")
    service = _Service()
    _client(service).convert(image, "balanced")

    body = _form(service.requests[0])
    assert 'name="do_ocr"' not in body
    assert 'name="do_table_structure"' not in body
    assert 'name="include_images"\r\n\r\nfalse' in body


def test_a_finished_task_is_not_paused_before_its_result(pdf, monkeypatch):
    pauses: list[float] = []
    monkeypatch.setattr(docling_remote.time, "sleep", pauses.append)
    _client(_Service(statuses=["started", "success"])).convert(pdf, "balanced")
    assert len(pauses) == 1  # after "started" only


def test_no_api_key_sends_no_key_header(pdf):
    service = _Service()
    _client(service).convert(pdf, "balanced")
    assert "X-Api-Key" not in service.requests[0].headers


def test_failed_task_reports_the_service_reason(pdf):
    with pytest.raises(DoclingServiceError, match="failed to parse report.pdf: PDF is damaged"):
        _client(_Service(statuses=["failure"])).convert(pdf, "balanced")


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_key_names_the_setting_and_hides_the_key(pdf, status):
    with pytest.raises(DoclingServiceError) as exc_info:
        _client(_Service(submit_status=status), api_key=KEY).convert(pdf, "balanced")
    message = str(exc_info.value)
    assert f"HTTP {status}" in message
    assert "knowledge.docling.api_key" in message
    assert KEY not in message


def test_other_http_errors_carry_the_detail(pdf):
    with pytest.raises(DoclingServiceError, match=r"HTTP 422.*nope"):
        _client(_Service(submit_status=422)).convert(pdf, "balanced")


def test_unreachable_service_says_so_without_credentials(pdf):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = DoclingServeClient(
        URL_WITH_CREDENTIALS,
        timeout_s=5,
        transport=httpx.MockTransport(refuse),
    )
    with pytest.raises(DoclingServiceError) as exc_info:
        client.convert(pdf, "balanced")
    message = str(exc_info.value)
    assert "http://docling.test:5001 is unreachable" in message
    assert "pw" not in message


def test_deadline_names_the_timeout_setting(pdf, monkeypatch):
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(docling_remote.time, "monotonic", lambda: next(clock))
    with pytest.raises(DoclingServiceError, match=r"within 250s .*started.*knowledge\.docling\.timeout_s"):
        _client(_Service(statuses=["started"] * 50), timeout_s=250).convert(pdf, "balanced")


def test_result_without_a_document_lists_the_errors(pdf):
    result = {
        "document": {"json_content": None},
        "status": "failure",
        "errors": [{"error_message": "bad page"}],
    }
    with pytest.raises(DoclingServiceError, match="bad page"):
        _client(_Service(result=result)).convert(pdf, "balanced")


def test_unreadable_document_names_both_schema_versions(pdf):
    content = _doc().export_to_dict()
    content["version"] = "99.0.0"
    with pytest.raises(DoclingServiceError, match=rf"99\.0\.0.*{CURRENT_VERSION}"):
        _client(_Service(result={"document": {"json_content": content}, "status": "success"})).convert(
            pdf, "balanced"
        )


class _Flaky(_Service):
    """docling-serve behind a restart or under load: the first answers on a path are errors."""

    def __init__(self, path, errors, **kwargs):
        super().__init__(**kwargs)
        self.path, self.errors = path, list(errors)

    def __call__(self, request):
        if request.url.path == self.path and self.errors:
            self.requests.append(request)
            error = self.errors.pop(0)
            if isinstance(error, Exception):
                raise error
            return error
        return super().__call__(request)


def test_polls_ride_out_overload_and_a_dropped_connection(pdf, monkeypatch):
    """docling-serve answers 503 + Retry-After when its queue backend is busy; a proxy or a
    restart drops connections. Polls are reads, so the file keeps its task instead of failing."""
    pauses: list[float] = []
    monkeypatch.setattr(docling_remote.time, "sleep", pauses.append)
    errors = [
        httpx.Response(503, headers={"Retry-After": "3"}),
        httpx.ConnectError("connection reset"),
        httpx.Response(502),
    ]
    service = _Flaky("/v1/status/poll/t1", errors, statuses=["success"])
    doc = _client(service).convert(pdf, "balanced")

    assert doc.export_to_dict() == _doc().export_to_dict()
    assert pauses == [3.0, 2.0, 4.0]  # the service's Retry-After, else a back-off that doubles per retry


def test_the_result_fetch_is_retried_too(pdf):
    service = _Flaky("/v1/result/t1", [httpx.Response(504)], statuses=["success"])
    assert _client(service).convert(pdf, "balanced").export_to_dict() == _doc().export_to_dict()


def test_retries_stop_at_the_files_deadline(pdf, monkeypatch):
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(docling_remote.time, "monotonic", lambda: next(clock))
    service = _Flaky("/v1/status/poll/t1", [httpx.Response(503)] * 50)
    with pytest.raises(DoclingServiceError, match=r"unreachable \(HTTP 503\)"):
        _client(service, timeout_s=250).convert(pdf, "balanced")


def test_a_lost_task_says_why_and_what_to_change(pdf):
    """docling-serve's default engine keeps tasks in process memory: after a restart, or from
    another replica, a poll gets 404."""
    service = _Flaky("/v1/status/poll/t1", [httpx.Response(404, json={"detail": "Task not found."})])
    with pytest.raises(DoclingServiceError) as exc_info:
        _client(service).convert(pdf, "balanced")
    message = str(exc_info.value)
    assert "lost the task for report.pdf" in message
    assert "one replica" in message


def test_the_upload_itself_is_not_retried(pdf):
    """A retried upload could start a second parse of the same file."""
    service = _Flaky("/v1/convert/file/async", [httpx.Response(503)])
    with pytest.raises(DoclingServiceError, match="HTTP 503"):
        _client(service).convert(pdf, "balanced")


def test_health_passes_and_fails():
    _client(_Service()).health()

    def down(request):
        return httpx.Response(503, text="starting")

    with pytest.raises(DoclingServiceError, match="HTTP 503"):
        DoclingServeClient(URL, transport=httpx.MockTransport(down)).health()


def test_load_processor_resolves_a_dotted_path():
    assert load_processor("json:dumps") is json.dumps


@pytest.mark.parametrize("spec", ["json.dumps", "no_such_module_xyz:fn", "json:no_such_attr", "json:__doc__"])
def test_load_processor_rejects_bad_specs(spec):
    with pytest.raises(ValueError, match="knowledge.docling.processor"):
        load_processor(spec)
