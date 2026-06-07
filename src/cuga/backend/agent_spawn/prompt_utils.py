"""Prompt formatting for available agents block."""

from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry


def format_available_agents_block(registry: AgentDescriptorRegistry) -> str:
    lines = ["<available_agents>"]
    for entry in sorted(registry.all(), key=lambda e: e.name):
        lines.append(f"- **{entry.name}**: {entry.description}")
    lines.append("</available_agents>")
    lines.append("")
    lines.append(
        "Use `await spawn_agent(name=\"<agent_name>\", task=\"<task>\")` to delegate "
        "a sub-task to a named agent. Use mode='async' for fire-and-forget, then "
        "`await get_agent_result(future_id)` to retrieve the result."
    )
    return "\n".join(lines)
