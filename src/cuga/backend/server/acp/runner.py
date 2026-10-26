"""Lazy construction of the direct CUGA runner used by ACP stdio."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, AsyncIterator


class StdioCugaRunner:
    """Protocol-neutral runner backed by CUGA's public direct SDK path.

    The web server's event stream depends on its full FastAPI lifespan and has
    legacy stdout writes, so it is unsafe for a protocol-clean stdio process.
    This narrow runner invokes the same CUGA graph through ``CugaAgent`` without
    starting an HTTP server. Construction remains lazy until the first prompt.
    """

    def __init__(self) -> None:
        self._agent: Any | None = None
        self._initialization_lock = asyncio.Lock()
        self._initialized = False
        self._closed = False
        self._servers_by_context: dict[str, list[Any]] = {}
        self._mcp_agents: dict[str, Any] = {}
        self._mcp_locks: dict[str, asyncio.Lock] = {}
        self._checkpoint_lock = asyncio.Lock()
        self._checkpointer_context: Any | None = None
        self._checkpointer: Any | None = None

    def configure_session(self, context_id: str, mcp_servers: list[Any]) -> None:
        self._servers_by_context[context_id] = list(mcp_servers)

    def _agent_kwargs(self, tools: list[Any] | None = None, instructions: str = "") -> dict[str, Any]:
        result: dict[str, Any] = {"tools": list(tools or [])}
        if self._checkpointer is not None:
            result["checkpointer"] = self._checkpointer
        if instructions:
            result["special_instructions"] = instructions
        return result

    def _get_agent(self) -> Any:
        if self._agent is None:
            from cuga.sdk import CugaAgent

            self._agent = CugaAgent(**self._agent_kwargs())
        return self._agent

    async def _ensure_checkpointer(self) -> None:
        import os
        from pathlib import Path

        folder = os.environ.get("CUGA_FOLDER")
        if not folder or self._checkpointer is not None:
            return
        async with self._checkpoint_lock:
            if self._checkpointer is not None:
                return
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            path = Path(folder) / "acp-checkpoints.sqlite3"
            path.parent.mkdir(parents=True, exist_ok=True)
            context = AsyncSqliteSaver.from_conn_string(str(path))
            self._checkpointer = await context.__aenter__()
            self._checkpointer_context = context

    @staticmethod
    def _server_key(servers: list[Any]) -> str:
        payload = [server.model_dump(by_alias=True) for server in servers]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    async def _get_initialized_agent(self, context_id: str | None = None) -> Any:
        """Initialize and compile the shared SDK agent exactly once."""
        if self._closed:
            raise RuntimeError("ACP runner is closed")
        await self._ensure_checkpointer()
        from cuga.backend.server.acp.mcp_tools import effective_mcp_servers

        servers = effective_mcp_servers(self._servers_by_context.get(context_id or "", []))
        if servers:
            key = self._server_key(servers)
            lock = self._mcp_locks.setdefault(key, asyncio.Lock())
            async with lock:
                agent = self._mcp_agents.get(key)
                if agent is None:
                    from cuga.backend.server.acp.mcp_tools import load_mcp_tools
                    from cuga.sdk import CugaAgent

                    tools, instructions = await load_mcp_tools(servers)
                    agent = CugaAgent(**self._agent_kwargs(tools, instructions))
                    await agent.initialize()
                    _ = agent.graph
                    self._mcp_agents[key] = agent
                return agent
        async with self._initialization_lock:
            agent = self._get_agent()
            if not self._initialized:
                await agent.initialize()
                # Graph compilation mutates shared agent state.
                _ = agent.graph
                self._initialized = True
            return agent

    async def run(
        self,
        message: str,
        context_id: str | None = None,
        approval: dict[str, Any] | None = None,
    ) -> AsyncIterator[Any]:
        from cuga.backend.server.agent_protocol.events import AgentStreamEvent

        agent = await self._get_initialized_agent(context_id)
        action_response = None
        invoke_message: str | None = message
        if approval is not None:
            action_id = approval.get("action_id")
            if not isinstance(action_id, str) or not action_id:
                yield AgentStreamEvent("error", final=True)
                return
            try:
                pending = await _pending_action(agent, context_id)
            except Exception:
                yield AgentStreamEvent("error", final=True)
                return
            if pending is None or pending.get("action_id") != action_id:
                yield AgentStreamEvent("error", final=True)
                return

            from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse

            action_response = ActionResponse(
                action_id=action_id,
                response_type=pending.get("type") or "confirmation",
                timestamp=datetime.now(timezone.utc).isoformat(),
                user_id="acp_user",
                confirmed=bool(approval.get("confirmed", False)),
                button_clicked=bool(approval.get("confirmed", False)),
                # Bound to the server-side pending action looked up above, so its identity
                # (e.g. an ACP permission pending_id) is echoed rather than client-supplied.
                additional_data=pending.get("additional_data"),
            )
            invoke_message = None

        result = await agent.invoke(
            invoke_message,
            thread_id=context_id,
            action_response=action_response,
        )
        try:
            pending = await _pending_action(agent, context_id)
        except Exception:
            yield AgentStreamEvent("error", final=True)
            return
        if pending is not None:
            event_data = {
                "text": pending.get("description") or "CUGA requests permission to continue.",
                "action_id": pending.get("action_id"),
            }
            details = pending.get("additional_data")
            tool = details.get("tool") if isinstance(details, dict) else None
            if isinstance(tool, dict) and isinstance(tool.get("full_code"), str):
                event_data["approval_code"] = tool["full_code"]
            yield AgentStreamEvent(
                "input_required",
                event_data,
                final=True,
            )
            return
        if result.error:
            yield AgentStreamEvent("error", final=True)
            return
        yield AgentStreamEvent("final_answer", {"text": result.answer}, final=True)

    async def shutdown(self) -> None:
        """Close and release the lazily constructed direct agent."""
        self._closed = True
        agents = [agent for agent in [self._agent, *self._mcp_agents.values()] if agent is not None]
        self._agent = None
        self._mcp_agents.clear()
        self._initialized = False
        for agent in agents:
            await agent.aclose()
        if self._checkpointer_context is not None:
            await self._checkpointer_context.__aexit__(None, None, None)
            self._checkpointer_context = None
            self._checkpointer = None


async def _pending_action(agent: Any, context_id: str | None) -> dict[str, Any] | None:
    if not context_id:
        return None
    config = {"configurable": {"thread_id": context_id}}
    async_get_state = getattr(agent.graph, "aget_state", None)
    snapshot = await async_get_state(config) if callable(async_get_state) else agent.graph.get_state(config)
    if not getattr(snapshot, "next", None):
        return None
    pending = (getattr(snapshot, "values", None) or {}).get("hitl_action")
    if pending is None:
        raise RuntimeError("Paused CUGA state has no permission action")
    if isinstance(pending, dict):
        result = pending
    else:
        dump = getattr(pending, "model_dump", None)
        if not callable(dump):
            raise RuntimeError("Paused CUGA state has malformed permission action")
        result = dump()
    action_id = result.get("action_id")
    if not isinstance(action_id, str) or not action_id:
        raise RuntimeError("Paused CUGA state has no valid permission action id")
    return result


def create_runner() -> StdioCugaRunner:
    """Create the lazy direct runner; no CUGA graph imports occur here."""
    return StdioCugaRunner()
