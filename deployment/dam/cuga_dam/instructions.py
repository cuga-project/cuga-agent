"""DAM channel instructions kept separate from MCP transport adaptation."""

from __future__ import annotations


def slack_tool_instructions(names: set[str]) -> str:
    """Tell CUGA how DAM delivers Slack turns when those tools are granted."""
    parts: list[str] = []
    if "mcp__platform_outbound__reply" in names:
        parts.append(
            "For Slack turns, plain answer text is not delivered. Follow the prompt's "
            "how-to-respond block using the CUGA tool names above: call "
            "mcp__platform_outbound__reply with threadTs when replying."
        )
        if "mcp__platform_outbound__no_reply_needed" in names:
            parts.append("Call mcp__platform_outbound__no_reply_needed when silence is appropriate.")
        parts.append("Do not end a Slack turn with only plain answer text.")
    if "mcp__platform_outbound__send_channel_message" in names:
        parts.append(
            "When explicitly asked to send a new top-level message to a connected Slack channel, "
            "call mcp__platform_outbound__send_channel_message with channel='slack' "
            "and text set to the message."
        )
        if "mcp__platform_outbound__describe_channel" in names:
            parts.append(
                "If the request names a Slack channel, first call "
                "mcp__platform_outbound__describe_channel with channel='slack'. "
                "Match the requested name (ignoring a leading '#') to exactly one "
                "chats[].title in the result, then pass that chat's id as chatId. "
                "If there is no unique match, ask for clarification without sending."
            )
        else:
            parts.append("For a named channel, set chatId to its ID.")
        parts.append(
            "For the agent's default chat, omit chatId. "
            "Use reply with threadTs for a thread response instead."
        )
    return " " + " ".join(parts) if parts else ""
