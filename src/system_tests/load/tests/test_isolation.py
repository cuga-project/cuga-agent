"""Unit tests for load test thread state isolation helpers."""

from system_tests.load.isolation import (
    ThreadStateExpectations,
    ThreadStateSnapshot,
    validate_thread_state_counts,
    validate_threads_state_uniformity,
)


def _counts(state_response: dict) -> int:
    return int(state_response.get("variables_count") or 0)


def _messages(state_response: dict) -> int:
    state = state_response.get("state") or {}
    return int(state.get("chat_messages_count") or 0)


def test_validate_thread_state_counts_accepts_expected_snapshot():
    ok, error = validate_thread_state_counts(
        {
            "thread_id": "abc",
            "variables_count": 3,
            "state": {"chat_messages_count": 4},
        },
        user_id=0,
        thread_id="abc",
        expectations=ThreadStateExpectations(variables_count=3, chat_messages_count=4),
        check_chat_messages=True,
        get_variables_count=_counts,
        get_chat_messages_count=_messages,
    )
    assert ok is True
    assert error == ""


def test_validate_thread_state_counts_rejects_overloaded_variables():
    ok, error = validate_thread_state_counts(
        {
            "thread_id": "abc",
            "variables_count": 6,
            "state": {"chat_messages_count": 4},
        },
        user_id=1,
        thread_id="abc",
        expectations=ThreadStateExpectations(variables_count=3, chat_messages_count=4),
        check_chat_messages=True,
        get_variables_count=_counts,
        get_chat_messages_count=_messages,
    )
    assert ok is False
    assert "6 variables" in error or "found 6" in error


def test_validate_threads_state_uniformity_requires_matching_counts():
    snapshots = [
        ThreadStateSnapshot(0, "a", 3, 6),
        ThreadStateSnapshot(1, "b", 3, 6),
    ]
    ok, error, report = validate_threads_state_uniformity(
        snapshots,
        expectations=ThreadStateExpectations(variables_count=3, chat_messages_count=6),
        check_chat_messages=True,
    )
    assert ok is True
    assert "uniform" in report


def test_validate_threads_state_uniformity_detects_message_overload():
    snapshots = [
        ThreadStateSnapshot(0, "a", 3, 6),
        ThreadStateSnapshot(1, "b", 3, 8),
    ]
    ok, error, _ = validate_threads_state_uniformity(
        snapshots,
        expectations=ThreadStateExpectations(variables_count=3, chat_messages_count=6),
        check_chat_messages=True,
    )
    assert ok is False
    assert "chat_messages" in error
