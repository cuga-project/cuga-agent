"""A conversation continues after the app restarts (#774)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow


async def test_completed_conversation_continues_after_restart_with_messages_and_variables(
    conversation_server,
):
    server = conversation_server
    thread_id = await server.create()
    first = await server.send(thread_id, "first question")
    assert first.status_code == 200, first.text
    assert "Error" not in server.event_names(first)

    await server.restart()

    # Read before any new turn runs: this is what the first process left in the files.
    restored = await server.checkpoint_values(thread_id)
    assert [m.content for m in restored["chat_messages"]] == ["first question", server.answer]
    assert restored["variables_storage"]["variable_1"]["value"] == {"turn": 1}

    second = await server.send(thread_id, "second question")
    assert second.status_code == 200, second.text
    assert "Error" not in server.event_names(second)

    continued = await server.checkpoint_values(thread_id)
    assert [m.content for m in continued["chat_messages"]] == [
        "first question",
        server.answer,
        "second question",
        server.answer,
    ]
    assert continued["variables_storage"]["variable_1"]["value"] == {"turn": 1}
    assert continued["variables_storage"]["variable_2"]["value"] == {"turn": 2}
    assert await server.db.get_thread_status(thread_id, server.user["sub"], server.agent_id) == "active"


async def test_typing_into_a_historical_thread_keeps_its_id_and_sidebar_entry(conversation_server):
    server = conversation_server
    thread_id = await server.create()
    assert (await server.send(thread_id, "first question")).status_code == 200
    before = await server.listing()
    assert [c["id"] for c in before] == [thread_id]
    assert before[0]["status"] == "active"

    await server.restart()

    # The restarted app lists the conversation as it was, and typing into it adds no entry.
    assert [c["id"] for c in await server.listing()] == [thread_id]
    response = await server.send(thread_id, "second question")
    assert response.status_code == 200, response.text
    assert "thread_created" not in server.event_names(response)  # the server did not issue a new ID

    after = await server.listing()
    assert [c["id"] for c in after] == [thread_id]
    assert after[0]["status"] == "active"
    assert len((await server.checkpoint_values(thread_id))["chat_messages"]) == 4
