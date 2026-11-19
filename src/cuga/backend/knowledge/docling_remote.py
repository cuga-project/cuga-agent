"""Parse documents in docling-serve, or in any service that speaks its v1 async convert API.

CUGA sends the file and gets a DoclingDocument back as JSON, then chunks it itself, so
chunks, page numbers and citations are the same as with local parsing. The client is
synchronous on purpose: ``KnowledgeEngine._load_document`` already runs in a worker thread.
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlsplit

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
_POLL_WAIT_S = 5.0  # the service holds a poll open up to this long while the task runs


class DoclingServiceError(RuntimeError):
    """The document service could not parse a file. The message is safe to show users."""


class DoclingServeClient:
    def __init__(
        self,
        url: str,
        api_key: str = "",
        timeout_s: float = 900.0,
        transport: httpx.BaseTransport | None = None,
    ):
        parts = urlsplit(url.rstrip("/"))
        # Messages and logs name the service without any user:password in the URL.
        self.name = parts._replace(netloc=parts.netloc.rpartition("@")[2]).geturl()
        self._timeout_s = timeout_s
        from docling_core.types.doc.document import CURRENT_VERSION

        headers = {"Accept-Docling-Document-Version": CURRENT_VERSION}
        if api_key:
            headers["X-Api-Key"] = api_key
        self._http = httpx.Client(
            base_url=url.rstrip("/"), headers=headers, timeout=httpx.Timeout(60.0), transport=transport
        )

    def health(self) -> None:
        self._request("GET", "/health")

    def close(self) -> None:
        self._http.close()

    def convert(self, file_path: Path, pdf_mode: str) -> DoclingDocument:
        """Parse ``file_path`` in the service and return its DoclingDocument."""
        from docling_core.types.doc import DoclingDocument
        from docling_core.types.doc.document import CURRENT_VERSION
        from pydantic import ValidationError

        t0 = time.monotonic()
        deadline = t0 + self._timeout_s
        fields = {
            "to_formats": "json",
            "document_timeout": str(self._timeout_s),
            "include_images": "false",  # CUGA indexes text only; local parsing makes no images either
            **(_PDF_MODE_FIELDS[pdf_mode] if file_path.suffix.lower() == ".pdf" else {}),
        }
        with file_path.open("rb") as fh:
            task = self._request(
                "POST", "/v1/convert/file/async", files={"files": (file_path.name, fh)}, data=fields
            ).json()
        t_submitted = time.monotonic()
        task_id, status, t_started = task["task_id"], task.get("task_status"), None
        while status not in ("success", "failure"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DoclingServiceError(
                    f"Document service {self.name} did not finish {file_path.name} within "
                    f"{self._timeout_s:.0f}s (last status {status}). Raise knowledge.docling.timeout_s."
                )
            t_poll = time.monotonic()
            task = self._get(
                f"/v1/status/poll/{task_id}",
                file_path.name,
                deadline,
                params={"wait": min(_POLL_WAIT_S, remaining)},
            ).json()
            status = task.get("task_status")
            if status == "started" and t_started is None:
                t_started = time.monotonic()
            if status not in ("success", "failure"):  # at most one poll a second
                time.sleep(max(0.0, 1.0 - (time.monotonic() - t_poll)))
        if status == "failure":
            reason = task.get("error_message") or "no reason given"
            raise DoclingServiceError(
                f"Document service {self.name} failed to parse {file_path.name}: {reason}"
            )

        t_done = time.monotonic()
        result = self._get(f"/v1/result/{task_id}", file_path.name, deadline).json()
        content = (result.get("document") or {}).get("json_content")
        if not content:
            raise DoclingServiceError(
                f"Document service {self.name} returned no document for {file_path.name} "
                f"(status {result.get('status')}); errors: {result.get('errors') or 'none given'}"
            )
        try:
            doc = DoclingDocument.model_validate(content)
        except ValidationError as e:
            raise DoclingServiceError(
                f"Document service {self.name} returned a DoclingDocument v{content.get('version')} "
                f"for {file_path.name}; this CUGA reads up to v{CURRENT_VERSION} ({e.errors()[0]['msg']})"
            ) from e
        logger.info(
            "docling-serve timings ({}): upload_s={:.2f} queue_s={} processing_s={} download_s={:.2f}",
            file_path.name,
            t_submitted - t0,
            f"{t_started - t_submitted:.2f}" if t_started else "n/a",
            result.get("processing_time"),
            time.monotonic() - t_done,
        )
        return doc

    def _get(self, path: str, filename: str, deadline: float, **kwargs: Any) -> httpx.Response:
        """A status poll or result fetch. Both are reads, so a dropped connection, a proxy error
        or a busy service (503 + Retry-After) is retried until the file's deadline."""
        pause = 1.0
        while True:
            try:
                response = self._http.get(path, **kwargs)
            except httpx.TransportError as e:
                why, wait = type(e).__name__, pause
            else:
                if response.status_code == 404:
                    raise DoclingServiceError(
                        f"Document service {self.name} lost the task for {filename}: it restarted, or "
                        "another replica answered. docling-serve keeps tasks in memory unless it runs "
                        "its RQ engine with Redis, so run it as one replica. Upload the file again."
                    )
                if response.status_code not in (502, 503, 504):
                    return self._check(response)
                why, wait = (
                    f"HTTP {response.status_code}",
                    _seconds(response.headers.get("Retry-After"), pause),
                )
            if time.monotonic() + wait >= deadline:
                raise DoclingServiceError(f"Document service {self.name} is unreachable ({why})")
            time.sleep(wait)
            pause = min(pause * 2, 10.0)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as e:
            raise DoclingServiceError(
                f"Document service {self.name} is unreachable ({type(e).__name__})"
            ) from e
        return self._check(response)

    def _check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code in (401, 403):
            raise DoclingServiceError(
                f"Document service {self.name} rejected the API key (HTTP {response.status_code}). "
                "Check knowledge.docling.api_key."
            )
        if response.is_error:
            raise DoclingServiceError(
                f"Document service {self.name} returned HTTP {response.status_code}: {response.text[:300]}"
            )
        return response


def _seconds(retry_after: str | None, default: float) -> float:
    """Retry-After in seconds (the HTTP-date form falls back to the default), capped at 30 s."""
    try:
        return min(float(retry_after), 30.0) if retry_after else default
    except ValueError:
        return default


def load_processor(spec: str) -> Callable[[Path], DoclingDocument]:
    """Resolve ``knowledge.docling.processor`` ("package.module:function") to the function."""
    module_name, _, attr = spec.partition(":")
    try:
        if not module_name or not attr:
            raise ValueError("expected 'package.module:function'")
        fn = getattr(importlib.import_module(module_name), attr)
        if not callable(fn):
            raise ValueError(f"{attr} is not callable")
    except (ImportError, AttributeError, ValueError) as e:
        raise ValueError(f"Cannot load knowledge.docling.processor {spec!r}: {e}") from e
    return fn
