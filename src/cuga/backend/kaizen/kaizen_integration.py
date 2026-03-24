"""
Kaizen Integration Module

Self-contained client wrapper for the Kaizen MCP server.
Handles fetching guidelines and saving trajectories via FastMCP client.
All operations are non-fatal — errors are logged as warnings and never crash the agent.
"""

import json
from typing import Optional, List

from loguru import logger
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from cuga.config import settings


class KaizenIntegration:
    """Client wrapper for interacting with the Kaizen MCP server."""

    @classmethod
    def is_enabled(cls) -> bool:
        """Check if Kaizen integration is active based on settings."""
        if not settings.kaizen.enabled:
            return False
        if settings.kaizen.lite_mode_only and not settings.advanced_features.lite_mode:
            return False
        return True

    @classmethod
    async def get_guidelines(cls, task: str) -> Optional[str]:
        """Fetch guidelines from Kaizen for the given task description.

        Args:
            task: The task description to search guidelines for.

        Returns:
            Guidelines text if available, None otherwise.
        """
        if not cls.is_enabled():
            return None
        try:
            result = await cls._call_tool("get_guidelines", {"task": task})
            if result:
                logger.info(f"Kaizen: Received guidelines ({len(str(result))} chars)")
                return str(result)
            logger.debug("Kaizen: No guidelines found for this task")
            return None
        except Exception as e:
            logger.warning(f"Kaizen get_guidelines failed (non-fatal): {e}")
            return None

    @classmethod
    async def save_trajectory(
        cls,
        chat_messages: List[BaseMessage],
        task_id: str,
        success: bool,
    ) -> None:
        """Save the agent trajectory to Kaizen for tip generation.

        Args:
            chat_messages: List of LangChain BaseMessage objects from CugaLite.
            task_id: Identifier for the task (sub_task or first user message).
            success: Whether the task completed successfully.
        """
        if not cls.is_enabled():
            return
        if success and not settings.kaizen.save_on_success:
            return
        if not success and not settings.kaizen.save_on_failure:
            return

        try:
            logger.debug(
                f"Kaizen: Converting {len(chat_messages)} chat_messages. "
                f"Types: {[type(m).__name__ for m in chat_messages[:10]]}"
            )
            openai_messages = cls._convert_messages(chat_messages)
            if not openai_messages:
                logger.warning("Kaizen: No messages to save (empty trajectory)")
                return

            trajectory_json = json.dumps(openai_messages)
            logger.info(
                f"Kaizen: Saving trajectory ({len(openai_messages)} messages, "
                f"{len(trajectory_json)} chars, "
                f"task_id={task_id[:80]}, success={success})"
            )
            logger.debug(f"Kaizen: trajectory_data preview: {trajectory_json[:500]}")
            await cls._call_tool(
                "save_trajectory",
                {
                    "trajectory_data": trajectory_json,
                    "task_id": task_id,
                },
            )
            logger.info("Kaizen: Trajectory saved successfully")
        except Exception as e:
            logger.warning(f"Kaizen save_trajectory failed (non-fatal): {e}")

    @staticmethod
    def _convert_messages(chat_messages: List[BaseMessage]) -> list:
        """Convert LangChain BaseMessage list to OpenAI conversation format.

        Args:
            chat_messages: List of LangChain BaseMessage objects.

        Returns:
            List of dicts in OpenAI format: [{"role": "...", "content": "..."}]
        """
        result = []
        for i, msg in enumerate(chat_messages):
            if isinstance(msg, HumanMessage):
                role = "user"
            elif isinstance(msg, AIMessage):
                role = "assistant"
            else:
                logger.debug(f"Kaizen: Skipping message {i} of type {type(msg).__name__}")
                continue  # Skip system messages, tool messages, etc.

            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content:  # Skip empty messages
                result.append({"role": role, "content": content})
            else:
                logger.debug(f"Kaizen: Skipping empty {role} message {i}")
        logger.debug(f"Kaizen: Converted {len(result)}/{len(chat_messages)} messages")
        return result

    @classmethod
    async def _call_tool(cls, tool_name: str, args: dict):
        """Call a Kaizen MCP tool via FastMCP client.

        Creates a fresh connection per call to avoid stale connection issues.

        Args:
            tool_name: Name of the MCP tool to call.
            args: Arguments dict for the tool.

        Returns:
            The tool result (parsed from TextContent if applicable).
        """
        from fastmcp import Client
        from fastmcp.client.transports import SSETransport
        from mcp.types import TextContent

        url = settings.kaizen.url
        transport = SSETransport(url)

        async with Client(transport) as client:
            result = await client.call_tool(tool_name, args)

            # Parse result from MCP CallToolResult
            # fastmcp 2.x returns CallToolResult with .content (list) and .data attributes
            if result is None:
                return None

            # Use .data if available (pre-parsed by fastmcp)
            if hasattr(result, 'data') and result.data is not None:
                data = result.data
                if isinstance(data, str):
                    try:
                        return json.loads(data)
                    except (json.JSONDecodeError, TypeError):
                        return data
                return data

            # Fallback: parse .content list
            if hasattr(result, 'content') and result.content:
                first = result.content[0]
                if isinstance(first, TextContent):
                    text = first.text
                    try:
                        return json.loads(text)
                    except (json.JSONDecodeError, TypeError):
                        return text
                return str(first)

            return None
