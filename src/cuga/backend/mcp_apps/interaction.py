"""Turn an MCP Apps pause into a human-in-the-loop action, and a finished form back into a tool result."""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from cuga.backend.mcp_apps.protocol import ACTION_ID, McpAppInteractionPause, result_text
from cuga.backend.mcp_apps.store import Interaction, interaction_store

_MAX_RESULT_CHARS = 8000


def _call_repr(tool: str, arguments: Mapping[str, Any]) -> str:
    args = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in arguments.items())
    if len(args) > 300:
        args = args[:297] + "..."
    return f"{tool}({args})"


def create_pause_action(
    pause: McpAppInteractionPause, *, thread_id: Optional[str], user_id: Optional[str] = None
):
    """Record the interaction and build the ``FollowUpAction`` plus the note for the agent's history.

    Returns ``(hitl_action, agent_note)``.
    """
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
        ActionType,
        AdditionalData,
        FollowUpAction,
    )

    env = pause.envelope
    item = interaction_store.create(
        thread_id=thread_id,
        user_id=user_id,
        agent_id=pause.agent_id,
        server=str(env["server"]),
        tool=str(env["tool"]),
        agent_tool=pause.tool_name,
        resource_uri=str(env["resource_uri"]),
        tool_input=dict(env.get("tool_input") or {}),
        tool_result=dict(env.get("tool_result") or {}),
    )
    title = env.get("tool_title") or str(env["tool"]).replace("_", " ").capitalize()
    summary = result_text(item.tool_result)
    action = FollowUpAction(
        action_id=ACTION_ID,
        action_name=title,
        description=summary or f"{title} needs your input.",
        type=ActionType.MCP_APP,
        callback_url="/stream",
        additional_data=AdditionalData(
            tool={
                "mcp_app": {
                    "interaction_id": item.interaction_id,
                    "thread_id": thread_id,
                    "server": item.server,
                    "tool": item.tool,
                    "agent_tool": item.agent_tool,
                    "tool_title": env.get("tool_title"),
                    "resource_uri": item.resource_uri,
                    "tool_input": item.tool_input,
                    "tool_result": item.tool_result,
                }
            }
        ),
    )
    note = (
        f"Execution stopped at `{_call_repr(pause.tool_name, item.tool_input)}`: this tool opened an "
        "interactive form for the user. Code after this call did not run. The tool's result will be "
        "given in the next message, after the user finishes the form."
    )
    if summary:
        note += f"\nTool status: {summary}"
    return action, note


def interaction_id_of(additional_data: Any) -> Optional[str]:
    tool = getattr(additional_data, "tool", None) if additional_data is not None else None
    if isinstance(tool, Mapping) and isinstance(tool.get("mcp_app"), Mapping):
        value = tool["mcp_app"].get("interaction_id")
        return value if isinstance(value, str) else None
    return None


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_RESULT_CHARS else text[: _MAX_RESULT_CHARS - 20] + "\n... (truncated)"


def outcome_message(item: Optional[Interaction], metadata: Mapping[str, Any]) -> str:
    """The observation the agent reads as the result of the paused tool call."""
    if item is None:
        return (
            "The interactive form could not be continued (the session expired or the server restarted). "
            "The tool returned no result. Tell the user and ask whether to start again."
        )
    call = _call_repr(item.agent_tool, item.tool_input)
    if metadata.get("cancelled"):
        return (
            f"Result of `{call}`: the user closed the form without finishing it. The tool returned no "
            "result. Do not call the tool again on your own; ask the user how to proceed."
        )

    # The app's ui/update-model-context is what MCP Apps gives the model. The
    # server-verified results of the app's own tool calls back it up.
    context = item.model_context or {}
    data: Any = context.get("structuredContent")
    last = item.app_results[-1]["result"] if item.app_results else None
    if data is None and last is not None:
        data = last.get("structuredContent")
    context_text = "\n".join(
        str(c.get("text", ""))
        for c in context.get("content") or []
        if isinstance(c, Mapping) and c.get("type") == "text"
    ).strip()
    server_text = result_text(last) if last else ""
    user_message = str(metadata.get("message") or "").strip()

    parts = [f"Result of `{call}` — the user completed the interactive form."]
    if user_message:
        parts.append(f"User: {user_message}")
    if context_text or server_text:
        parts.append(f"Tool says: {context_text or server_text}")
    if data is not None:
        parts.append(
            "Returned data:\n```json\n" + _clip(json.dumps(data, indent=2, ensure_ascii=False)) + "\n```"
        )
    parts.append("Continue the task, using this as the return value of the tool call.")
    return "\n".join(parts)
