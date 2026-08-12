"""Repeat-call annotation (#596).

Every case here is a sequence taken from AppWorld evaluation bundles, because
the rule is about call *history*, not about any single response:

* `d194965_1` — `add_song_to_playlist` succeeds, the agent re-runs its script,
  the identical call comes back 422 "The song is already in the playlist."
  That is the state we set ourselves: annotate.
* `ac62177_1` — `mark_thread_unread(47823)` returns 409 "does not exist" with no
  prior success. Genuine failure: no annotation. Every 409 observed across five
  bundles is of this kind, which is why status code alone cannot be the signal.
* `gmail_show_thread` — a GET returning "does not exist" after an identical
  success (22 observed). A failing read establishes nothing: no annotation.
* `amazon_place_order()` / `spotify_previous_song()` — no arguments, so a prior
  success says nothing about the current goal state (98 mutating calls succeeded
  more than once with identical arguments across the bundles, these among them).

In all cases `status` and `message` must survive untouched: the caller still
sees a failure, and the annotation is only evidence alongside it.
"""

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import call_history

pytestmark = pytest.mark.unit


ADD_SONG = "spotify_add_song_to_playlist_playlists_playlist_id_songs_post"
SHOW_THREAD = "gmail_show_thread_email_threads_email_thread_id_get"
MARK_UNREAD = "gmail_mark_thread_unread_email_threads_email_thread_id_unread_post"


def _already_in_playlist():
    return {
        "status": "exception",
        "error_type": "HTTPError",
        "message": '422 Client Error: Unprocessable Entity for url: '
        'http://localhost:9111/spotify/playlists/654/songs '
        '{"message": "The song is already in the playlist."}',
        "status_code": 422,
        "url": "http://localhost:9111/spotify/playlists/654/songs",
        "method": "POST",
    }


def _does_not_exist(method="POST"):
    return {
        "status": "exception",
        "error_type": "HTTPError",
        "message": '409 Client Error: Conflict for url: '
        'http://localhost:9111/gmail/email_threads/47823/unread '
        '{"message": "The email thread with id 47823 does not exist."}',
        "status_code": 409,
        "url": "http://localhost:9111/gmail/email_threads/47823/unread",
        "method": method,
    }


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "annotate_repeat_calls", True, raising=False)
    call_history.reset()
    yield
    call_history.reset()


def _observe(name, args, result):
    return call_history.observe("spotify", name, args, result)


def test_repeat_of_successful_mutation_is_annotated():
    """d194965_1: the song is in the playlist because we put it there."""
    args = {"playlist_id": 654, "song_id": 1}
    _observe(ADD_SONG, args, {"message": "Song added to playlist."})

    result = _observe(ADD_SONG, args, _already_in_playlist())

    assert result["prior_identical_success"] == {"count": 1, "calls_since": 1}


def test_annotation_never_alters_status_or_message():
    """The caller must still see the failure exactly as the API reported it."""
    args = {"playlist_id": 654, "song_id": 1}
    original = _already_in_playlist()
    _observe(ADD_SONG, args, {"message": "Song added to playlist."})

    result = _observe(ADD_SONG, args, dict(original))

    assert result["status"] == "exception"
    assert result["message"] == original["message"]
    assert result["status_code"] == 422


def test_genuine_not_found_is_not_annotated():
    """ac62177_1: 409 with no prior success stays a plain error."""
    result = _observe(MARK_UNREAD, {"email_thread_id": 47823}, _does_not_exist())

    assert "prior_identical_success" not in result


def test_failing_read_after_success_is_not_annotated():
    """A GET establishes nothing, so a prior success cannot vouch for it."""
    args = {"email_thread_id": 47823}
    _observe(SHOW_THREAD, args, {"id": 47823, "subject": "..."})

    result = _observe(SHOW_THREAD, args, _does_not_exist(method="GET"))

    assert "prior_identical_success" not in result


def test_argument_free_call_is_not_annotated():
    """amazon_place_order() / spotify_previous_song(): effect is server-side state."""
    _observe("amazon_place_order_orders_post", {}, {"order_id": 12})

    result = _observe("amazon_place_order_orders_post", {}, _does_not_exist())

    assert "prior_identical_success" not in result


