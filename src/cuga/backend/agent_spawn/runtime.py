"""SpawnAgentRuntime — drives a CugaAgent as an ad-hoc SubCuga subagent."""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any, Callable, Dict, List, Optional, Set
from uuid import uuid4

from langchain_core.tools import StructuredTool
from loguru import logger
from cuga.backend.cuga_graph.utils.langfuse_tracing import (
    get_langfuse_invoke_config,
    sync_langfuse_callbacks_from_config,
)
from cuga.backend.observability.openlit_init import set_session_attribute

from cuga.config import settings

_spawn_depth: contextvars.ContextVar[int] = contextvars.ContextVar("_spawn_depth", default=0)

# Per-request emit callback — ContextVar so concurrent AgentLoop streams never share one global.
_event_callback: contextvars.ContextVar[Optional[Callable[[str, dict], None]]] = contextvars.ContextVar(
    "_event_callback", default=None
)

# Tools that should never be passed down to subagents (would cause recursion or confusion).
# Children that may nest get spawn tools re-injected in prepare_node when depth allows.
_SPAWN_INTERNAL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "spawn_agent",
        "get_agent_result",
        "load_skill",
        "find_tools",
        "create_update_todos",
    }
)

# Keep subagent FinalAnswer short — parent needs parseable raw results, not chat.
_SUBAGENT_SPECIAL_INSTRUCTIONS = (
    "You are a short-lived subagent. Complete the assigned task and return ONLY the raw result. "
    "No preamble, no markdown fencing, no offers to help further."
)

# Per-parent-thread async future store + task tracking (graph is process-wide).
_futures_by_thread: Dict[str, Dict[str, Any]] = {}
_tasks_by_thread: Dict[str, Set[asyncio.Task]] = {}
_task_by_future: Dict[str, asyncio.Task] = {}


def set_event_callback(cb: Optional[Callable[[str, dict], None]]) -> contextvars.Token:
    """Install a per-context spawn event callback. Returns a token for reset_event_callback."""
    return _event_callback.set(cb)


def reset_event_callback(token: contextvars.Token) -> None:
    _event_callback.reset(token)


def _emit(event_name: str, data: dict) -> None:
    cb = _event_callback.get()
    if cb:
        try:
            cb(event_name, data)
        except Exception:
            pass  # never let event emission crash the agent


def thread_spawn_futures(thread_id: str) -> Dict[str, Any]:
    """Return the mutable futures map for a parent conversation thread."""
    key = thread_id or "_default"
    return _futures_by_thread.setdefault(key, {})


def pop_spawn_future(thread_id: str, future_id: str) -> None:
    store = _futures_by_thread.get(thread_id or "_default")
    if store is not None:
        store.pop(future_id, None)
        if not store:
            _futures_by_thread.pop(thread_id or "_default", None)


def cancel_spawn_future(thread_id: str, future_id: str) -> bool:
    """Cancel one async spawn by future_id. Returns True if a running task was cancelled."""
    task = _task_by_future.get(future_id)
    cancelled = False
    if task is not None and not task.done():
        task.cancel()
        cancelled = True
    key = thread_id or "_default"
    store = _futures_by_thread.get(key)
    if store is not None and future_id in store and store[future_id].get("status") == "running":
        store[future_id] = {"status": "timeout", "result": None, "error": "timeout"}
    return cancelled


def clear_runtime_caches(thread_id: Optional[str] = None) -> None:
    """Clear spawn futures (and cancel tracked async tasks) for one thread or all."""
    if thread_id is None:
        for tasks in list(_tasks_by_thread.values()):
            for t in list(tasks):
                t.cancel()
        _tasks_by_thread.clear()
        _futures_by_thread.clear()
        _task_by_future.clear()
        return
    key = thread_id or "_default"
    for t in list(_tasks_by_thread.get(key, ())):
        t.cancel()
    store = _futures_by_thread.get(key) or {}
    for fid in list(store):
        _task_by_future.pop(fid, None)
    _tasks_by_thread.pop(key, None)
    _futures_by_thread.pop(key, None)


def pending_spawn_tasks(thread_id: str) -> List[asyncio.Task]:
    key = thread_id or "_default"
    return [t for t in _tasks_by_thread.get(key, set()) if not t.done()]


