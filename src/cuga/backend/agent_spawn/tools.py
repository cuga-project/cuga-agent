"""StructuredTool definitions for spawn_agent and get_agent_result."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Literal, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime, cancel_spawn_future, pop_spawn_future


class SpawnAgentInput(BaseModel):
    task: str = Field(
        ...,
        description=(
            "Full task description for the subagent. Include all context it needs — "
            "the subagent has no memory of the current conversation."
        ),
        max_length=4000,
    )
    mode: Literal["sync", "async"] = Field(
        default="sync", description="'sync' waits for result; 'async' returns a future_id"
    )
    timeout: float = Field(
        default=300.0,
        description="Seconds to wait for sync-mode completion (ignored for async)",
        gt=0,
    )
    share_workspace: bool = Field(
        default=False,
        description=(
            "If True, parent and child share one workspace both ways: the child can read "
            "existing session uploads/files, and files it creates (e.g. .md reports) appear "
            "in the parent session workspace. Default False = isolated empty workspace "
            "(safer for parallel async spawns)."
        ),
    )


class GetAgentResultInput(BaseModel):
    future_id: str = Field(..., description="future_id from a previous async spawn_agent call")
    timeout: float = Field(default=60.0, description="Seconds to wait for result")


def create_spawn_tools(
    spawn_futures: Dict[str, Any],
    parent_config: Optional[Dict[str, Any]] = None,
    parent_structured_tools: Optional[List[StructuredTool]] = None,
) -> list[StructuredTool]:
    """Factory: returns [spawn_agent_tool, get_agent_result_tool]."""

    parent_thread_id = (parent_config or {}).get("configurable", {}).get("thread_id", "") or ""

    async def spawn_agent(
        task: str = "",
        mode: Literal["sync", "async"] = "sync",
        share_workspace: bool = False,
        timeout: float = 300.0,
    ) -> str:
        rt = SpawnAgentRuntime.from_parent(
            parent_config,
            spawn_futures_ref=spawn_futures,
            parent_structured_tools=parent_structured_tools,
        )

        if mode == "async":
            return await rt.execute_async(task, share_workspace=share_workspace)
        try:
            return await asyncio.wait_for(
                rt.execute(task, share_workspace=share_workspace),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return f"[SpawnTimeout] sync spawn_agent did not complete within {timeout}s"

    async def get_agent_result(future_id: str, timeout: float = 60.0) -> str:
        if future_id not in spawn_futures:
            return f"Unknown future_id: {future_id!r}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            entry = spawn_futures[future_id]
            if entry["status"] == "done":
                result = entry.get("result") or ""
                pop_spawn_future(parent_thread_id, future_id)
                spawn_futures.pop(future_id, None)
                return result
            if entry["status"] == "error":
                err = f"[SpawnError] {entry.get('error', 'unknown error')}"
                pop_spawn_future(parent_thread_id, future_id)
                spawn_futures.pop(future_id, None)
                return err
            if entry["status"] in ("timeout", "cancelled"):
                err = f"[SpawnError] {entry.get('error', entry['status'])}"
                pop_spawn_future(parent_thread_id, future_id)
                spawn_futures.pop(future_id, None)
                return err
            await asyncio.sleep(0.5)
        cancel_spawn_future(parent_thread_id, future_id)
        pop_spawn_future(parent_thread_id, future_id)
        spawn_futures.pop(future_id, None)
        return f"[SpawnTimeout] Agent {future_id!r} did not complete within {timeout}s"

    spawn_tool = StructuredTool.from_function(
        coroutine=spawn_agent,
        name="spawn_agent",
        description=(
            "Spawn a SubCuga subagent with fresh context to handle a task independently. "
            "The subagent inherits all your tools and runs without any prior conversation history. "
            "Pass the complete task description — everything the subagent needs to succeed. "
            "When including prior results in task, build it with string concat + json.dumps "
            "(never nested f-strings/triple quotes around those values). "
            "mode='sync' (default): blocks until the subagent finishes — use only for a single sequential subtask. "
            "mode='async': returns a future_id immediately so you can spawn multiple subagents in parallel before "
            "collecting results with get_agent_result. Use mode='async' whenever you have two or more independent "
            "subtasks that could run simultaneously. "
            "share_workspace=False (default): isolated empty workspace. "
            "share_workspace=True: same workspace both ways — child reads parent uploads/files and "
            "anything the child writes (reports, .md, outputs) is visible in the parent session "
            "(avoid for parallel async writers on the same files)."
        ),
        args_schema=SpawnAgentInput,
    )
    result_tool = StructuredTool.from_function(
        coroutine=get_agent_result,
        name="get_agent_result",
        description="Wait for and retrieve the result of an async spawn_agent call.",
        args_schema=GetAgentResultInput,
    )
    return [spawn_tool, result_tool]