def test_different_arguments_do_not_match():
    args = {"playlist_id": 654, "song_id": 1}
    _observe(ADD_SONG, args, {"message": "Song added to playlist."})

    result = _observe(ADD_SONG, {"playlist_id": 654, "song_id": 2}, _already_in_playlist())

    assert "prior_identical_success" not in result


def test_argument_order_does_not_matter():
    _observe(ADD_SONG, {"playlist_id": 654, "song_id": 1}, {"message": "Song added."})

    result = _observe(ADD_SONG, {"song_id": 1, "playlist_id": 654}, _already_in_playlist())

    assert result["prior_identical_success"]["count"] == 1


def test_repeat_count_and_recency_are_reported():
    """`calls_since` is the staleness cue for state that may have been reverted."""
    args = {"playlist_id": 654, "song_id": 1}
    _observe(ADD_SONG, args, {"message": "Song added to playlist."})
    _observe(ADD_SONG, args, {"message": "Song added to playlist."})
    for _ in range(3):
        _observe("spotify_show_playlist_playlists_playlist_id_get", {"playlist_id": 654}, {"id": 654})

    result = _observe(ADD_SONG, args, _already_in_playlist())

    assert result["prior_identical_success"] == {"count": 2, "calls_since": 4}


def test_5xx_is_not_annotated():
    args = {"playlist_id": 654, "song_id": 1}
    _observe(ADD_SONG, args, {"message": "Song added to playlist."})
    server_error = dict(_already_in_playlist(), status_code=500)

    result = _observe(ADD_SONG, args, server_error)

    assert "prior_identical_success" not in result


def test_history_does_not_cross_tasks(monkeypatch):
    """A success in one task can never speak for another."""
    from cuga.backend.activity_tracker.tracker import ActivityTracker

    tracker = ActivityTracker()
    args = {"playlist_id": 654, "song_id": 1}

    monkeypatch.setattr(tracker, "task_id", "d194965_1", raising=False)
    call_history.reset()
    _observe(ADD_SONG, args, {"message": "Song added to playlist."})

    monkeypatch.setattr(tracker, "task_id", "8a13317_1", raising=False)
    result = _observe(ADD_SONG, args, _already_in_playlist())

    assert "prior_identical_success" not in result


@pytest.mark.asyncio
async def test_annotation_reaches_the_agent_through_the_real_call_api(monkeypatch):
    """Drive `call_api` itself, with only the HTTP layer faked.

    The previous attempt at #596 passed its unit tests while failing 100% of the
    time in production, because the fixtures never exercised the real call path.
    This test asserts the wiring: two calls through `call_api`, the second
    carrying the annotation the agent's code will see.
    """
    import json as _json

    from cuga.backend.cuga_graph.nodes.cuga_lite.providers import registry as provider

    bodies = [
        _json.dumps({"message": "Song added to playlist."}),
        _json.dumps(_already_in_playlist()),
    ]

    class _FakeResponse:
        def __init__(self, body):
            self.status = 200
            self._body = body

        async def text(self):
            return self._body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, *args, **kwargs):
            return _FakeResponse(bodies.pop(0))

    monkeypatch.setattr(provider.aiohttp, "ClientSession", lambda *a, **k: _FakeSession())

    args = {"playlist_id": 654, "song_id": 1}
    await provider.call_api("spotify", ADD_SONG, dict(args))
    result = await provider.call_api("spotify", ADD_SONG, dict(args))

    assert result["status"] == "exception"
    assert result["prior_identical_success"] == {"count": 1, "calls_since": 1}


def test_disabled_by_default(monkeypatch):
    """The flag is off in settings.toml; nothing is recorded or annotated."""
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "annotate_repeat_calls", False, raising=False)
    args = {"playlist_id": 654, "song_id": 1}
    _observe(ADD_SONG, args, {"message": "Song added to playlist."})

    result = _observe(ADD_SONG, args, _already_in_playlist())

    assert "prior_identical_success" not in result
