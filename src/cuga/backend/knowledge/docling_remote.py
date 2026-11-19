"""Parse documents in docling-serve, or in any service that answers its v1 async convert API
with the document inline (docling-serve's default).

CUGA sends the file and gets a DoclingDocument back as JSON, then chunks it itself. The
service's Docling version and OCR engine decide the text and headings it returns. The client
is synchronous on purpose: ``KnowledgeEngine._load_document`` already runs in a worker thread.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

import httpx
from loguru import logger

if TYPE_CHECKING:
    from docling_core.types.doc import DoclingDocument

# Same meaning as the local converter's pdf_mode (engine._get_docling_converter), which
# applies it to PDFs only: other formats keep Docling's defaults (an image is still OCRed).
_PDF_MODE_FIELDS: dict[str, dict[str, str]] = {
    "fast": {"do_ocr": "false", "do_table_structure": "false"},
    "balanced": {"do_ocr": "false", "do_table_structure": "true"},
    "accurate": {"do_ocr": "true", "force_ocr": "true", "do_table_structure": "true"},
}
# A status poll or result fetch is a read, so a busy or gateway answer, or a dropped
# connection, is retried. An upload is retried only when the service most likely took nothing:
# no connection was made, or it answered busy (429, or 503 while its queue is full; with the RQ
# engine a 503 can rarely follow a queued task, which then parses twice with the same result).
# A request keeps retrying for _RETRY_S at most, enough for a docling-serve restart: a wrong
# address or certificate fails the same way every time, and a service that stays down should not
# hold an ingest slot until timeout_s.
_RETRY_READ = frozenset({429, 502, 503, 504})
_RETRY_UPLOAD = frozenset({429, 503})
_NOT_CONNECTED = (httpx.ConnectError, httpx.ConnectTimeout)
_RETRY_S = 60.0


class DoclingServiceError(RuntimeError):
    """The document service could not parse a file. The message is safe to show users: it
    never names the service's address (the engine logs that once, at start)."""


