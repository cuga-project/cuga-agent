"""Prompt formatting for agent spawning."""


def format_available_agents_block() -> str:
    return (
        "**Ad-hoc subagent spawning is available.** When a skill or task instructs you to use a subagent, "
        "call `await spawn_agent(task=\"<full task description and context>\")`. "
        "The subagent (SubCuga) inherits all your tools and runs with a completely fresh context — "
        "no prior conversation history. Pass everything the subagent needs in the task string. "
        "Use `mode='async'` + `await get_agent_result(future_id)` for parallel spawning."
    )
