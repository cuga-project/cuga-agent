"""DAM runtime configuration for CUGA's reusable ACP runner."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from cuga.backend.server.acp.runner import StdioCugaRunner

from .instructions import slack_tool_instructions
from .mcp_tools import effective_mcp_servers, load_mcp_tools


class DamRunner(StdioCugaRunner):
    """Bind DAM-managed tools and persistent checkpoints to ACP contexts."""

    mcp_transports = ("http", "sse")
    preload_modules = (
        "langgraph.checkpoint.sqlite.aio",
        "fastmcp",
        "langchain.mcp",
    )

    def __init__(self) -> None:
        super().__init__()
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

    async def _before_warm_initialize(self) -> None:
        await self._ensure_checkpointer()

    @staticmethod
    def _server_key(servers: list[Any]) -> str:
        payload = [server.model_dump(by_alias=True) for server in servers]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    async def _get_initialized_agent(self, context_id: str | None = None) -> Any:
        if self._closed:
            raise RuntimeError("ACP runner is closed")
        await self._ensure_checkpointer()
        servers = effective_mcp_servers(self._servers_by_context.get(context_id or "", []))
        if not servers:
            return await super()._get_initialized_agent(context_id)

        key = self._server_key(servers)
        lock = self._mcp_locks.setdefault(key, asyncio.Lock())
        async with lock:
            agent = self._mcp_agents.get(key)
            if agent is None:
                from cuga.sdk import CugaAgent

                tools, aliases = await load_mcp_tools(servers)
                instructions = (aliases + slack_tool_instructions({tool.name for tool in tools})).strip()
                agent = CugaAgent(**self._agent_kwargs(tools, instructions))
                await agent.initialize()
                _ = agent.graph
                self._mcp_agents[key] = agent
            return agent

    async def shutdown(self) -> None:
        agents = list(self._mcp_agents.values())
        self._mcp_agents.clear()
        try:
            await super().shutdown()
        finally:
            try:
                for agent in agents:
                    await agent.aclose()
            finally:
                if self._checkpointer_context is not None:
                    await self._checkpointer_context.__aexit__(None, None, None)
                    self._checkpointer_context = None
                    self._checkpointer = None


def create_runner() -> DamRunner:
    return DamRunner()
