"""Feature flag for the manage-UI multi-agent registry."""


def is_agent_registry_enabled() -> bool:
    from cuga.config import settings

    return bool(getattr(getattr(settings, "supervisor", None), "registry_enabled", False))


def agent_special_instructions(config: dict) -> str | None:
    """Read editor instructions and legacy roster instructions consistently."""
    if "special_instructions" in config:
        return config.get("special_instructions") or None
    meta = config.get("agent") or {}
    if meta.get("kind") == "supervisor":
        instructions = (config.get("supervisor") or {}).get("special_instructions")
        if instructions:
            return instructions
    return meta.get("description") or None


async def supervisor_graph_overrides(config: dict, *, use_draft: bool = False) -> dict:
    """Registry agents use their stored role, including the default agent.

    Outside registry mode the legacy YAML/global supervisor configuration still
    applies. Inside it, a single agent must never inherit that global roster.
    """
    if not is_agent_registry_enabled():
        return {}
    if ((config or {}).get("agent") or {}).get("kind", "single") != "supervisor":
        return {"supervisor_enabled": False, "supervisor_agents": None, "supervisor_plan_approval": False}

    from cuga.supervisor_utils.supervisor_config import build_agents_from_stored_subagents

    supervisor = (config or {}).get("supervisor") or {}
    agents = await build_agents_from_stored_subagents(supervisor.get("subAgents") or [], use_draft=use_draft)
    return {
        "supervisor_enabled": True,
        "supervisor_agents": agents,
        "supervisor_plan_approval": bool(supervisor.get("planApproval")),
    }
