"""Child process for ``test_sdk_durable_checkpointer.py`` (#774). Not a test module.

Run as ``python _durable_checkpointer_child.py <step> <db_path> <thread_id>``. Each
step opens an ``AsyncSqliteSaver`` on ``db_path``, builds a fresh ``CugaAgent`` on a
fake chat model, does one thing on the thread and prints one ``RESULT_JSON: {...}``
line. Every run is a new interpreter, so whatever a step reads from the thread came
from the SQLite file, not from memory.

The file is run by path, not imported: the ``sdk`` CI job collects
``src/cuga/sdk_core/tests`` with ``--import-mode=importlib`` and the directory is not
a package, so the fake model and the HITL stand-in are defined here, not in the test.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from typing import Any, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

RESULT_PREFIX = "RESULT_JSON: "
ANSWER = "There are 3 accounts."
APPROVED_ANSWER = "deleted after approval"
# Plain JSON values on purpose: the default (allowlist) serde would load a user
# pydantic model stored here back as a dict, by design.
VARIABLE_NAME = "customer"
VARIABLE_VALUE = {"id": 42, "name": "Acme", "tags": ["vip", "emea"], "balance": 12.5, "active": True}

fake_llm_calls = 0


class _FixedChatModel(BaseChatModel):
    """Answers every call with the same plain-text message and counts the calls."""

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
        global fake_llm_calls
        fake_llm_calls += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.answer))])


def _approval_subgraph(**_kwargs):
    """Stand-in CugaLite subgraph: asks for tool approval, then answers once approved.

    Same stand-in as ``test_sdk_async_state.py``; the HITL wrapper graph around it
    (SDKCallback -> SuggestHumanActions -> WaitForResponse) is the real one.
    """
    from langgraph.graph import END, START, StateGraph

    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
        create_tool_approval_action,
    )
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


def _approval():
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse, ActionType

    return ActionResponse(
        action_id="tool_approval",
        response_type=ActionType.CONFIRMATION,
        confirmed=True,
        timestamp=datetime.now().isoformat(),
    )


def _content(message: Any) -> str:
    # Stored as message objects or as their model_dump() dicts, depending on the update.
    return str(message["content"] if isinstance(message, dict) else message.content)


async def _snapshot(agent, thread_id: str) -> dict:
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge

    snapshot = await agent.graph.aget_state({"configurable": {"thread_id": thread_id}})
    values = snapshot.values or {}
    return {
        "chat_messages": [_content(m) for m in values.get("chat_messages") or []],
        "variables": VariableBridge.extract_values(values.get("variables_storage") or {}),
        "next": list(snapshot.next),
        "interrupts": len(snapshot.interrupts),
        "hitl_action": values.get("hitl_action") is not None,
    }


async def _run(step: str, db_path: str, thread_arg: str) -> dict:
    from contextlib import nullcontext
    from unittest.mock import patch

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from cuga import CugaAgent
    from cuga.backend.storage.checkpoint.serde import build_checkpoint_serde

    out: dict = {"step": step}
    hitl = step.startswith("hitl_")
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
            agent = CugaAgent(
                tools=[],
                model=_FixedChatModel(),
                auto_load_policies=False,
                enable_knowledge=False,
                enable_skills=False,
                checkpointer=saver,
            )
            out["saver"] = type(agent.graph.checkpointer).__name__

            if step == "turn1":
                thread_id = thread_arg
                result = await agent.invoke(
                    "first question", thread_id=thread_id, variables={VARIABLE_NAME: VARIABLE_VALUE}
                )
                out["answer"] = result.answer
                out["after"] = await _snapshot(agent, thread_id)

            elif step == "turn2":
                thread_id = thread_arg
                out["before"] = await _snapshot(agent, thread_id)
                result = await agent.invoke("second question", thread_id=thread_id)
                out["answer"] = result.answer
                out["result_variables"] = result.variables
                out["after"] = await _snapshot(agent, thread_id)

            elif step == "hitl_pause":
                thread_id = thread_arg
                result = await agent.invoke("delete the users table", thread_id=thread_id)
                out["answer"] = result.answer
                out["after"] = await _snapshot(agent, thread_id)

            elif step == "hitl_resume":
                thread_id = thread_arg
                out["before"] = await _snapshot(agent, thread_id)
                result = await agent.invoke(None, thread_id=thread_id, action_response=_approval())
                out["answer"] = result.answer
                out["after"] = await _snapshot(agent, thread_id)

            else:
                raise SystemExit(f"unknown step {step!r}")

    out["fake_llm_calls"] = fake_llm_calls
    return out


def main(argv: List[str]) -> None:
    step, db_path, thread_id = argv[1:4]
    out = asyncio.run(_run(step, db_path, thread_id))
    print(RESULT_PREFIX + json.dumps(out, default=str), flush=True)


if __name__ == "__main__":
    main(sys.argv)
