"""The shared error helpers must never give exception details to the caller.

These tests cover the fix for the CodeQL rule py/stack-trace-exposure. What they
check is deliberately simple: nothing taken from the exception may appear in the
response body. Not str(exc), not repr(exc), not exc.args, and not the name of the
exception class. Only fixed text written by the caller, plus a random reference
code.
"""

from __future__ import annotations

import json
import sys

import pytest

from cuga.backend.server.error_responses import (
    GENERIC_MESSAGE,
    log_error_ref,
    safe_error_payload,
    safe_error_response,
    safe_http_exception,
)

SECRET = "/srv/secret/path.toml and api_key=sk-live-abcdef"


def _boom() -> Exception:
    """An exception whose text is exactly what must not reach the caller."""
    try:
        raise RuntimeError(SECRET)
    except RuntimeError as exc:
        return exc


def _assert_clean(blob: str) -> None:
    assert SECRET not in blob
    assert "sk-live-abcdef" not in blob
    assert "RuntimeError" not in blob
    assert "Traceback" not in blob
    assert "/srv/secret" not in blob


@pytest.mark.unit
def test_payload_hides_exception_text() -> None:
    try:
        raise RuntimeError(SECRET)
    except RuntimeError as exc:
        payload = safe_error_payload(exc)

    _assert_clean(json.dumps(payload))
    assert payload["status"] == "error"
    assert payload["message"] == GENERIC_MESSAGE
    assert payload["ref"]


@pytest.mark.unit
def test_payload_uses_caller_literal_message() -> None:
    try:
        raise ValueError(SECRET)
    except ValueError as exc:
        payload = safe_error_payload(exc, message="Failed to save policies")

    assert payload["message"] == "Failed to save policies"
    _assert_clean(json.dumps(payload))


@pytest.mark.unit
def test_response_is_json_and_carries_no_traceback_key() -> None:
    try:
        raise RuntimeError(SECRET)
    except RuntimeError as exc:
        response = safe_error_response(exc, status=500, message="Failed to save policies")

    assert response.status_code == 500
    body = json.loads(bytes(response.body))
    assert "traceback" not in body
    _assert_clean(json.dumps(body))


@pytest.mark.unit
def test_http_exception_detail_is_message_and_ref_only() -> None:
    try:
        raise RuntimeError(SECRET)
    except RuntimeError as exc:
        http_exc = safe_http_exception(exc, message="Failed to save manage config")

    assert http_exc.status_code == 500
    _assert_clean(str(http_exc.detail))
    assert "Failed to save manage config" in http_exc.detail


@pytest.mark.unit
def test_ref_is_random_and_not_derived_from_the_exception() -> None:
    exc = _boom()
    refs = {log_error_ref(exc) for _ in range(5)}
    assert len(refs) == 5, "refs must be unique per call, not a digest of the exception"
    for ref in refs:
        assert len(ref) == 12
        _assert_clean(ref)


@pytest.mark.unit
def test_traceback_is_bound_to_the_exception_not_the_ambient_one() -> None:
    """The helper must work when called outside an ``except`` block.

    Python's ``.exception()`` logging call looks up whichever exception is being
    handled right now. Called from somewhere else, such as a callback that runs
    after a task has finished, it would record no stack trace, and the reference
    code would point at a log entry with nothing useful in it.
    """
    import logging

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("test_error_responses.detached")
    logger.setLevel(logging.ERROR)
    logger.addHandler(_Capture())

    # Captured first, then used after the except block has finished.
    exc = _boom()
    assert sys.exc_info() == (None, None, None), "this test needs no exception being handled"

    ref = log_error_ref(exc, log=logger, context="Detached failure")

    assert len(records) == 1
    assert ref in records[0].getMessage()
    assert records[0].exc_info is not None, "the stack trace must come from the argument"
    assert records[0].exc_info[1] is exc


@pytest.mark.unit
def test_ref_reaches_the_log_so_detail_is_recoverable() -> None:
    """The stack trace is not lost. It goes to the log under the returned code."""
    records: list[str] = []

    class _SpyLogger:
        """Shaped like a standard library logger."""

        def error(self, message: str, exc_info: object = None) -> None:
            records.append(message)

    try:
        raise RuntimeError(SECRET)
    except RuntimeError as exc:
        payload = safe_error_payload(exc, log=_SpyLogger(), context="Saving policies failed")

    assert len(records) == 1
    assert payload["ref"] in records[0]
    assert "Saving policies failed" in records[0]
