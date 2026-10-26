"""The SDK graph can resume from DAM's persistent ACP checkpoint database."""

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.graph import END, StateGraph

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


class _State(TypedDict):
    count: int


def _graph() -> StateGraph:
    graph = StateGraph(_State)
    graph.add_node("advance", lambda state: {"count": state["count"] + 1})
    graph.add_node("WaitForResponse", lambda state: state)
    graph.set_entry_point("advance")
    graph.add_edge("advance", "WaitForResponse")
    graph.add_edge("WaitForResponse", END)
    return graph


@pytest.mark.unit
async def test_sdk_reopens_checkpoint_after_process_restart(tmp_path) -> None:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from cuga.sdk import CugaAgent

    path = str(tmp_path / "checkpoints.sqlite3")
    config = {"configurable": {"thread_id": "dam-session-context"}}

    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        first = CugaAgent(
            model=object(),
            tools=[],
            checkpointer=saver,
            cuga_folder=str(tmp_path / "cuga"),
            auto_load_policies=False,
        )
        first._create_graph = _graph
        await first.graph.ainvoke({"count": 3}, config)
        assert (await first.graph.aget_state(config)).values["count"] == 4

    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        resumed = CugaAgent(
            model=object(),
            tools=[],
            checkpointer=saver,
            cuga_folder=str(tmp_path / "cuga"),
            auto_load_policies=False,
        )
        resumed._create_graph = _graph
        state = await resumed.graph.aget_state(config)
        assert state.values["count"] == 4
        assert state.next == ("WaitForResponse",)
