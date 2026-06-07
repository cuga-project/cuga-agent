"""SpawnAgentRuntime — instantiates and drives a CugaAgent for a descriptor."""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from langchain_core.tools import StructuredTool
from loguru import logger

from cuga.backend.agent_spawn.registry import AgentDescriptorEntry
from cuga.backend.agent_spawn.tool_builder import (
    ToolDefinitionError,
    build_tool_from_definition,
    build_tools_from_skill_tool_definitions,
)
from cuga.config import settings

_spawn_depth: contextvars.ContextVar[int] = contextvars.ContextVar("_spawn_depth", default=0)

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

# Process-level caches — keyed by descriptor name.
# _agent_cache: compiled CugaAgent instances, keyed by (name, model, frozenset(tool_names)).
# _static_tools_cache: assembled skill+definition StructuredTools, keyed by descriptor name.
# Parent tools are always resolved fresh because they come from the mutable _parent_tools_context.
_agent_cache: Dict[tuple, Any] = {}
_static_tools_cache: Dict[str, List[Any]] = {}


def clear_runtime_caches() -> None:
    """Clear process-level spawn caches (use in tests or after descriptor changes)."""
    _agent_cache.clear()
    _static_tools_cache.clear()


async def prewarm_agent_for_entry(
    entry: AgentDescriptorEntry,
    parent_tools_context: Dict[str, Any],
    parent_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Pre-build and pre-compile a CugaAgent for a descriptor in the background.

    Fired as a background task from prepare_node so graph compilation runs
    concurrently with the parent LLM call. By the time spawn_agent is invoked,
    the compiled graph is already in _agent_cache — eliminating the compilation
    delay on the first spawn.
    """
    import os
    if os.environ.get("CUGA_AGENT_SPAWN_NO_CACHE"):
        return
    try:
        rt = SpawnAgentRuntime(entry, parent_tools_context, parent_config)
        tools = rt._assemble_tools()
        cache_key = (entry.name, str(entry.model), frozenset(t.name for t in tools))
        cached = _agent_cache.get(cache_key)
        if cached is not None and getattr(cached, "_compiled_graph", None) is not None:
            return  # already fully warm
        agent = rt._build_agent(tools)  # populates _agent_cache on miss
        # Force graph compilation off the event loop so it doesn't block LLM I/O.
        if getattr(agent, "_compiled_graph", None) is None:
            await asyncio.to_thread(lambda: agent.graph)  # type: ignore[attr-defined]
    except Exception as e:
        logger.debug(f"agent_spawn: prewarm skipped for {entry.name!r}: {e}")


class SpawnAgentRuntime:
    def __init__(
        self,
        entry: AgentDescriptorEntry,
        parent_tools_context: Dict[str, Any],
        parent_config: Optional[Dict[str, Any]] = None,
        spawn_futures_ref: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._entry = entry
        self._parent_tools_context = parent_tools_context
        self._parent_config = parent_config or {}
        # Use the caller-provided futures dict (same object as in create_spawn_tools closure).
        # Falls back to the module-level dict when instantiated outside the tools factory.
        self._spawn_futures: Dict[str, Any] = spawn_futures_ref if spawn_futures_ref is not None else _spawn_futures

    def _resolve_parent_tools(self) -> List[StructuredTool]:
        """Resolve parent-context tool names to StructuredTool instances.

        Re-wraps functions from parent context at spawn time — avoids a parallel
        _tools_for_spawn dict and keeps the tool list always current.
        """
        out: list[StructuredTool] = []
        for name in self._entry.tools:
            fn = self._parent_tools_context.get(name)
            if fn is None:
                logger.warning(f"agent_spawn: parent tool {name!r} not found in tools_context, skipping")
                continue
            try:
                if asyncio.iscoroutinefunction(fn):
                    tool = StructuredTool.from_function(coroutine=fn, name=name, description="")
                else:
                    tool = StructuredTool.from_function(func=fn, name=name, description="")
                out.append(tool)
            except Exception as e:
                logger.warning(f"agent_spawn: could not wrap parent tool {name!r}: {e}")
        return out

    def _build_skill_tools(self) -> List[StructuredTool]:
        """Load skill_tools entries from SKILL.md tools: blocks."""
        from cuga.backend.skills.loader import discover_skills

        if not self._entry.skill_tools:
            return []

        skill_entries = discover_skills(None)
        by_name = {e.name: e for e in skill_entries}
        out: list[StructuredTool] = []
        for skill_name in self._entry.skill_tools:
            entry = by_name.get(skill_name)
            if entry is None:
                logger.warning(f"agent_spawn: skill_tool {skill_name!r} not found, skipping")
                continue
            out.extend(build_tools_from_skill_tool_definitions(entry))
        return out

    def _build_definition_tools(self) -> List[StructuredTool]:
        """Build StructuredTools from the agent descriptor's tool_definitions."""
        out: list[StructuredTool] = []
        for defn in self._entry.tool_definitions:
            out.append(build_tool_from_definition(defn))
        return out

    def _build_static_tools(self) -> List[StructuredTool]:
        """Build and cache skill + definition tools for this descriptor.

        These tools are deterministic for a given descriptor (no parent context
        dependency), so they are cached for the process lifetime by descriptor name.
        Parent tools are always resolved fresh in _resolve_parent_tools().
        """
        import os
        if os.environ.get("CUGA_AGENT_SPAWN_NO_CACHE"):
            return self._build_skill_tools() + self._build_definition_tools()
        cached = _static_tools_cache.get(self._entry.name)
        if cached is not None:
            return cached
        result = self._build_skill_tools() + self._build_definition_tools()
        _static_tools_cache[self._entry.name] = result
        return result

    def _assemble_tools(self) -> List[StructuredTool]:
        """Merge parent + skill + definition tools; definition tools win on name collision."""
        base = self._resolve_parent_tools() if self._entry.inherit_parent_tools else []
        static = self._build_static_tools()
        by_name: dict[str, StructuredTool] = {t.name: t for t in base}
        for t in static:
            by_name[t.name] = t
        return list(by_name.values())

    def _make_thread_id(self) -> str:
        """Unique thread_id per spawn: {prefix}_{uuid4().hex[:8]}."""
        return f"{self._entry.thread_id_prefix}_{uuid4().hex[:8]}"

    def _build_agent(self, tools: List[StructuredTool]):
        """Return a CugaAgent for the given tool set, reusing a cached instance when possible.

        The compiled LangGraph graph is stateless — thread_id is passed at invoke
        time — so the same CugaAgent can safely serve multiple spawns of the same
        descriptor. Cache key includes tool names so changes to the tool list (e.g.
        in tests) still produce a fresh agent.
        """
        import os
        from cuga.sdk import CugaAgent

        no_cache = os.environ.get("CUGA_AGENT_SPAWN_NO_CACHE")
        if not no_cache:
            cache_key = (
                self._entry.name,
                str(self._entry.model),
                frozenset(t.name for t in tools),
            )
            cached = _agent_cache.get(cache_key)
            if cached is not None:
                return cached

        agent_kwargs: dict = {"tools": tools}
        if self._entry.model:
            from cuga.backend.llm.models import LLMManager
            agent_kwargs["model"] = LLMManager().get_model(self._entry.model)
        agent = CugaAgent(**agent_kwargs)

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

    async def _run_stream(self, agent, task: str, thread_id: str, cfg: dict) -> str:
        final_answer = ""
        forward = getattr(settings.agent_spawn, "forward_sync_subagent_events", True)
        async for chunk in agent.stream(task, thread_id=thread_id, config=cfg):
            # CugaAgent.stream() yields (namespace, state_dict) tuples when subgraphs=True
            state_dict = chunk[1] if isinstance(chunk, tuple) else chunk
            if not isinstance(state_dict, dict):
                continue
            node_data = next(iter(state_dict.values()), None)
            if not isinstance(node_data, dict):
                continue
            if forward and "script" in node_data:
                _emit("CodeAgent", {**node_data, "subagent": self._entry.name})
            candidate = node_data.get("final_answer")
            if candidate:
                final_answer = candidate
        return final_answer

    async def execute(self, task: str) -> str:
        """Run the sub-agent synchronously; return its final_answer."""
        depth = _spawn_depth.get()
        max_depth = getattr(settings.agent_spawn, "max_spawn_depth", 2)
        if depth >= max_depth:
            return f"[SpawnError] max_spawn_depth={max_depth} exceeded"

        tools = self._assemble_tools()
        thread_id = self._make_thread_id()
        invoke_cfg = self._build_invoke_config()

        from cuga.backend.observability.openlit_init import set_session_attribute
        parent_thread_id = self._parent_config.get("configurable", {}).get("thread_id", "")
        set_session_attribute(parent_thread_id)

        _emit("SpawnAgent", {
            "agent_name": self._entry.name,
            "task": task[:200],
            "mode": "sync",
            "thread_id": thread_id,
        })

        token = _spawn_depth.set(depth + 1)
        try:
            agent = self._build_agent(tools)
            answer = await self._run_stream(agent, task, thread_id, invoke_cfg)
        finally:
            _spawn_depth.reset(token)

        _emit("SpawnAgentResult", {
            "agent_name": self._entry.name,
            "thread_id": thread_id,
            "status": "success",
            "answer": answer[:500],
        })
        return answer

    async def execute_async(self, task: str) -> str:
        """Fire-and-forget spawn; return future_id for get_agent_result."""
        from cuga.backend.observability.openlit_init import set_session_attribute
        parent_thread_id = self._parent_config.get("configurable", {}).get("thread_id", "")
        set_session_attribute(parent_thread_id)  # propagate session before task is created

        future_id = f"future_{uuid4().hex[:8]}"
        self._spawn_futures[future_id] = {"status": "running", "result": None, "error": None}
        asyncio.create_task(self._execute_and_store(future_id, task))
        return future_id

    async def _execute_and_store(self, future_id: str, task: str) -> None:
        """Runs execute() and stores result; errors become descriptive strings."""
        try:
            result = await self.execute(task)
            self._spawn_futures[future_id] = {"status": "done", "result": result, "error": None}
        except Exception as e:
            logger.warning(f"agent_spawn: async execute failed for future_id={future_id}: {e}")
            self._spawn_futures[future_id] = {"status": "error", "result": None, "error": str(e)}