async def wait_pending_spawns(
    thread_id: str, timeout: float = 5.0, *, cancel_on_timeout: bool = True
) -> None:
    """Await in-flight async spawns for this parent thread (best-effort).

    On timeout, cancels residual tasks by default so the parent stream can end
    without leaving tool-inheriting children running.
    """
    pending = pending_spawn_tasks(thread_id)
    if not pending:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout)
    except asyncio.TimeoutError:
        still = pending_spawn_tasks(thread_id)
        logger.warning(
            f"agent_spawn: {len(still)} async spawn(s) still running "
            f"after {timeout}s for thread={thread_id!r}"
        )
        if cancel_on_timeout and still:
            clear_runtime_caches(thread_id)


def _track_task(thread_id: str, future_id: str, task: asyncio.Task) -> None:
    key = thread_id or "_default"
    bucket = _tasks_by_thread.setdefault(key, set())
    bucket.add(task)
    _task_by_future[future_id] = task

    def _done(t: asyncio.Task) -> None:
        bucket.discard(t)
        _task_by_future.pop(future_id, None)
        # Only pop if this callback's bucket is still the live map entry — a
        # reused thread_id may have a fresher bucket after clear_runtime_caches.
        if not bucket and _tasks_by_thread.get(key) is bucket:
            _tasks_by_thread.pop(key, None)

    task.add_done_callback(_done)


