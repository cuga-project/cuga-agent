"""Shared draft-state mutations: agent graph rebuild, tools include."""

from typing import Any

from cuga.backend.server.manage_routes.helpers import extract_agent_feature_overrides


async def rebuild_agent_from_config(agent: Any, config: dict[str, Any]) -> None:
    """Reset tool provider and rebuild an agent graph from config."""
    if not agent:
        return
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
    await agent.build_graph()
