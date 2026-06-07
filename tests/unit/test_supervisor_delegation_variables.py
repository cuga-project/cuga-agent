import pytest

from cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation import (
    resolve_names_from_caller_frame,
)


@pytest.mark.asyncio
async def test_resolve_names_from_caller_frame_finds_async_grandparent_local():
    async def generated_supervisor_code_frame():
        request = {
            "traveler": "John Doe",
            "origin": "New York",
            "destination": "Boston",
            "start_date": "2026-06-22",
            "end_date": "2026-06-26",
            "cabin_preference": "economy",
        }

        async def delegate_to_agent_like_frame():
            return resolve_names_from_caller_frame(["request"])

        assert request["traveler"] == "John Doe"
        return await delegate_to_agent_like_frame()

    resolved = await generated_supervisor_code_frame()

    assert resolved == {
        "request": {
            "traveler": "John Doe",
            "origin": "New York",
            "destination": "Boston",
            "start_date": "2026-06-22",
            "end_date": "2026-06-26",
            "cabin_preference": "economy",
        }
    }


def test_resolve_names_from_caller_frame_finds_global_context_variable():
    request_from_global_context = {"origin": "NYC"}

    def generated_supervisor_code_frame():
        return resolve_names_from_caller_frame(["request_from_global_context"])

    resolved = generated_supervisor_code_frame()

    assert resolved == {"request_from_global_context": request_from_global_context}