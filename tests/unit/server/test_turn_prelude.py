"""Fail-closed checkpoint check before a turn (#774, design §6.2 step 4, §10)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cuga.backend.server.turn_prelude import ThreadUnavailable, check_turn_state

pytestmark = pytest.mark.unit

_PRESENT = SimpleNamespace(values={"input": "hi"}, next=())
_PAUSED = SimpleNamespace(values={}, next=("WaitForResponse",))
_ABSENT = SimpleNamespace(values={}, next=())
_INCOMPATIBLE = SimpleNamespace(values={"chat_messages": "not a list"}, next=())


@pytest.mark.parametrize(
    ("status", "snapshot", "kind", "expected"),
    [
        ("active", _PRESENT, "sqlite", None),
        ("new", _PRESENT, "sqlite", None),  # a checkpoint always wins over the status
        ("active", _PAUSED, "postgres", None),
        ("new", _ABSENT, "sqlite", None),  # a new conversation starts empty
        ("active", _ABSENT, "sqlite", "missing"),  # never silently starts empty
        ("active", None, "postgres", "missing"),
        ("active", _ABSENT, "memory", None),  # in-memory: a restart loses state by design
        ("active", RuntimeError("db down"), "sqlite", "unreadable"),
        ("active", RuntimeError("db down"), "memory", "unreadable"),
        ("active", _INCOMPATIBLE, "sqlite", "incompatible"),
    ],
)
async def test_checkpoint_rules(status, snapshot, kind, expected):
    graph = SimpleNamespace(
        aget_state=AsyncMock(side_effect=snapshot)
        if isinstance(snapshot, Exception)
        else AsyncMock(return_value=snapshot)
    )
    if expected is None:
        await check_turn_state(graph, "key", status, kind)
        return
    with pytest.raises(ThreadUnavailable) as exc:
        await check_turn_state(graph, "key", status, kind)
    assert exc.value.reason == expected


async def test_stream_returns_409_unavailable_without_running_the_turn(server):
    thread_id = await server.create()
    await server.send(thread_id)
    await server.saver.adelete_thread(server.key(thread_id))  # e.g. the checkpoint DB was lost

    response = await server.send(thread_id, "again")

    assert response.status_code == 409
    assert response.json() == {"error": "unavailable", "reason": "missing"}
    assert server.turns == ["hello"]
    assert (await server.send(thread_id)).status_code == 409  # and the lease was released