class DoclingServeClient:
    def __init__(
        self,
        url: str,
        api_key: str = "",
        timeout_s: float = 3600.0,
        transport: httpx.BaseTransport | None = None,
    ):
        parts = urlsplit(url.rstrip("/"))
        # The address without user:password, so neither CUGA nor httpx ever logs a password.
        self.name = parts._replace(netloc=parts.netloc.rpartition("@")[2]).geturl()
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"knowledge.docling.url must look like http://host:port, got {self.name!r}")
        self._timeout_s = timeout_s
        self._http = httpx.Client(
            base_url=self.name,
            headers={"X-Api-Key": api_key} if api_key else {},
            auth=httpx.BasicAuth(unquote(parts.username), unquote(parts.password or ""))
            if parts.username
            else None,
            timeout=httpx.Timeout(60.0),
            transport=transport,
        )

    def health(self) -> None:
        """The service answers and accepts the API key. docling-serve's /health needs no key, so
        this polls a task that does not exist: docling-serve's own "Task not found." is healthy."""
        try:
            response = self._http.get(f"/v1/status/poll/{uuid.uuid4().hex}")
        except httpx.RequestError as e:
            raise DoclingServiceError(f"The document service is unreachable ({self._cause(e)})") from e
        if response.status_code != 404:
            _json(response)
        elif "task not found" not in response.text.lower():  # a proxy or another app 404s every path
            raise _not_docling_serve("HTTP 404 on its status endpoint")

    def close(self) -> None:
        self._http.close()

    def convert(self, file_path: Path, pdf_mode: str) -> DoclingDocument:
        """Parse ``file_path`` in the service and return its DoclingDocument. The file may wait
        ``timeout_s`` in the service's queue, then gets ``timeout_s`` to parse: the service
        stops at the same moment, since it gets the same number as its document_timeout."""
        from docling_core.types.doc import DoclingDocument
        from docling_core.types.doc.document import CURRENT_VERSION
        from pydantic import ValidationError

        name = file_path.name
        version = {"Accept-Docling-Document-Version": CURRENT_VERSION}
        t0 = time.monotonic()
        deadline = t0 + self._timeout_s
        fields = {
            "to_formats": "json",
            "document_timeout": str(self._timeout_s),
            "include_images": "false",  # CUGA indexes text only; local parsing makes no images either
            **(_PDF_MODE_FIELDS[pdf_mode] if file_path.suffix.lower() == ".pdf" else {}),
        }
        with file_path.open("rb") as fh:  # httpx rewinds the file if the upload is retried
            task = self._send(
                "POST",
                "/v1/convert/file/async",
                deadline,
                _RETRY_UPLOAD,
                _NOT_CONNECTED,
                files={"files": (name, fh)},
                data=fields,
                headers=version,
            )
        t_submitted = time.monotonic()
        task_id, status, t_started, pause = task.get("task_id"), task.get("task_status"), None, 0.05
        if not task_id:
            raise _not_docling_serve("no task id")
        while status not in ("success", "failure"):
            now = time.monotonic()
            if status == "started" and t_started is None:
                t_started, deadline = now, now + self._timeout_s
            if now >= deadline:
                raise DoclingServiceError(
                    f"The document service did not finish {name} within {self._timeout_s:.0f}s of "
                    f"{'parsing' if t_started else 'waiting in its queue'}. Raise knowledge.docling.timeout_s."
                )
            time.sleep(pause)  # a short file is back within a poll or two; long ones are polled each second
            pause = min(pause * 2, 1.0)
            task = self._send(
                "GET", f"/v1/status/poll/{task_id}", deadline, _RETRY_READ, httpx.TransportError
            )
            status = task.get("task_status")
        if status == "failure":
            reason = task.get("error_message") or "no reason given"
            raise DoclingServiceError(f"The document service failed to parse {name}: {reason}")

        t_done = time.monotonic()
        result = self._send(
            "GET",
            f"/v1/result/{task_id}",
            max(deadline, t_done + 60),
            _RETRY_READ,
            httpx.TransportError,
            headers=version,
        )
        document = result.get("document")
        content = document.get("json_content") if isinstance(document, dict) else None
        if not isinstance(content, dict) or not content:
            raise DoclingServiceError(
                f"The document service returned no document for {name} (status {result.get('status')}); "
                f"errors: {result.get('errors') or 'none given'}"
            )
        if result.get("status") != "success":  # e.g. partial_success: some pages are missing
            logger.warning(
                "Document service parsed {} with status {}: {}",
                name,
                result.get("status"),
                str(result.get("errors"))[:300],
            )
        try:
            doc = DoclingDocument.model_validate(content)
        except ValidationError as e:
            raise DoclingServiceError(
                f"The document service returned a DoclingDocument v{content.get('version')} for {name}; "
                f"this CUGA reads up to v{CURRENT_VERSION} ({e.errors()[0]['msg']})"
            ) from e
        logger.info(
            "docling-serve timings ({}): upload_s={:.2f} queue_s={} processing_s={} download_s={:.2f}",
            name,
            t_submitted - t0,
            f"{t_started - t_submitted:.2f}" if t_started else "n/a",
            result.get("processing_time"),
            time.monotonic() - t_done,
        )
        return doc

    def _send(
        self,
        method: str,
        path: str,
        deadline: float,
        retry_codes: frozenset[int],
        retry_errors: tuple[type[Exception], ...] | type[Exception],
        files: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """One request, repeated with a pause that doubles from 1 s to 10 s while the answer
        is in ``retry_codes`` or the error in ``retry_errors``, for at most ``_RETRY_S`` and never
        past ``deadline``. Returns the JSON body."""
        pause, give_up = 1.0, min(deadline, time.monotonic() + _RETRY_S)
        while True:
            try:
                response = self._http.request(method, path, files=files, data=data, headers=headers)
            except httpx.RequestError as e:
                if not isinstance(e, retry_errors) or time.monotonic() + pause >= give_up:
                    raise DoclingServiceError(
                        f"The document service is unreachable ({self._cause(e)})"
                    ) from e
            else:
                if response.status_code not in retry_codes or time.monotonic() + pause >= give_up:
                    return _json(response)
            time.sleep(pause)
            pause = min(pause * 2, 10.0)

    def _cause(self, error: httpx.RequestError) -> str:
        """The error's type and text (a TLS or DNS reason) for users, without the service's host."""
        host = self._http.base_url.host
        text = str(error).replace(host, "<service>") if host else str(error)
        logger.warning("Document service {}: {}: {}", self.name, type(error).__name__, error)
        return f"{type(error).__name__}: {text}" if text else type(error).__name__


def _json(response: httpx.Response) -> dict[str, Any]:
    code, path = response.status_code, response.request.url.path
    if code in (401, 403):
        raise DoclingServiceError(
            f"The document service rejected the API key (HTTP {code}). Check knowledge.docling.api_key."
        )
    if code == 404 and ("/v1/status/poll/" in path or "/v1/result/" in path):
        raise DoclingServiceError(
            "The document service lost the task: it restarted, or another replica answered. "
            "docling-serve keeps tasks in memory unless it runs its RQ engine with Redis, so run it "
            "as one replica. Upload the file again."
        )
    if response.is_redirect:
        logger.warning(
            "Document service redirects {} to {}", response.request.url, response.headers.get("location")
        )
        raise DoclingServiceError(
            f"The document service redirects elsewhere (HTTP {code}). Check knowledge.docling.url, "
            "for example http against https."
        )
    if not response.is_success:
        raise DoclingServiceError(f"The document service answered HTTP {code}: {response.text[:300]}")
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise _not_docling_serve(f"HTTP {code}, {response.headers.get('content-type') or 'no content type'}")
    return body


def _not_docling_serve(what: str) -> DoclingServiceError:
    return DoclingServiceError(
        f"The document service did not answer like docling-serve ({what}). Check knowledge.docling.url."
    )
