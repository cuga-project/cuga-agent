"""SpawnAgentRuntime — drives a CugaAgent as an ad-hoc SubCuga subagent."""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from langchain_core.tools import StructuredTool
from loguru import logger

from cuga.config import settings

_spawn_depth: contextvars.ContextVar[int] = contextvars.ContextVar("_spawn_depth", default=0)

# Tools that should never be passed down to subagents (would cause recursion or confusion).
_SPAWN_INTERNAL_TOOL_NAMES: frozenset[str] = frozenset({
    "spawn_agent", "get_agent_result", "load_skill", "find_tools", "create_update_todos",
})

_event_callback: Optional[Callable[[str, dict], None]] = None


def set_event_callback(cb: Optional[Callable[[str, dict], None]]) -> None:
    global _event_callback
    _event_callback = cb


def _emit(event_name: str, data: dict) -> None:
    if _event_callback:
        try:
            _event_callback(event_name, data)
        except Exception:
            pass  # never let event emission crash the agent


# Shared future store: future_id → {"status": "running"|"done"|"error", "result": str|None, "error": str|None}
_spawn_futures: Dict[str, Any] = {}

# Process-level cache keyed by frozenset of tool names.
_agent_cache: Dict[frozenset, Any] = {}


def clear_runtime_caches() -> None:
    """Clear the process-level agent cache (use in tests or after tool changes)."""
    _agent_cache.clear()


class SpawnAgentRuntime:
    def __init__(
        self,
        parent_structured_tools: List[StructuredTool],
        parent_config: Optional[Dict[str, Any]] = None,
        spawn_futures_ref: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._parent_structured_tools = parent_structured_tools
        self._parent_config = parent_config or {}
        self._spawn_futures: Dict[str, Any] = (
            spawn_futures_ref if spawn_futures_ref is not None else _spawn_futures
        )

    @classmethod
    def from_parent(
        cls,
        parent_config: Optional[Dict[str, Any]] = None,
        spawn_futures_ref: Optional[Dict[str, Any]] = None,
        parent_structured_tools: Optional[List[StructuredTool]] = None,
    ) -> "SpawnAgentRuntime":
        """Create a runtime that inherits all parent tools with a fresh context.

        The "fresh eyes" pattern: a skill instructs CUGA to delegate via natural
        language (e.g. "⚠️ USE SUBAGENTS") without requiring a predefined AGENT.md.
        Spawn/skill meta-tools are filtered out to prevent recursive spawning.
        """
        filtered = [
            t for t in (parent_structured_tools or [])
            if t.name not in _SPAWN_INTERNAL_TOOL_NAMES
        ]
        return cls(filtered, parent_config, spawn_futures_ref)

    def _make_thread_id(self) -> str:
        return f"sub_cuga_{uuid4().hex[:8]}"

    def _build_agent(self, tools: List[StructuredTool]):
        import os
        from cuga.sdk import CugaAgent

        no_cache = os.environ.get("CUGA_AGENT_SPAWN_NO_CACHE")
        if not no_cache:
            cache_key = frozenset(t.name for t in tools)
            cached = _agent_cache.get(cache_key)
            if cached is not None:
                return cached

        agent = CugaAgent(tools=tools)

        if not no_cache:
            _agent_cache[cache_key] = agent  # type: ignore[possibly-undefined]
        return agent

    def _build_invoke_config(self) -> dict:
        from cuga.backend.cuga_graph.utils.langfuse_tracing import (
            get_langfuse_invoke_config,
            sync_langfuse_callbacks_from_config,
        )
        if self._parent_config:
            sync_langfuse_callbacks_from_config(self._parent_config)
        return get_langfuse_invoke_config()

    async def _run_stream(self, agent, task: str, thread_id: str, cfg: dict, spawn_id: str = "") -> str:
        final_answer = ""
        forward = getattr(settings.agent_spawn, "forward_sync_subagent_events", True)
        async for chunk in agent.stream(task, thread_id=thread_id, config=cfg):
            state_dict = chunk[1] if isinstance(chunk, tuple) else chunk
            if not isinstance(state_dict, dict):
                continue
            node_data = next(iter(state_dict.values()), None)
            if not isinstance(node_data, dict):
                continue
            if forward and "script" in node_data:
                _emit("CodeAgent", {**node_data, "subagent": "SubCuga", "spawn_id": spawn_id})
            candidate = node_data.get("final_answer")
            if candidate:
                final_answer = candidate
        return final_answer

    async def execute(self, task: str, spawn_id: str = "") -> str:
        """Run the sub-agent synchronously; return its final_answer."""
        depth = _spawn_depth.get()
        max_depth = getattr(settings.agent_spawn, "max_spawn_depth", 2)
        if depth >= max_depth:
            return f"[SpawnError] max_spawn_depth={max_depth} exceeded"

        tools = self._parent_structured_tools
        thread_id = self._make_thread_id()
        invoke_cfg = self._build_invoke_config()

        from cuga.backend.observability.openlit_init import set_session_attribute
        parent_thread_id = self._parent_config.get("configurable", {}).get("thread_id", "")
        set_session_attribute(parent_thread_id)

        _emit("SpawnAgent", {
            "agent_name": "SubCuga",
            "task": task[:200],
            "mode": "async" if spawn_id else "sync",
            "thread_id": thread_id,
            "spawn_id": spawn_id,
        })

        token = _spawn_depth.set(depth + 1)
        try:
            agent = self._build_agent(tools)
            answer = await self._run_stream(agent, task, thread_id, invoke_cfg, spawn_id=spawn_id)
        finally:
            _spawn_depth.reset(token)

        _emit("SpawnAgentResult", {
            "agent_name": "SubCuga",
            "thread_id": thread_id,
            "status": "success",
            "answer": answer[:500],
            "spawn_id": spawn_id,
        })
        return answer

    async def execute_async(self, task: str) -> str:
        """Fire-and-forget spawn; return future_id for get_agent_result."""
        from cuga.backend.observability.openlit_init import set_session_attribute
        parent_thread_id = self._parent_config.get("configurable", {}).get("thread_id", "")
        set_session_attribute(parent_thread_id)

        future_id = f"future_{uuid4().hex[:8]}"
        self._spawn_futures[future_id] = {"status": "running", "result": None, "error": None}
        asyncio.create_task(self._execute_and_store(future_id, task))
        return future_id

    async def _execute_and_store(self, future_id: str, task: str) -> None:
        try:
            result = await self.execute(task, spawn_id=future_id)
            self._spawn_futures[future_id] = {"status": "done", "result": result, "error": None}
        except Exception as e:
            logger.warning(f"agent_spawn: async execute failed for future_id={future_id}: {e}")
            self._spawn_futures[future_id] = {"status": "error", "result": None, "error": str(e)}
