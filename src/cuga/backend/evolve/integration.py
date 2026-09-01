"""
Evolve Integration Module

Self-contained client wrapper for the Evolve MCP server.
It can resolve Evolve through the CUGA MCP registry or talk to a direct SSE
endpoint, depending on configuration. All operations are non-fatal: errors are
logged as warnings and never crash the agent.
"""

import json
from typing import Any, List, Optional

import aiohttp
from loguru import logger
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from cuga.config import settings

# Placeholder user identifiers that must not reach Evolve as real users:
# "default" is AgentState.user_id's default; "default_user" is the server's
# DEFAULT_USER_ID for unauthenticated requests.
_EVOLVE_SENTINEL_IDS = {"default", "default_user"}


def normalize_evolve_identifier(value: Optional[str]) -> Optional[str]:
    """Return None for empty or sentinel placeholders so Evolve only sees real ids."""
    if value is None:
        return None
    stripped = str(value).strip()
    if not stripped or stripped in _EVOLVE_SENTINEL_IDS:
        return None
    return stripped


class EvolveIntegration:
    """Client wrapper for interacting with the Evolve MCP server."""

    @staticmethod
    def _get_mode() -> str:
        mode = str(getattr(settings.evolve, "mode", "auto") or "auto").lower()
        return mode if mode in {"auto", "registry", "direct"} else "auto"

    @staticmethod
    def _get_app_name() -> str:
        return str(getattr(settings.evolve, "app_name", "evolve") or "evolve")

    @classmethod
    def is_enabled(cls) -> bool:
        """Check if Evolve integration is active based on settings."""
        return bool(settings.evolve.enabled)

    @classmethod
    async def get_guidelines(
        cls,
        task: str,
        user_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[str]:
        """Fetch guidelines from Evolve for the given task description."""
        if not cls.is_enabled():
            return None
        try:
            user_id = normalize_evolve_identifier(user_id)
            namespace_id = normalize_evolve_identifier(namespace_id)
            session_id = normalize_evolve_identifier(session_id)
            args: dict = {"task": task}
            if user_id:
                args["user_id"] = user_id
            if namespace_id:
                args["namespace_id"] = namespace_id
            if session_id:
                args["session_id"] = session_id
            result = await cls._call_tool("get_guidelines", args)
            if result:
                logger.info(f"Evolve: Received guidelines ({len(str(result))} chars)")
                return str(result)
            logger.debug("Evolve: No guidelines found for this task")
            return None
        except Exception as e:
            logger.warning(f"Evolve get_guidelines failed (non-fatal): {e}")
            return None

    @classmethod
    async def get_guidelines_with_attribution(
        cls,
        task: str,
        user_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Fetch formatted guidelines and the entity IDs included in the prompt."""
        if not cls.is_enabled():
            return None
        try:
            args: dict[str, Any] = {"task": task}
            for key, value in {
                "user_id": normalize_evolve_identifier(user_id),
                "namespace_id": normalize_evolve_identifier(namespace_id),
                "session_id": normalize_evolve_identifier(session_id),
            }.items():
                if value:
                    args[key] = value
            result = await cls._call_tool("get_guidelines_with_attribution", args)
            if isinstance(result, str):
                result = json.loads(result)
            if not isinstance(result, dict):
                return None
            return {
                "text": str(result.get("text") or ""),
                "entity_ids": [
                    str(entity_id) for entity_id in result.get("entity_ids", []) if str(entity_id).strip()
                ],
                "namespace_id": result.get("namespace_id"),
            }
        except Exception as e:
            logger.warning(f"Evolve attributed guideline retrieval failed (non-fatal): {e}")
            return None

    @classmethod
    async def store_user_facts(
        cls,
        user_id: str,
        message: str,
        metadata: dict | None = None,
    ) -> None:
        """Store durable user facts/preferences without interrupting lite execution."""
        if not cls.is_enabled():
            return
        if not user_id or not message:
            return

        try:
            payload = {
                "user_id": user_id,
                "message": message,
                "metadata": json.dumps(metadata or {}),
            }
            result = await cls._call_tool("store_user_facts", payload)
            if isinstance(result, dict):
                logger.info(
                    "Evolve: Stored user facts (stored_count=%s)",
                    result.get("stored_count", 0),
                )
        except Exception as e:
            logger.warning(f"Evolve store_user_facts failed (non-fatal): {e}")

    @classmethod
    async def retrieve_user_facts(
        cls,
        user_id: str,
        query: str,
        limit: int = 5,
    ) -> Optional[dict]:
        """Retrieve durable user facts/preferences without interrupting lite execution."""
        if not cls.is_enabled():
            return None
        if not user_id or not query:
            return None

        try:
            result = await cls._call_tool(
                "retrieve_user_facts",
                {
                    "user_id": user_id,
                    "query": query,
                    "limit": limit,
                },
            )
            if result:
                return result if isinstance(result, dict) else None
            return None
        except Exception as e:
            logger.warning(f"Evolve retrieve_user_facts failed (non-fatal): {e}")
            return None

    @classmethod
    async def save_trajectory(
        cls,
        chat_messages: List[BaseMessage],
        task_id: str,
        success: bool,
        user_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Save the agent trajectory to Evolve for tip generation."""
        if not cls.is_enabled():
            return
        if success and not settings.evolve.save_on_success:
            return
        if not success and not settings.evolve.save_on_failure:
            return

        try:
            user_id = normalize_evolve_identifier(user_id)
            namespace_id = normalize_evolve_identifier(namespace_id)
            session_id = normalize_evolve_identifier(session_id)
            logger.debug(
                f"Evolve: Converting {len(chat_messages)} chat_messages. "
                f"Types: {[type(m).__name__ for m in chat_messages[:10]]}"
            )
            openai_messages = cls._convert_messages(chat_messages)
            if not openai_messages:
                logger.warning("Evolve: No messages to save (empty trajectory)")
                return

            trajectory_json = json.dumps(openai_messages)
            logger.info(
                f"Evolve: Saving trajectory ({len(openai_messages)} messages, "
                f"{len(trajectory_json)} chars, "
                f"task_id={task_id[:80]}, success={success})"
            )
            logger.debug(f"Evolve: trajectory_data preview: {trajectory_json[:500]}")
            args: dict = {
                "trajectory_data": trajectory_json,
                "task_id": task_id,
            }
            if user_id:
                args["user_id"] = user_id
            if namespace_id:
                args["namespace_id"] = namespace_id
            if session_id:
                args["session_id"] = session_id
            await cls._call_tool("save_trajectory", args)
            logger.info("Evolve: Trajectory saved successfully")
        except Exception as e:
            logger.warning(f"Evolve save_trajectory failed (non-fatal): {e}")

    @classmethod
    async def delete_entity(
        cls,
        entity_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Delete an entity through Evolve's ownership checks."""
        if not cls.is_enabled() or not entity_id:
            return None
        args: dict[str, Any] = {"entity_id": entity_id}
        for key, value in {
            "user_id": normalize_evolve_identifier(user_id),
            "agent_id": normalize_evolve_identifier(agent_id),
            "namespace_id": normalize_evolve_identifier(namespace_id),
        }.items():
            if value:
                args[key] = value
        return await cls._call_structured_tool("delete_entity", args)

    @classmethod
    async def list_entities(
        cls,
        entity_types: Optional[list[str]] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        metadata_filters: Optional[dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
        include_content: bool = False,
        namespace_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Return a structured Evolve entity inventory."""
        args: dict[str, Any] = {"limit": limit, "include_content": include_content}
        optional = {
            "entity_types": entity_types,
            "user_id": normalize_evolve_identifier(user_id),
            "agent_id": normalize_evolve_identifier(agent_id),
            "session_id": normalize_evolve_identifier(session_id),
            "metadata_filters": json.dumps(metadata_filters) if metadata_filters else None,
            "cursor": cursor,
            "namespace_id": normalize_evolve_identifier(namespace_id),
        }
        args.update({key: value for key, value in optional.items() if value is not None})
        return await cls._call_structured_tool("list_entities", args)

    @classmethod
    async def get_entity(
        cls,
        entity_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Return one Evolve entity without mutating its access timestamp."""
        args: dict[str, Any] = {"entity_id": entity_id, "record_access": False}
        for key, value in {
            "user_id": normalize_evolve_identifier(user_id),
            "agent_id": normalize_evolve_identifier(agent_id),
            "namespace_id": normalize_evolve_identifier(namespace_id),
        }.items():
            if value:
                args[key] = value
        return await cls._call_structured_tool("get_entity", args)

    @classmethod
    async def patch_entity_metadata(
        cls,
        entity_id: str,
        metadata_patch: dict[str, Any],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Patch entity metadata through Evolve's authorization layer."""
        args: dict[str, Any] = {
            "entity_id": entity_id,
            "metadata_patch": json.dumps(metadata_patch),
        }
        for key, value in {
            "user_id": normalize_evolve_identifier(user_id),
            "agent_id": normalize_evolve_identifier(agent_id),
            "namespace_id": normalize_evolve_identifier(namespace_id),
        }.items():
            if value:
                args[key] = value
        return await cls._call_structured_tool("patch_entity_metadata", args)

    @classmethod
    async def record_access(
        cls,
        entity_ids: list[str],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Record actual use of entities for retention and audit purposes."""
        args: dict[str, Any] = {"entity_ids": entity_ids}
        for key, value in {
            "user_id": normalize_evolve_identifier(user_id),
            "agent_id": normalize_evolve_identifier(agent_id),
            "namespace_id": normalize_evolve_identifier(namespace_id),
        }.items():
            if value:
                args[key] = value
        return await cls._call_structured_tool("record_access", args)

    @classmethod
    async def validate_retention_policy(cls, policy: dict[str, Any]) -> Optional[dict]:
        """Validate and normalize an Evolve retention policy."""
        return await cls._call_structured_tool(
            "validate_retention_policy",
            {"policy": json.dumps(policy)},
        )

    @classmethod
    async def run_retention(
        cls,
        policy: dict[str, Any],
        *,
        dry_run: bool = True,
        as_of: Optional[str] = None,
        scan_limit: Optional[int] = None,
        run_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
        metadata_filters: Optional[dict[str, Any]] = None,
    ) -> Optional[dict]:
        """Run Evolve retention and return its entity-linked report."""
        args: dict[str, Any] = {"policy": json.dumps(policy), "dry_run": dry_run}
        optional = {
            "as_of": as_of,
            "scan_limit": scan_limit,
            "run_id": run_id,
            "namespace_id": normalize_evolve_identifier(namespace_id),
            "metadata_filters": json.dumps(metadata_filters) if metadata_filters else None,
        }
        args.update({key: value for key, value in optional.items() if value is not None})
        return await cls._call_structured_tool("run_retention", args)

    @classmethod
    async def get_compliance_status(cls, namespace_id: Optional[str] = None) -> Optional[dict]:
        """Return Evolve backend, retention, and protection-hook health."""
        args: dict[str, Any] = {}
        namespace_id = normalize_evolve_identifier(namespace_id)
        if namespace_id:
            args["namespace_id"] = namespace_id
        return await cls._call_structured_tool("get_compliance_status", args)

    @classmethod
    async def _call_structured_tool(
        cls,
        tool_name: str,
        args: dict[str, Any],
    ) -> Optional[dict]:
        if not cls.is_enabled():
            return None
        try:
            result = await cls._call_tool(tool_name, args)
            return result if isinstance(result, dict) else None
        except Exception as e:
            logger.warning(f"Evolve {tool_name} failed (non-fatal): {e}")
            return None

    @staticmethod
    def _convert_messages(chat_messages: List[BaseMessage]) -> list:
        """Convert LangChain BaseMessage list to OpenAI conversation format."""
        result = []
        for i, msg in enumerate(chat_messages):
            if isinstance(msg, HumanMessage):
                role = "user"
            elif isinstance(msg, AIMessage):
                role = "assistant"
            else:
                logger.debug(f"Evolve: Skipping message {i} of type {type(msg).__name__}")
                continue

            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content:
                result.append({"role": role, "content": content})
            else:
                logger.debug(f"Evolve: Skipping empty {role} message {i}")
        logger.debug(f"Evolve: Converted {len(result)}/{len(chat_messages)} messages")
        return result

    @classmethod
    async def _call_tool(cls, tool_name: str, args: dict):
        """Call an Evolve MCP tool via the registry or direct SSE."""
        mode = cls._get_mode()
        registry_enabled = bool(getattr(settings.advanced_features, "registry", False))

        if mode in {"auto", "registry"} and registry_enabled:
            try:
                return await cls._call_tool_via_registry(tool_name, args)
            except Exception as e:
                if mode == "registry":
                    raise
                logger.debug(f"Evolve registry call failed, falling back to direct SSE: {e}")

        if mode in {"auto", "direct"}:
            return await cls._call_tool_direct(tool_name, args)

        raise ValueError(f"Unsupported Evolve mode: {mode}")

    @classmethod
    async def _registry_has_app(cls, app_name: str) -> bool:
        """Check whether the registry currently exposes the configured Evolve app."""
        from cuga.backend.tools_env.registry.utils.api_utils import get_apps

        apps = await get_apps()
        return any(app.name == app_name for app in apps)

    @classmethod
    async def _call_tool_via_registry(cls, tool_name: str, args: dict):
        """Call Evolve through the CUGA MCP registry."""
        from cuga.backend.tools_env.registry.utils.api_utils import get_agent_id, get_registry_base_url

        app_name = cls._get_app_name()
        if not await cls._registry_has_app(app_name):
            raise RuntimeError(f"Evolve app '{app_name}' is not configured in the registry")

        function_name = tool_name if tool_name.startswith(f"{app_name}_") else f"{app_name}_{tool_name}"
        url = f"{get_registry_base_url()}/functions/call"
        agent_id = get_agent_id()
        if agent_id:
            url = f"{url}?agent_id={agent_id}"

        payload = {
            "app_name": app_name,
            "function_name": function_name,
            "args": args,
        }

        timeout_seconds = float(getattr(settings.evolve, "timeout", 30.0))
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers={"accept": "application/json", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as response:
                response_text = await response.text()
                if response.status != 200:
                    raise RuntimeError(
                        f"Evolve registry call failed with status {response.status}: {response_text}"
                    )

                try:
                    return json.loads(response_text)
                except json.JSONDecodeError:
                    return response_text

    @classmethod
    async def _call_tool_direct(cls, tool_name: str, args: dict):
        """Call an Evolve MCP tool via direct FastMCP SSE transport."""
        import asyncio
        from fastmcp import Client
        from fastmcp.client.transports import SSETransport
        from mcp.types import TextContent

        url = settings.evolve.url
        transport = SSETransport(url)

        async with Client(transport) as client:
            try:
                result = await asyncio.wait_for(
                    client.call_tool(tool_name, args), timeout=settings.evolve.timeout
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"Evolve MCP call timed out after {settings.evolve.timeout}s: "
                    f"tool={tool_name}, args={args}"
                )
                return None

            if result is None:
                return None

            if hasattr(result, 'data') and result.data is not None:
                data = result.data
                if isinstance(data, str):
                    try:
                        return json.loads(data)
                    except (json.JSONDecodeError, TypeError):
                        return data
                return data

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
