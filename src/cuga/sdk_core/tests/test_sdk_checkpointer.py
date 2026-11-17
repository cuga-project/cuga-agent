"""The ``checkpointer=`` argument of ``CugaAgent`` and ``CugaSupervisor`` (#774).

Covers the default saver, a passed saver, rejection of sync-only savers, graph state
read only through the async API (on ``AsyncOnlySaver``), and a thread that survives a
restart: two agents on separate ``AsyncSqliteSaver`` connections to one file share
nothing but that file. ``LANGGRAPH_STRICT_MSGPACK=true`` (root conftest) makes a
state type missing from the serde allowlist fail here instead of loading as a dict.
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime
from typing import Any, List, Optional
from unittest.mock import patch

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.base import BaseCheckpointSaver, empty_checkpoint
from langgraph.checkpoint.memory import MemorySaver

from cuga import CugaAgent, CugaSupervisor

pytestmark = pytest.mark.unit

ANSWER = "There are 3 accounts."
APPROVED_ANSWER = "deleted after approval"
PAUSED = "Execution paused for approval"
# Plain JSON values: a user pydantic model kept in variables_storage would come back
# from the allowlist serde as a dict, by design, so it could not be compared with ==.
VARIABLE_NAME = "customer"
VARIABLE_VALUE = {"id": 42, "name": "Acme", "tags": ["vip", "emea"], "balance": 12.5, "active": True}


class _FixedChatModel(BaseChatModel):
    """Answers every call with the same plain-text message."""

    answer: str = ANSWER

    @property
    def _llm_type(self) -> str:
        return "fixed-fake"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.answer))])


class _SyncOnlySaver(BaseCheckpointSaver):
    """Implements only the sync API, so ``aget_tuple`` is the base-class stub."""

    def get_tuple(self, config):
        return None

    def list(self, config, *, filter=None, before=None, limit=None):
        return iter(())

    def put(self, config, checkpoint, metadata, new_versions):
        return config

    def put_writes(self, config, writes, task_id, task_path=""):
        return None


def _agent(checkpointer=None, model: Optional[BaseChatModel] = None) -> CugaAgent:
    return CugaAgent(
        tools=[],
        model=model or _FixedChatModel(),
        auto_load_policies=False,
        enable_knowledge=False,
        enable_skills=False,
        checkpointer=checkpointer,
    )


def _supervisor(checkpointer=None, helper_checkpointer=None) -> CugaSupervisor:
    return CugaSupervisor(
        agents={"helper": _agent(helper_checkpointer)},
        model=_FixedChatModel(answer="Supervisor answer."),
        auto_load_policies=False,
        checkpointer=checkpointer,
    )


_BUILDERS = [pytest.param(_agent, id="agent"), pytest.param(_supervisor, id="supervisor")]


# -- the checkpointer argument -------------------------------------------------


@pytest.mark.parametrize("build", _BUILDERS)
def test_default_is_a_fresh_memory_saver_per_instance(build):
    first, second = build().graph, build().graph
    assert isinstance(first.checkpointer, MemorySaver)
    assert first.checkpointer is not second.checkpointer


@pytest.mark.parametrize("build", _BUILDERS)
async def test_passed_saver_is_used(build, tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
        graph_saver = build(checkpointer=saver).graph.checkpointer
        # Under strict msgpack, compile() swaps in saver.with_allowlist(...), a shallow
        # clone, so prove the graph writes to the passed saver's store instead of identity.
        assert type(graph_saver) is type(saver)
        config = {"configurable": {"thread_id": "sdk-sqlite", "checkpoint_ns": ""}}
        written = await graph_saver.aput(
            config, empty_checkpoint(), {"source": "input", "step": -1, "parents": {}}, {}
        )
        stored = await saver.aget_tuple(written)
        assert stored is not None
        assert stored.checkpoint["id"] == written["configurable"]["checkpoint_id"]


@pytest.mark.parametrize("build", _BUILDERS)
def test_sync_only_saver_is_rejected_on_first_compile(build):
    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        # SqliteSaver overrides the async methods only to raise NotImplementedError.
        for saver in (_SyncOnlySaver(), SqliteSaver(conn)):
            with pytest.raises(TypeError, match="async-capable checkpointer"):
                build(checkpointer=saver).graph
    finally:
        conn.close()


# -- supervisor state reads go through the async API ----------------------------
# invoke() wraps its state reads in ``except Exception``, so a sync read would not fail
# the call: it would silently drop the history or the pause notice. Each test checks
# both ``sync_calls == []`` and the result that depends on the state read.


async def test_supervisor_invoke_without_sync_calls(async_only_saver_cls):
    saver = async_only_saver_cls()
    supervisor = _supervisor(saver, async_only_saver_cls())

    result = await supervisor.invoke("hello", thread_id="t-async-supervisor")

    assert saver.sync_calls == []
    assert result.answer
    assert PAUSED not in result.answer


async def test_supervisor_pause_and_resume_without_sync_calls(async_only_saver_cls):
    from langgraph.graph import END, START, StateGraph

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState

    # A parked graph on the supervisor's own state type: invoke() returns no answer,
    # so the pause notice depends on the aget_state read.
    def produce(_state: CugaSupervisorState) -> dict:
        return {"final_answer": ""}

    def wait_for_response(_state: CugaSupervisorState) -> dict:
        return {"final_answer": "resumed"}

    builder = StateGraph(CugaSupervisorState)
    builder.add_node("Produce", produce)
    builder.add_node("WaitForResponse", wait_for_response)
    builder.add_edge(START, "Produce")
    builder.add_edge("Produce", "WaitForResponse")
    builder.add_edge("WaitForResponse", END)

    saver = async_only_saver_cls()
    supervisor = _supervisor(saver, async_only_saver_cls())
    supervisor._compiled_graph = builder.compile(checkpointer=saver, interrupt_before=["WaitForResponse"])

    paused = await supervisor.invoke("work", thread_id="t-async-supervisor-hitl")
    resumed = await supervisor.invoke(None, thread_id="t-async-supervisor-hitl")

    assert saver.sync_calls == []
    assert PAUSED in paused.answer
    assert resumed.answer == "resumed"


# -- a thread survives a restart on a durable saver -----------------------------


def _approval_subgraph(**_kwargs):
    """Stand-in CugaLite subgraph: asks for tool approval, then answers once approved.

    The HITL wrapper graph around it (SDKCallback -> SuggestHumanActions ->
    WaitForResponse) is the real one.
    """
    from langgraph.graph import END, START, StateGraph

    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import create_tool_approval_action
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    async def run(state: AgentState) -> dict:
        metadata = dict(state.cuga_lite_metadata or {})
        if metadata.get("user_approved"):
            return {"final_answer": APPROVED_ANSWER, "hitl_action": None}
        metadata.update(policy_name="Approve deletes", required_tools=["delete_database"])
        return {
            "hitl_action": create_tool_approval_action(
                policy_name="Approve deletes",
                required_tools=["delete_database"],
                code_preview=["delete_database('users')"],
                full_code="delete_database('users')",
            ),
            "cuga_lite_metadata": metadata,
        }

    graph = StateGraph(AgentState)
    graph.add_node("Run", run)
    graph.add_edge(START, "Run")
    graph.add_edge("Run", END)
    return graph


@asynccontextmanager
async def _restarted_agent(db_path, *, hitl: bool = False):
    """A new ``CugaAgent`` on a new ``AsyncSqliteSaver`` connection to ``db_path``."""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from cuga.backend.storage.checkpoint.serde import build_checkpoint_serde

    # The graph is compiled on first use of agent.graph, so build it inside the patch.
    stand_in = (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph.create_cuga_lite_graph",
            _approval_subgraph,
        )
        if hitl
        else nullcontext()
    )
    async with aiosqlite.connect(db_path) as conn:
        saver = AsyncSqliteSaver(conn, serde=build_checkpoint_serde())
        with stand_in:
            agent = _agent(saver)
            assert isinstance(agent.graph.checkpointer, AsyncSqliteSaver)
        yield agent


async def _snapshot(agent, thread_id: str) -> dict:
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge

    snapshot = await agent.graph.aget_state({"configurable": {"thread_id": thread_id}})
    values = snapshot.values or {}
    return {
        # Stored as message objects or as their model_dump() dicts, depending on the update.
        "chat_messages": [
            str(m["content"] if isinstance(m, dict) else m.content) for m in values.get("chat_messages") or []
        ],
        "variables": VariableBridge.extract_values(values.get("variables_storage") or {}),
        "next": list(snapshot.next),
        "hitl_action": values.get("hitl_action") is not None,
    }


async def test_restarted_agent_continues_the_thread_with_messages_and_variables(tmp_path):
    db = tmp_path / "cp.db"
    async with _restarted_agent(db) as agent:
        first = await agent.invoke("first question", thread_id="t", variables={VARIABLE_NAME: VARIABLE_VALUE})
        assert first.answer == ANSWER

    async with _restarted_agent(db) as agent:
        before = await _snapshot(agent, "t")
        assert before["chat_messages"] == ["first question", ANSWER]
        assert before["variables"] == {VARIABLE_NAME: VARIABLE_VALUE}

        second = await agent.invoke("second question", thread_id="t")
        after = await _snapshot(agent, "t")

    assert second.answer == ANSWER
    assert second.variables == {VARIABLE_NAME: VARIABLE_VALUE}
    assert after["chat_messages"] == ["first question", ANSWER, "second question", ANSWER]
    assert after["next"] == []


async def test_restarted_agent_resumes_a_hitl_pause(tmp_path):
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse, ActionType

    db = tmp_path / "cp.db"
    async with _restarted_agent(db, hitl=True) as agent:
        paused = await agent.invoke("delete the users table", thread_id="h")
        assert PAUSED in paused.answer

    async with _restarted_agent(db, hitl=True) as agent:
        before = await _snapshot(agent, "h")
        assert before["next"] == ["WaitForResponse"]
        assert before["hitl_action"] is True

        approval = ActionResponse(
            action_id="tool_approval",
            response_type=ActionType.CONFIRMATION,
            confirmed=True,
            timestamp=datetime.now().isoformat(),
        )
        resumed = await agent.invoke(None, thread_id="h", action_response=approval)
        after = await _snapshot(agent, "h")

    assert resumed.answer == APPROVED_ANSWER
    assert after["next"] == []
    assert after["hitl_action"] is False
