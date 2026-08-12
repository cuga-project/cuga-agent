"""#596 regression tests for the MCP **adapter** path.

This is the path that serves AppWorld traffic. The first version of the fix was
applied only to `registry.api_registry`, so it never fired in production: an
evaluation run with 270 `422 "already marked as read"` responses produced zero
reclassifications. These tests drive `create_handler`'s error branch with the
exact response shape observed in those traces.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
from pydantic import BaseModel

from cuga.backend.tools_env.registry.mcp_manager.adapter import create_handler

pytestmark = pytest.mark.unit


class _Params(BaseModel):
    pass


def _api():
    # `request_body=None` matters: extract_body_params / determine_content_type
    # dereference it, and a bare SimpleNamespace without it raises AttributeError
    # before the HTTP call is ever made.
    return SimpleNamespace(
        name="gmail_mark_read",
        method="POST",
        operation_id="mark_read",
        path="/email_threads/{id}/read",
        parameters=[],
        request_body=None,
    )


def _response(status: int, body: str) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body.encode()
    r.headers["content-type"] = "application/json"
    r.url = "http://localhost:9111/gmail/email_threads/29695/read"
    return r


def _run_handler(status: int, body: str):
    """Invoke the real handler with a mocked HTTP layer; return its result."""
    resp = _response(status, body)
    handler = create_handler(_api(), _Params, "http://localhost:9111", "gmail", {})
    with (
        patch(
            "cuga.backend.tools_env.registry.mcp_manager.adapter.requests.request",
            return_value=resp,
        ),
        patch(
            "cuga.backend.tools_env.registry.mcp_manager.adapter.extract_url_params",
            return_value=({}, {}),
        ),
        patch(
            "cuga.backend.tools_env.registry.mcp_manager.adapter.get_operation_override_parameters",
            return_value=None,
        ),
    ):
        return handler(_Params())


def test_422_already_marked_as_read_is_satisfied_on_adapter_path():
    """The exact shape seen 270x in bundle 20260811_165801 (task 277d81d_1)."""
    result = _run_handler(422, '{"message": "This email thread is already marked as read."}')

    assert isinstance(result, dict), "an error result should still be a dict"
    assert result["status"] != "exception", (
        "already-satisfied must not read as a failure on the adapter path (#596)"
    )
    assert result["already_satisfied"] is True
    assert result["status_code"] == 422
    assert "already marked as read" in result["message"]


def test_409_already_exists_is_satisfied_on_adapter_path():
    result = _run_handler(409, '{"message": "Note with this user_id and title already exists."}')

    assert result["status"] != "exception"
    assert result["already_satisfied"] is True
    assert result["status_code"] == 409


def test_genuine_422_still_fails_on_adapter_path():
    """Insufficient balance is a real failure — the goal does NOT hold."""
    result = _run_handler(
        422, '{"message": "Your payment card doesn\'t have enough balance to place the order."}'
    )

    assert result["status"] == "exception"
    assert result.get("already_satisfied") is None
    assert result["status_code"] == 422


def test_500_is_untouched_on_adapter_path():
    result = _run_handler(500, '{"message": "Internal server error"}')

    assert result["status"] == "exception"
    assert result.get("already_satisfied") is None
