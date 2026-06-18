"""Per-thread state count validation for concurrent load tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThreadStateExpectations:
    variables_count: int
    chat_messages_count: int | None = None


@dataclass(frozen=True)
class ThreadStateSnapshot:
    user_id: int
    thread_id: str
    variables_count: int
    chat_messages_count: int


def extract_thread_state_snapshot(
    user_id: int,
    thread_id: str,
    state_response: dict,
    *,
    get_variables_count,
    get_chat_messages_count,
) -> ThreadStateSnapshot:
    return ThreadStateSnapshot(
        user_id=user_id,
        thread_id=thread_id,
        variables_count=get_variables_count(state_response),
        chat_messages_count=get_chat_messages_count(state_response),
    )


def validate_thread_state_counts(
    state_response: dict,
    *,
    user_id: int,
    thread_id: str,
    expectations: ThreadStateExpectations,
    check_chat_messages: bool,
    get_variables_count,
    get_chat_messages_count,
) -> tuple[bool, str]:
    if state_response.get("thread_id") != thread_id:
        return (
            False,
            f"User {user_id}: state thread_id mismatch "
            f"(expected {thread_id}, got {state_response.get('thread_id')})",
        )

    variables_count = get_variables_count(state_response)
    if variables_count != expectations.variables_count:
        return (
            False,
            f"User {user_id}: expected {expectations.variables_count} variables, "
            f"found {variables_count} (possible state overload or leak)",
        )

    if check_chat_messages and expectations.chat_messages_count is not None:
        chat_messages_count = get_chat_messages_count(state_response)
        if chat_messages_count != expectations.chat_messages_count:
            return (
                False,
                f"User {user_id}: expected {expectations.chat_messages_count} chat_messages, "
                f"found {chat_messages_count} (possible message history overload or leak)",
            )

    return True, ""


def validate_threads_state_uniformity(
    snapshots: list[ThreadStateSnapshot],
    *,
    expectations: ThreadStateExpectations,
    check_chat_messages: bool,
) -> tuple[bool, str, str]:
    """Validate every thread matches expected counts and all threads match each other."""
    if not snapshots:
        return False, "No thread state snapshots to validate", ""

    lines = [
        "Post-run per-thread state counts "
        f"(expected: {expectations.variables_count} variables"
        + (
            f", {expectations.chat_messages_count} chat_messages"
            if check_chat_messages and expectations.chat_messages_count is not None
            else ""
        )
        + "):"
    ]

    reference_vars = expectations.variables_count
    reference_msgs = expectations.chat_messages_count

    for snapshot in sorted(snapshots, key=lambda s: s.user_id):
        if snapshot.variables_count != reference_vars:
            return (
                False,
                f"User {snapshot.user_id}: variable count {snapshot.variables_count} != "
                f"expected {reference_vars}",
                "",
            )

        if check_chat_messages and reference_msgs is not None:
            if snapshot.chat_messages_count != reference_msgs:
                return (
                    False,
                    f"User {snapshot.user_id}: chat_messages count {snapshot.chat_messages_count} != "
                    f"expected {reference_msgs}",
                    "",
                )

        lines.append(
            f"  user {snapshot.user_id:2d}  vars={snapshot.variables_count}  "
            f"chat_messages={snapshot.chat_messages_count}  thread={snapshot.thread_id[:8]}..."
        )

    variable_counts = {s.variables_count for s in snapshots}
    if len(variable_counts) != 1:
        return (
            False,
            f"Variable count mismatch across threads: {sorted(variable_counts)}",
            "",
        )

    if check_chat_messages:
        message_counts = {s.chat_messages_count for s in snapshots}
        if len(message_counts) != 1:
            return (
                False,
                f"chat_messages count mismatch across threads: {sorted(message_counts)}",
                "",
            )

    lines.append("All threads have uniform state counts.")
    return True, "", "\n".join(lines)
