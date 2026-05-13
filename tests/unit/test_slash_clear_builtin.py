import asyncio
import uuid

from cuga.backend.slash_commands import (
    build_slash_registry,
    parse_and_dispatch,
)


def test_clear_is_registered_as_builtin():
    reg = build_slash_registry()
    names = {c.name for c in reg.list_commands()}
    assert "clear" in names


def test_clear_returns_new_thread_id():
    reg = build_slash_registry()
    result = asyncio.run(
        parse_and_dispatch(
            "/clear", slash_registry=reg, thread_id="thread-a"
        )
    )
    assert result.kind == "builtin"
    assert result.new_thread_id
    uuid.UUID(result.new_thread_id)  # raises if not a valid UUID
    assert result.new_thread_id != "thread-a"
    assert "fresh conversation" in (result.text or "").lower()


def test_clear_without_thread_id_still_mints_one():
    reg = build_slash_registry()
    result = asyncio.run(
        parse_and_dispatch("/clear", slash_registry=reg, thread_id=None)
    )
    assert result.kind == "builtin"
    assert result.new_thread_id


def test_clear_calls_stop_event_hook_when_provided():
    reg = build_slash_registry()
    cleared: list[str] = []

    def clear_hook(tid: str) -> None:
        cleared.append(tid)

    result = asyncio.run(
        parse_and_dispatch(
            "/clear",
            slash_registry=reg,
            thread_id="thread-b",
            clear_stop_event=clear_hook,
        )
    )
    assert result.kind == "builtin"
    assert cleared == ["thread-b"]


def test_clear_hook_failure_does_not_abort_dispatch():
    reg = build_slash_registry()

    def clear_hook(tid: str) -> None:
        raise RuntimeError("simulated stop-event failure")

    result = asyncio.run(
        parse_and_dispatch(
            "/clear",
            slash_registry=reg,
            thread_id="thread-c",
            clear_stop_event=clear_hook,
        )
    )
    assert result.kind == "builtin"
    assert result.new_thread_id
