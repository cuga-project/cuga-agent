"""The docling-serve client: CUGA sends the file, polls the task and reads the
DoclingDocument back. Every service failure must reach the user as one clear message
that carries neither the API key nor the service's address."""

from __future__ import annotations

import base64
from pathlib import Path

import httpx
import pytest
from docling_core.types.doc import DoclingDocument
from docling_core.types.doc.document import CURRENT_VERSION
from loguru import logger

from cuga.backend.knowledge import docling_remote
from cuga.backend.knowledge.docling_remote import DoclingServeClient, DoclingServiceError

pytestmark = pytest.mark.unit

URL = "http://docling.test:5001"
KEY = "sekret"  # pragma: allowlist secret
ROTATED_KEY = "stale"  # pragma: allowlist secret
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
    """Pauses take no real time but still move the client's clock, so retry windows end."""
    slept = [0.0]
    monotonic = docling_remote.time.monotonic
    monkeypatch.setattr(docling_remote.time, "sleep", lambda s: slept.__setitem__(0, slept[0] + s))
    monkeypatch.setattr(docling_remote.time, "monotonic", lambda: monotonic() + slept[0])


def _client(service, api_key="", timeout_s=3600.0) -> DoclingServeClient:
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
    assert 'name="document_timeout"\r\n\r\n3600.0' in body  # the service stops when CUGA does


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


def test_polls_start_fast_then_settle_at_once_a_second(pdf, monkeypatch):
    """A short file is back within a poll or two; a long one costs one poll a second."""
    pauses: list[float] = []
    monkeypatch.setattr(docling_remote.time, "sleep", pauses.append)
    _client(_Service(statuses=["pending"] * 6 + ["success"])).convert(pdf, "balanced")
    assert pauses == [0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0]  # none after "success"


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


def test_unreachable_service_gives_the_cause_but_not_the_address(pdf):
    def refuse(request):
        raise httpx.ConnectError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", request=request
        )

    client = DoclingServeClient(URL_WITH_CREDENTIALS, timeout_s=5, transport=httpx.MockTransport(refuse))
    with pytest.raises(DoclingServiceError) as exc_info:
        client.convert(pdf, "balanced")
    message = str(exc_info.value)
    assert "unreachable (ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED]" in message
    assert "docling.test" not in message and "pw" not in message


def test_credentials_in_the_url_travel_as_basic_auth_only(pdf):
    """httpx logs every request's URL, so the password must not be part of it."""
    service = _Service()
    client = DoclingServeClient(URL_WITH_CREDENTIALS, transport=httpx.MockTransport(service))
    client.convert(pdf, "balanced")
    assert client.name == URL
    for request in service.requests:
        assert request.url.userinfo == b""
        assert request.headers["Authorization"] == "Basic " + base64.b64encode(b"user:pw").decode()


@pytest.mark.parametrize(("status", "phase"), [("started", "parsing"), ("pending", "waiting in its queue")])
def test_deadline_names_the_phase_and_the_timeout_setting(pdf, monkeypatch, status, phase):
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(docling_remote.time, "monotonic", lambda: next(clock))
    with pytest.raises(
        DoclingServiceError, match=rf"within 250s of {phase}\. Raise knowledge\.docling\.timeout_s"
    ):
        _client(_Service(statuses=[status] * 50), timeout_s=250).convert(pdf, "balanced")


def test_parsing_gets_the_full_timeout_after_time_in_the_queue(pdf, monkeypatch):
    """The service counts document_timeout from when it starts parsing; so does CUGA."""
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(docling_remote.time, "monotonic", lambda: next(clock))
    statuses = ["started"] * 3 + ["success"]  # seen started at 300, done at 600: past 250 from the upload
    doc = _client(_Service(statuses=statuses), timeout_s=250).convert(pdf, "balanced")
    assert doc.export_to_dict() == _doc().export_to_dict()


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
        httpx.Response(503),
        httpx.ConnectError("connection reset"),
        httpx.Response(502),
        httpx.Response(429),
        httpx.Response(504),
    ]
    service = _Flaky("/v1/status/poll/t1", errors, statuses=["success"])
    doc = _client(service).convert(pdf, "balanced")

    assert doc.export_to_dict() == _doc().export_to_dict()
    assert pauses == [0.05, 1.0, 2.0, 4.0, 8.0, 10.0]  # first poll, then a back-off that doubles to 10 s


def test_an_error_inside_the_service_is_not_retried(pdf):
    """A 500 from docling-serve is an unhandled error on its side: the same request fails again."""
    service = _Flaky(
        "/v1/result/t1", [httpx.Response(500, text="Internal Server Error")], statuses=["success"]
    )
    with pytest.raises(DoclingServiceError, match="answered HTTP 500: Internal Server Error"):
        _client(service).convert(pdf, "balanced")


def test_the_result_fetch_is_retried_too(pdf):
    service = _Flaky("/v1/result/t1", [httpx.Response(504)], statuses=["success"])
    assert _client(service).convert(pdf, "balanced").export_to_dict() == _doc().export_to_dict()


def test_retries_stop_at_the_files_deadline(pdf, monkeypatch):
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(docling_remote.time, "monotonic", lambda: next(clock))
    service = _Flaky("/v1/status/poll/t1", [httpx.Response(503, text="Server is busy")] * 50)
    with pytest.raises(DoclingServiceError, match=r"answered HTTP 503: Server is busy"):
        _client(service, timeout_s=250).convert(pdf, "balanced")