class SpawnAgentRuntime:
    def __init__(
        self,
        parent_structured_tools: List[StructuredTool],
        parent_config: Optional[Dict[str, Any]] = None,
        spawn_futures_ref: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._parent_structured_tools = parent_structured_tools
        self._parent_config = parent_config or {}
        # Prefer explicit ref (tests); else per-parent-thread store.
        if spawn_futures_ref is not None:
            self._spawn_futures: Dict[str, Any] = spawn_futures_ref
        else:
            self._spawn_futures = thread_spawn_futures(self._parent_thread_id())

    @classmethod
    def from_parent(
        cls,
        parent_config: Optional[Dict[str, Any]] = None,
        spawn_futures_ref: Optional[Dict[str, Any]] = None,
        parent_structured_tools: Optional[List[StructuredTool]] = None,
    ) -> "SpawnAgentRuntime":
        """Create a runtime that inherits parent tools with a fresh context.

        Spawn/skill meta-tools are filtered out; nesting re-injects spawn tools
        in the child's prepare_node when depth allows.
        """
        filtered = [t for t in (parent_structured_tools or []) if t.name not in _SPAWN_INTERNAL_TOOL_NAMES]
        return cls(filtered, parent_config, spawn_futures_ref)

    def _make_thread_id(self) -> str:
        return f"sub_cuga_{uuid4().hex[:8]}"

    def _parent_thread_id(self) -> str:
        return self._parent_config.get("configurable", {}).get("thread_id", "") or ""

    def _resolve_thread_ids(self, share_workspace: bool) -> tuple[str, str]:
        """Return (conversation_thread_id, workspace_thread_id).

        Conversation thread is always a fresh sub_cuga_* id (isolated chat/checkpointer).
        Workspace thread defaults to that same id; with share_workspace=True it reuses the
        parent thread so fs/run_command see parent uploads and session files.
        """
        conversation_thread_id = self._make_thread_id()
        parent_thread_id = self._parent_thread_id()
        if share_workspace and parent_thread_id:
            return conversation_thread_id, parent_thread_id
        return conversation_thread_id, conversation_thread_id

    def _build_agent(self, tools: List[StructuredTool]):
        # Fresh agent per spawn: parallel async subagents from the same parent
        # share tool names + parent thread_id, so a process-level cache would
        # hand them the same CugaAgent and race shared sandbox/tool state.
        from cuga.sdk import CugaAgent

        return CugaAgent(tools=tools, special_instructions=_SUBAGENT_SPECIAL_INSTRUCTIONS)

    def _build_invoke_config(self, workspace_thread_id: str = "") -> dict:
        if self._parent_config:
            sync_langfuse_callbacks_from_config(self._parent_config)
        cfg = get_langfuse_invoke_config()
        configurable = dict(cfg.get("configurable") or {})
        parent_cfg = dict((self._parent_config or {}).get("configurable") or {})
        for key in ("skills_enabled", "skills_folder"):
            if key in parent_cfg:
                configurable[key] = parent_cfg[key]
        if workspace_thread_id:
            configurable["workspace_thread_id"] = workspace_thread_id
        if configurable:
            cfg = {**cfg, "configurable": configurable}
        return cfg

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

    async def execute(self, task: str, spawn_id: str = "", share_workspace: bool = False) -> str:
        """Run the sub-agent synchronously; return its final_answer."""
        depth = _spawn_depth.get()
        max_depth = getattr(settings.agent_spawn, "max_spawn_depth", 2)
        if depth >= max_depth:
            return f"[SpawnError] max_spawn_depth={max_depth} exceeded"

        if not spawn_id:
            spawn_id = f"sync_{uuid4().hex[:8]}"

        tools = self._parent_structured_tools
        thread_id, workspace_thread_id = self._resolve_thread_ids(share_workspace)
        invoke_cfg = self._build_invoke_config(workspace_thread_id=workspace_thread_id)

        parent_thread_id = self._parent_thread_id()
        set_session_attribute(parent_thread_id)

        _emit(
            "SpawnAgent",
            {
                "agent_name": "SubCuga",
                "task": task[:200],
                "mode": "sync" if spawn_id.startswith("sync_") else "async",
                "thread_id": thread_id,
                "workspace_thread_id": workspace_thread_id,
                "share_workspace": bool(share_workspace and parent_thread_id),
                "spawn_id": spawn_id,
            },
        )

        token = _spawn_depth.set(depth + 1)
        status = "success"
        answer = ""
        try:
            agent = self._build_agent(tools)
            answer = await self._run_stream(agent, task, thread_id, invoke_cfg, spawn_id=spawn_id)
        except asyncio.CancelledError:
            status = "cancelled"
            answer = "[SpawnError] cancelled"
            raise
        except Exception as e:
            status = "error"
            answer = f"[SpawnError] {e}"
            logger.warning(f"agent_spawn: sync execute failed spawn_id={spawn_id}: {e}")
        finally:
            _spawn_depth.reset(token)
            if status != "cancelled":
                _emit(
                    "SpawnAgentResult",
                    {
                        "agent_name": "SubCuga",
                        "thread_id": thread_id,
                        "workspace_thread_id": workspace_thread_id,
                        "status": status,
                        "answer": (answer or "")[:500],
                        "spawn_id": spawn_id,
                    },
                )
        return answer

    async def execute_async(self, task: str, share_workspace: bool = False) -> str:
        """Fire-and-forget spawn; return future_id for get_agent_result."""
        parent_thread_id = self._parent_thread_id()
        set_session_attribute(parent_thread_id)

        future_id = f"future_{uuid4().hex[:8]}"
        self._spawn_futures[future_id] = {"status": "running", "result": None, "error": None}
        task_obj = asyncio.create_task(
            self._execute_and_store(future_id, task, share_workspace=share_workspace)
        )
        _track_task(parent_thread_id, future_id, task_obj)
        return future_id

    async def _execute_and_store(self, future_id: str, task: str, share_workspace: bool = False) -> None:
        try:
            result = await self.execute(task, spawn_id=future_id, share_workspace=share_workspace)
            self._spawn_futures[future_id] = {"status": "done", "result": result, "error": None}
        except asyncio.CancelledError:
            self._spawn_futures[future_id] = {
                "status": "cancelled",
                "result": None,
                "error": "cancelled",
            }
            _emit(
                "SpawnAgentResult",
                {
                    "agent_name": "SubCuga",
                    "status": "cancelled",
                    "answer": "cancelled",
                    "spawn_id": future_id,
                },
            )
            raise
        except Exception as e:
            logger.warning(f"agent_spawn: async execute failed for future_id={future_id}: {e}")
            self._spawn_futures[future_id] = {"status": "error", "result": None, "error": str(e)}
            _emit(
                "SpawnAgentResult",
                {
                    "agent_name": "SubCuga",
                    "status": "error",
                    "answer": str(e)[:500],
                    "spawn_id": future_id,
                },
            )
