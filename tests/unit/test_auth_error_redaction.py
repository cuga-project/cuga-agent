"""Failure details from AppWorld sign-in must not put credentials in the logs.

These tests cover the fix for the CodeQL rule py/clear-text-logging-sensitive-data.
The addresses these handlers call are a sign-in form and a list of stored account
passwords, so the body that was sent and the authorisation header are the parts
that must not be recorded.
"""

from __future__ import annotations

import httpx
import pytest

from cuga.backend.tools_env.registry.registry.authentication.appworld_auth_manager import (
    _log_http_status_error,
    _mask_identifier,
    _redact_headers,
)


@pytest.mark.unit
def test_credential_headers_are_replaced() -> None:
    redacted = _redact_headers(
        {
            "Authorization": "Bearer sk-live-abcdef",
            "Cookie": "session=deadbeef",
            "X-API-Key": "super-secret",  # pragma: allowlist secret
            "Content-Type": "application/x-www-form-urlencoded",
        }
    )

    assert redacted["Authorization"] == "***"
    assert redacted["Cookie"] == "***"
    assert redacted["X-API-Key"] == "***"
    # Headers that are not credentials stay readable, as they help with troubleshooting.
    assert redacted["Content-Type"] == "application/x-www-form-urlencoded"


@pytest.mark.unit
def test_header_matching_is_case_insensitive() -> None:
    assert _redact_headers({"AUTHORIZATION": "Bearer x"})["AUTHORIZATION"] == "***"
    assert _redact_headers({"authorization": "Bearer x"})["authorization"] == "***"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("user@example.com", "u***m"),
        ("+15551234567", "+***7"),
        ("ab", "***"),
        ("", "***"),
    ],
)
def test_identifier_masking(value: str, expected: str) -> None:
    assert _mask_identifier(value) == expected


@pytest.mark.unit
def test_request_body_is_never_read_off_the_wire(caplog: pytest.LogCaptureFixture) -> None:
    """The real sign-in body contains a username and password, so it must not be logged."""
    request = httpx.Request(
        "POST",
        "http://localhost:9000/phone/auth/token",
        headers={"Authorization": "Bearer sk-live-abcdef"},
        data={"username": "user@example.com", "password": "hunter2"},  # pragma: allowlist secret
    )
    response = httpx.Response(401, json={"message": "bad credentials"}, request=request)
    exc = httpx.HTTPStatusError("401", request=request, response=response)

    logged: list[str] = []

    import cuga.backend.tools_env.registry.registry.authentication.appworld_auth_manager as mod

    handler_id = mod.logger.add(lambda msg: logged.append(str(msg)), level="ERROR")
    try:
        body = _log_http_status_error(
            exc,
            "fetching token for phone",
            request_body=f"username={_mask_identifier('user@example.com')}&password=***",
        )
    finally:
        mod.logger.remove(handler_id)

    blob = "\n".join(logged)
    assert "hunter2" not in blob
    assert "sk-live-abcdef" not in blob
    assert "user@example.com" not in blob
    # The parts that help with troubleshooting are still there.
    assert "401" in blob
    assert "password=***" in blob
    # The response body is returned so the caller can still build its error.
    assert body == {"message": "bad credentials"}
