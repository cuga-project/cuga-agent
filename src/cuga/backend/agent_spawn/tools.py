"""StructuredTool definitions for spawn_agent and get_agent_result."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry


class SpawnAgentInput(BaseModel):
    name: Optional[str] = Field(
        None,
        description=(
            "Named agent from <available_agents>. Omit (or pass null) to spawn an ad-hoc "
            "subagent that inherits all your tools with a fresh context and no prior conversation."
        ),
    )
    task: str = Field(
        ...,
        description=(
            "Full task description for the subagent. Include all context it needs — "
            "the subagent has no memory of the current conversation."
        ),
        max_length=4000,
    )
    mode: str = Field(default="sync", description="'sync' waits for result; 'async' returns a future_id")


class GetAgentResultInput(BaseModel):
    future_id: str = Field(..., description="future_id from a previous async spawn_agent call")
    timeout: float = Field(default=60.0, description="Seconds to wait for result")


def create_spawn_tools(
    registry: AgentDescriptorRegistry,
    parent_tools_context: Dict[str, Any],
    spawn_futures: Dict[str, Any],
    parent_config: Optional[Dict[str, Any]] = None,
    parent_structured_tools: Optional[List[StructuredTool]] = None,
) -> list[StructuredTool]:
    """Factory: returns [spawn_agent_tool, get_agent_result_tool]."""

    async def spawn_agent(name: Optional[str] = None, task: str = "", mode: str = "sync") -> str:
        from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime

        if name:
            entry = registry.get(name)
            if entry is None:
                known = ", ".join(e.name for e in registry.all()) or "(none — use ad-hoc spawning by omitting name)"
                return f"Unknown agent: {name!r}. Known agents: {known}"
            rt = SpawnAgentRuntime(entry, parent_tools_context, parent_config, spawn_futures_ref=spawn_futures)
        else:
            rt = SpawnAgentRuntime.adhoc(
                parent_tools_context,
                parent_config,
                spawn_futures_ref=spawn_futures,
                parent_structured_tools=parent_structured_tools,
            )

        if mode == "async":
            future_id = await rt.execute_async(task)
            return future_id
        return await rt.execute(task)

    async def get_agent_result(future_id: str, timeout: float = 60.0) -> str:
        if future_id not in spawn_futures:
            return f"Unknown future_id: {future_id!r}"
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            entry = spawn_futures[future_id]
            if entry["status"] == "done":
                return entry.get("result") or ""
            if entry["status"] == "error":
                return f"[SpawnError] {entry.get('error', 'unknown error')}"
            await asyncio.sleep(0.5)
        return f"[SpawnTimeout] Agent {future_id!r} did not complete within {timeout}s"

    spawn_tool = StructuredTool.from_function(
        coroutine=spawn_agent,
        name="spawn_agent",
        description=(
            "Spawn a subagent with fresh context to handle a task independently. "
            "Omit 'name' for an ad-hoc subagent that inherits all your tools. "
            "Pass 'name' to use a named agent from <available_agents>. "
            "mode='sync' waits for the result; mode='async' returns a future_id."
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
