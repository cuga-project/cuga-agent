"""Shared draft-state mutations: agent graph rebuild, tools include."""

import asyncio
from typing import Any

from cuga.backend.server.manage_routes.helpers import extract_agent_feature_overrides


def agent_rebuild_lock(agent: Any) -> asyncio.Lock:
    lock = getattr(agent, "_config_rebuild_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        agent._config_rebuild_lock = lock
    return lock


async def rebuild_agent_from_config(agent: Any, config: dict[str, Any], *, use_draft: bool = False) -> None:
    """Serialize direct edits with rebuilds triggered by a changed roster member."""
    if not agent:
        return
    async with agent_rebuild_lock(agent):
        await _apply_config_and_rebuild(agent, config, use_draft=use_draft)


async def _apply_config_and_rebuild(agent: Any, config: dict[str, Any], *, use_draft: bool = False) -> None:
    """Apply config while the caller holds agent_rebuild_lock."""
    tp = getattr(agent, "tool_provider", None)
    if tp is not None and hasattr(tp, "reset"):
        tp.reset()
    overrides = extract_agent_feature_overrides(config or {})
    if overrides["enable_todos"] is not None:
        agent.enable_todos = overrides["enable_todos"]
    if overrides["reflection_enabled"] is not None:
        agent.reflection_enabled = overrides["reflection_enabled"]
    if overrides["shortlisting_tool_threshold"] is not None:
        agent.shortlisting_tool_threshold = overrides["shortlisting_tool_threshold"]
    if overrides["cuga_lite_max_steps"] is not None:
        agent.cuga_lite_max_steps = overrides["cuga_lite_max_steps"]
    if overrides["enable_filesystem_tools"] is not None:
        agent.enable_filesystem_tools = overrides["enable_filesystem_tools"]
    llm_cfg = (config or {}).get("llm") or {}
    agent.llm_config = llm_cfg if llm_cfg else None
    from cuga.backend.server.agent_registry import (
        agent_special_instructions,
        configured_tool_apps,
        supervisor_graph_overrides,
    )

    if tp is not None:
        from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import unwrap_tool_provider

        scoped_provider = unwrap_tool_provider(tp)
        if hasattr(scoped_provider, "app_names"):
            scoped_provider.app_names = configured_tool_apps(config)
    agent.special_instructions = agent_special_instructions(config)
    for key, value in (await supervisor_graph_overrides(config, use_draft=use_draft)).items():
        setattr(agent, key, value)
    await agent.build_graph()
