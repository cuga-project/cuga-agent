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
        "Only use the default mode='sync' when there is a single subtask or when each subtask depends on the previous result. "
        "Workspace: default is an isolated empty workspace. Pass `share_workspace=True` for a shared "
        "workspace both ways — child reads this session's uploads/files, and files the child creates "
        "(e.g. markdown reports) appear in the parent session (do not use for parallel async writers "
        "on the same paths). "
        "**Inspect before you act (CRITICAL):** after each `get_agent_result`, `print` the returned value in that "
        "same code block (or an isolated follow-up block) and read the actual text before writing any parsing, "
        "formatting, or follow-up `spawn_agent` code. Subagent answers may be chatty or wrapped — do not assume "
        "a fixed shape like `cpu: 10`. Treat returned strings as opaque data. "
        "When passing prior results into a new spawn task, never embed them in nested f-strings or triple quotes — "
        "build the task with concatenation and `json.dumps(...)`, e.g. "
        '`task = "Format as a markdown table. Inputs JSON: " + json.dumps({"cpu": cpu_out, "disk": disk_out})`.'
    )
