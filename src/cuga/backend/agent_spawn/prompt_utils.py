"""Prompt formatting for available agents block."""

from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry


def format_available_agents_block(registry: AgentDescriptorRegistry) -> str:
    entries = sorted(registry.all(), key=lambda e: e.name)
    lines = []

    if entries:
        lines.append("<available_agents>")
        for entry in entries:
            lines.append(f"- **{entry.name}**: {entry.description}")
        lines.append("</available_agents>")
        lines.append("")
        lines.append(
            "**Named agent:** `await spawn_agent(name=\"<agent_name>\", task=\"<task>\")`\n"
            "**Ad-hoc subagent (inherits all your tools):** `await spawn_agent(task=\"<full task + context>\")`\n"
            "Use `mode='async'` for fire-and-forget, then `await get_agent_result(future_id)` to retrieve."
        )
    else:
        lines.append(
            "**Ad-hoc subagent spawning is available.** When a skill or task instructs you to use a subagent, "
            "call `await spawn_agent(task=\"<full task description and context>\")`. "
            "The subagent inherits all your tools and runs with a completely fresh context — "
            "no prior conversation history. Pass everything the subagent needs in the task string. "
            "Use `mode='async'` + `await get_agent_result(future_id)` for parallel spawning."
        )

    return "\n".join(lines)
