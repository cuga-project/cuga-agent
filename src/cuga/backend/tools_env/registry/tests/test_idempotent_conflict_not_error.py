"""Regression tests for #596 — responses reporting the desired state ALREADY holds
must be surfaced as satisfied, not as exceptions the agent retries.

Across five AppWorld bundles these accounted for 11,747 of 20,469 error responses
(57%). Tasks still passed (e.g. `0d01c76_1` at match_rate 1.0 with 2,295 such
responses), so the cost was wasted turns and tokens rather than wrong answers.
"""

from __future__ import annotations

import httpx
import pytest
from unittest.mock import AsyncMock

from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
from cuga.backend.tools_env.registry.registry.api_registry import (
    ApiRegistry,
    is_already_satisfied,
)

pytestmark = pytest.mark.unit


def _http_error(status_code: int, message: str) -> httpx.HTTPStatusError:
    """An httpx error shaped like the ones the registry catches."""
    request = httpx.Request("POST", "http://localhost:9111/demo/thing")
    response = httpx.Response(status_code, json={"message": message}, request=request)
    return httpx.HTTPStatusError(f"{status_code} error", request=request, response=response)


# ── classifier ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status,message",
    [
        (409, "Note with this user_id and title already exists."),
        (409, "Playlist with this title and user_id already exists."),
        (409, "anything at all"),  # 409 is idempotent by definition
        (422, "The song is already in the playlist."),
        (422, "This email thread is already marked as archived."),
        (422, "This email thread is already marked as read."),
        (422, "This email thread is already marked as unread."),
    ],
)
def test_classifier_accepts_already_satisfied(status, message):
    assert is_already_satisfied(status, message) is True


@pytest.mark.parametrize(
    "status,message",
    [
        (422, "Your payment card doesn't have enough balance to place the order."),
        (422, "The payment card has expired."),
        (422, "Invalid password reset code."),
        (422, "No files found in directory /./photos/."),
        (422, "The path source_file_path is invalid. It contains double slashes."),
        (400, "Bad request"),
        (500, "Internal server error"),
        (422, None),
    ],
)
def test_classifier_rejects_genuine_failures(status, message):
    """A broad match on "already" would mask these; the allow-list must not."""
    assert is_already_satisfied(status, message) is False


# ── end-to-end through call_function ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_409_already_exists_is_not_an_exception():
    manager = MCPManager(config={})
    registry = ApiRegistry(client=manager)
    manager.call_tool = AsyncMock(
        side_effect=_http_error(409, "Note with this user_id and title already exists.")
    )

    result = await registry.call_function(
        app_name="simple_note", function_name="simple_note_create_note", arguments={"title": "x"}
    )

    assert result["status"] != "exception", "an already-satisfied goal must not read as a failure"
    assert result["already_satisfied"] is True
    # original signal preserved for debugging
    assert result["status_code"] == 409
    assert "already exists" in result["message"]


@pytest.mark.asyncio
async def test_422_already_in_playlist_is_not_an_exception():
    manager = MCPManager(config={})
    registry = ApiRegistry(client=manager)
    manager.call_tool = AsyncMock(side_effect=_http_error(422, "The song is already in the playlist."))

    result = await registry.call_function(
        app_name="spotify", function_name="spotify_add_song_to_playlist", arguments={"song_id": 1}
    )

    assert result["status"] != "exception"
    assert result["already_satisfied"] is True
    assert result["status_code"] == 422


@pytest.mark.asyncio
async def test_genuine_422_still_raises_as_exception():
    """The insufficient-balance case must keep failing — the goal does NOT hold."""
    manager = MCPManager(config={})
    registry = ApiRegistry(client=manager)
    manager.call_tool = AsyncMock(
        side_effect=_http_error(422, "Your payment card doesn't have enough balance to place the order.")
    )

    result = await registry.call_function(
        app_name="amazon", function_name="amazon_place_order", arguments={"payment_card_id": 247}
    )

    assert result["status"] == "exception"
    assert result.get("already_satisfied") is None
    assert result["status_code"] == 422
