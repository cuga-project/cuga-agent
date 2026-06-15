"""Prompt formatting for agent spawning."""


def format_available_agents_block() -> str:
    return (
        "**Ad-hoc subagent spawning is available.** When a skill or task instructs you to use a subagent, "
        "call `await spawn_agent(task=\"<full task description and context>\")`. "
        "The subagent (SubCuga) inherits all your tools and runs with a completely fresh context — "
        "no prior conversation history. Pass everything the subagent needs in the task string. "
        "For multiple independent subtasks, always prefer parallel spawning: "
        "call `await spawn_agent(..., mode='async')` for each subtask first (collecting future_ids), "
        "then retrieve all results with `await get_agent_result(future_id)`. "
        "Only use the default mode='sync' when there is a single subtask or when each subtask depends on the previous result."
    )