def test_an_upload_that_cannot_connect_fails_within_two_minutes(pdf, monkeypatch):
    """Long enough for a docling-serve restart; a wrong address or certificate fails every time,
    so the user hears about it in minutes, not after timeout_s."""
    clock = iter(range(0, 100_000, 10))
    monkeypatch.setattr(docling_remote.time, "monotonic", lambda: next(clock))
    service = _Flaky("/v1/convert/file/async", [httpx.ConnectError("refused")] * 1000)
    with pytest.raises(DoclingServiceError, match="unreachable"):
        _client(service).convert(pdf, "balanced")
    assert 1 < len(service.requests) < 20


def test_a_tls_error_names_the_cause_but_not_the_host(pdf):
    def mismatch(request):
        raise httpx.ConnectError(
            "certificate verify failed: Hostname mismatch, certificate is not valid for 'docling.test'",
            request=request,
        )

    client = DoclingServeClient(URL, transport=httpx.MockTransport(mismatch))
    with pytest.raises(DoclingServiceError, match="Hostname mismatch") as exc_info:
        client.convert(pdf, "balanced")
    assert "docling.test" not in str(exc_info.value)


def test_a_body_that_cannot_be_decoded_reads_as_a_service_error(pdf):
    def garbled(request):
        if request.url.path == "/v1/convert/file/async":
            return httpx.Response(200, json={"task_id": "t1", "task_status": "success"})
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"not gzip")

    with pytest.raises(DoclingServiceError, match="DecodingError"):
        DoclingServeClient(URL, transport=httpx.MockTransport(garbled)).convert(pdf, "balanced")


def test_a_lost_task_says_why_and_what_to_change(pdf):
    """docling-serve's default engine keeps tasks in process memory: after a restart, or from
    another replica, a poll gets 404."""
    service = _Flaky("/v1/status/poll/t1", [httpx.Response(404, json={"detail": "Task not found."})])
    with pytest.raises(DoclingServiceError) as exc_info:
        _client(service).convert(pdf, "balanced")
    message = str(exc_info.value)
    assert "lost the task" in message
    assert "one replica" in message


@pytest.mark.parametrize(
    "refusal",
    [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("timed out"),
        httpx.Response(503),
        httpx.Response(429),
    ],
)
def test_an_upload_the_service_never_took_is_sent_again(pdf, refusal):
    """No connection, or docling-serve's busy answer: nothing was queued, so sending again is safe."""
    service = _Flaky("/v1/convert/file/async", [refusal])
    _client(service).convert(pdf, "balanced")
    uploads = [r for r in service.requests if r.url.path == "/v1/convert/file/async"]
    assert len(uploads) == 2
    assert b"%PDF-1.4 test" in uploads[1].read()  # the file was sent again from its start


@pytest.mark.parametrize(
    ("refusal", "message"),
    [(httpx.Response(502), "HTTP 502"), (httpx.ReadTimeout("no answer"), "ReadTimeout")],
)
def test_an_upload_the_service_may_have_taken_is_not_sent_again(pdf, refusal, message):
    """A gateway error or a lost answer can follow a queued task: a second upload could parse twice."""
    service = _Flaky("/v1/convert/file/async", [refusal])
    with pytest.raises(DoclingServiceError, match=message):
        _client(service).convert(pdf, "balanced")


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (
            httpx.Response(301, headers={"location": "https://login.test/"}),
            r"redirects elsewhere \(HTTP 301\)",
        ),
        (httpx.Response(200, text="<html>Sign in</html>"), "did not answer like docling-serve"),
        (httpx.Response(200, json={"status": "queued"}), "no task id"),
    ],
)
def test_a_url_that_is_not_docling_serve_says_so(pdf, answer, message):
    """A login page, a redirect to https or another app's port must not read as a Python error."""
    service = _Flaky("/v1/convert/file/async", [answer])
    with pytest.raises(DoclingServiceError, match=message):
        _client(service).convert(pdf, "balanced")


def test_a_redirect_fails_the_health_check_without_naming_where_it_goes():
    def moved(request):
        return httpx.Response(
            301, headers={"location": "https://sso.test/login?next=http://docling.test:5001/"}
        )

    with pytest.raises(DoclingServiceError, match="redirects elsewhere") as exc_info:
        DoclingServeClient(URL, transport=httpx.MockTransport(moved)).health()
    assert "sso.test" not in str(exc_info.value) and "docling.test" not in str(exc_info.value)


def test_the_health_check_needs_the_api_key():
    """docling-serve's /health answers without a key; polling a task does not. A reindex relies
    on this check before it drops anything, so a wrong key must fail it."""
    seen: list[httpx.Request] = []

    def service(request):
        seen.append(request)
        if request.headers.get("X-Api-Key") != KEY:
            return httpx.Response(401, json={"detail": "Invalid API key"})
        return httpx.Response(404, json={"detail": "Task not found."})

    DoclingServeClient(URL, api_key=KEY, transport=httpx.MockTransport(service)).health()
    assert seen[0].url.path.startswith("/v1/status/poll/")
    with pytest.raises(DoclingServiceError, match="rejected the API key"):
        DoclingServeClient(URL, api_key=ROTATED_KEY, transport=httpx.MockTransport(service)).health()


def test_a_partly_parsed_document_is_indexed_and_logged(pdf):
    result = {
        "document": {"json_content": _doc().export_to_dict()},
        "status": "partial_success",
        "errors": [{"error_message": "page 7 failed"}],
    }
    lines: list[str] = []
    sink = logger.add(lines.append, level="WARNING")
    try:
        doc = _client(_Service(result=result)).convert(pdf, "balanced")
    finally:
        logger.remove(sink)
    assert doc.export_to_dict() == _doc().export_to_dict()
    assert any("partial_success" in line and "page 7 failed" in line for line in lines)


def test_health_passes_and_fails():
    _client(_Service()).health()

    def down(request):
        return httpx.Response(503, text="starting")

    with pytest.raises(DoclingServiceError, match="HTTP 503"):
        DoclingServeClient(URL, transport=httpx.MockTransport(down)).health()
