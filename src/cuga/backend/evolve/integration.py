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
        if not settings.evolve.enabled:
            return False
        if settings.evolve.lite_mode_only and not settings.advanced_features.lite_mode:
            return False
        return True

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
        """Fetch formatted guidelines and the entity ids included in the prompt."""
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
                    str(entity_id)
                    for entity_id in result.get("entity_ids", [])
                    if str(entity_id).strip()
                ],
                "namespace_id": result.get("namespace_id"),
            }
        except Exception as e:
            logger.warning(f"Evolve attributed guideline retrieval failed (non-fatal): {e}")
            return None

    @classmethod
    async def get_entities(
        cls,
        task: str,
        entity_type: str = "guideline",
        include_public: bool = False,
        limit: int = 10,
        user_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[str]:
        """Fetch task-relevant entities through Evolve's generic retrieval tool."""
        if not cls.is_enabled():
            return None
        try:
            args: dict[str, Any] = {
                "task": task,
                "entity_type": entity_type,
                "include_public": include_public,
                "limit": limit,
            }
            for key, value in {
                "user_id": normalize_evolve_identifier(user_id),
                "namespace_id": normalize_evolve_identifier(namespace_id),
                "session_id": normalize_evolve_identifier(session_id),
            }.items():
                if value:
                    args[key] = value
            result = await cls._call_tool("get_entities", args)
            return str(result) if result else None
        except Exception as e:
            logger.warning(f"Evolve get_entities failed (non-fatal): {e}")
            return None

    @classmethod
    async def get_relevant_guidelines(
        cls,
        task: str,
        top_k: Optional[int] = None,
        core_support: Optional[int] = None,
        user_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[str]:
        """Fetch Evolve's dosage-aware core plus task-relevant guidelines."""
        if not cls.is_enabled():
            return None
        try:
            args: dict[str, Any] = {"task": task}
            for key, value in {
                "top_k": top_k,
                "core_support": core_support,
                "user_id": normalize_evolve_identifier(user_id),
                "namespace_id": normalize_evolve_identifier(namespace_id),
                "session_id": normalize_evolve_identifier(session_id),
            }.items():
                if value is not None:
                    args[key] = value
            result = await cls._call_tool("get_relevant_guidelines", args)
            return str(result) if result else None
        except Exception as e:
            logger.warning(f"Evolve get_relevant_guidelines failed (non-fatal): {e}")
            return None

    @classmethod
    async def store_user_facts(
        cls,
        user_id: str,
        message: str,
        metadata: dict | None = None,
        enable_conflict_resolution: bool = False,
    ) -> Optional[dict]:
        """Store durable user facts/preferences without interrupting lite execution."""
        if not cls.is_enabled():
            return None
        if not user_id or not message:
            return None

        try:
            payload = {
                "user_id": user_id,
                "message": message,
                "metadata": json.dumps(metadata or {}),
                "enable_conflict_resolution": enable_conflict_resolution,
            }
            result = await cls._call_tool("store_user_facts", payload)
            if isinstance(result, dict):
                logger.info(
                    "Evolve: Stored user facts (stored_count=%s)",
                    result.get("stored_count", 0),
                )
                return result
            return None
        except Exception as e:
            logger.warning(f"Evolve store_user_facts failed (non-fatal): {e}")
            return None

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
        task_id: Optional[str],
        success: bool,
        user_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> Optional[list[dict[str, Any]]]:
        """Save the agent trajectory to Evolve for tip generation."""
        if not cls.is_enabled():
            return None
        if success and not settings.evolve.save_on_success:
            return None
        if not success and not settings.evolve.save_on_failure:
            return None

        try:
            user_id = normalize_evolve_identifier(user_id)
            namespace_id = normalize_evolve_identifier(namespace_id)
            session_id = normalize_evolve_identifier(session_id)
            owner_id = normalize_evolve_identifier(owner_id)
            logger.debug(
                f"Evolve: Converting {len(chat_messages)} chat_messages. "
                f"Types: {[type(m).__name__ for m in chat_messages[:10]]}"
            )
            openai_messages = cls._convert_messages(chat_messages)
            if not openai_messages:
                logger.warning("Evolve: No messages to save (empty trajectory)")
                return None

            trajectory_json = json.dumps(openai_messages)
            logger.info(
                f"Evolve: Saving trajectory ({len(openai_messages)} messages, "
                f"{len(trajectory_json)} chars, "
                f"task_id={(task_id or 'generated')[:80]}, success={success})"
            )
            logger.debug(f"Evolve: trajectory_data preview: {trajectory_json[:500]}")
            args: dict[str, Any] = {"trajectory_data": trajectory_json}
            if task_id:
                args["task_id"] = task_id
            if owner_id:
                args["owner_id"] = owner_id
            if user_id:
                args["user_id"] = user_id
            if namespace_id:
                args["namespace_id"] = namespace_id
            if session_id:
                args["session_id"] = session_id
            if tools is not None:
                args["tools"] = json.dumps(tools)
            result = await cls._call_tool("save_trajectory", args)
            logger.info("Evolve: Trajectory saved successfully")
            return result if isinstance(result, list) else None
        except Exception as e:
            logger.warning(f"Evolve save_trajectory failed (non-fatal): {e}")
            return None

    @classmethod
    async def create_entity(
        cls,
        content: str,
        entity_type: str,
        metadata: Optional[dict[str, Any]] = None,
        enable_conflict_resolution: bool = False,
        owner_id: Optional[str] = None,
        visibility: str = "private",
        namespace_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Optional[dict]:
        """Create an Evolve entity and return its structured update result."""
        if not cls.is_enabled() or not content or not entity_type:
            return None
        try:
            args: dict[str, Any] = {
                "content": content,
                "entity_type": entity_type,
                "metadata": json.dumps(metadata or {}),
                "enable_conflict_resolution": enable_conflict_resolution,
                "visibility": visibility,
            }
            if created_at:
                args["created_at"] = created_at
            owner_id = normalize_evolve_identifier(owner_id)
            namespace_id = normalize_evolve_identifier(namespace_id)
            if owner_id:
                args["owner_id"] = owner_id
            if namespace_id:
                args["namespace_id"] = namespace_id
            result = await cls._call_tool("create_entity", args)
            return result if isinstance(result, dict) else None
        except Exception as e:
            logger.warning(f"Evolve create_entity failed (non-fatal): {e}")
            return None

    @classmethod
    async def publish_entity(
        cls,
        entity_id: str,
        user_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Publish an owned Evolve entity."""
        return await cls._change_entity_visibility("publish_entity", entity_id, user_id, namespace_id)

    @classmethod
    async def unpublish_entity(
        cls,
        entity_id: str,
        user_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Return an owned Evolve entity to private visibility."""
        return await cls._change_entity_visibility("unpublish_entity", entity_id, user_id, namespace_id)

    @classmethod
    async def _change_entity_visibility(
        cls,
        tool_name: str,
        entity_id: str,
        user_id: Optional[str],
        namespace_id: Optional[str],
    ) -> Optional[dict]:
        if not cls.is_enabled() or not entity_id:
            return None
        try:
            args: dict[str, Any] = {"entity_id": entity_id}
            user_id = normalize_evolve_identifier(user_id)
            namespace_id = normalize_evolve_identifier(namespace_id)
            if user_id:
                args["user_id"] = user_id
            if namespace_id:
                args["namespace_id"] = namespace_id
            result = await cls._call_tool(tool_name, args)
            return result if isinstance(result, dict) else None
        except Exception as e:
            logger.warning(f"Evolve {tool_name} failed (non-fatal): {e}")
            return None

    @classmethod
    async def delete_entity(
        cls,
        entity_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Delete an owned Evolve entity through its MCP authorization checks."""
        if not cls.is_enabled() or not entity_id:
            return None
        try:
            args: dict[str, Any] = {"entity_id": entity_id}
            user_id = normalize_evolve_identifier(user_id)
            agent_id = normalize_evolve_identifier(agent_id)
            namespace_id = normalize_evolve_identifier(namespace_id)
            if user_id:
                args["user_id"] = user_id
            if agent_id:
                args["agent_id"] = agent_id
            if namespace_id:
                args["namespace_id"] = namespace_id
            result = await cls._call_tool("delete_entity", args)
            return result if isinstance(result, dict) else None
        except Exception as e:
            logger.warning(f"Evolve delete_entity failed (non-fatal): {e}")
            return None

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
        record_access: bool = False,
        namespace_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Return structured Evolve inventory for the Memory UI."""
        args: dict[str, Any] = {
            "limit": limit,
            "include_content": include_content,
            "record_access": record_access,
        }
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
        record_access: bool = True,
        namespace_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Return one Evolve entity for a Memory UI detail pane."""
        args: dict[str, Any] = {"entity_id": entity_id, "record_access": record_access}
        user_id = normalize_evolve_identifier(user_id)
        agent_id = normalize_evolve_identifier(agent_id)
        namespace_id = normalize_evolve_identifier(namespace_id)
        if user_id:
            args["user_id"] = user_id
        if agent_id:
            args["agent_id"] = agent_id
        if namespace_id:
            args["namespace_id"] = namespace_id
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
        """Patch Evolve entity metadata through its compliance hook seam."""
        args: dict[str, Any] = {
            "entity_id": entity_id,
            "metadata_patch": json.dumps(metadata_patch),
        }
        user_id = normalize_evolve_identifier(user_id)
        agent_id = normalize_evolve_identifier(agent_id)
        namespace_id = normalize_evolve_identifier(namespace_id)
        if user_id:
            args["user_id"] = user_id
        if agent_id:
            args["agent_id"] = agent_id
        if namespace_id:
            args["namespace_id"] = namespace_id
        return await cls._call_structured_tool("patch_entity_metadata", args)

    @classmethod
    async def record_access(
        cls,
        entity_ids: list[str],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Explicitly record use of Evolve entities for unused-retention rules."""
        args: dict[str, Any] = {"entity_ids": entity_ids}
        user_id = normalize_evolve_identifier(user_id)
        agent_id = normalize_evolve_identifier(agent_id)
        namespace_id = normalize_evolve_identifier(namespace_id)
        if user_id:
            args["user_id"] = user_id
        if agent_id:
            args["agent_id"] = agent_id
        if namespace_id:
            args["namespace_id"] = namespace_id
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
        dry_run: bool = True,
        as_of: Optional[str] = None,
        scan_limit: Optional[int] = None,
        run_id: Optional[str] = None,
        namespace_id: Optional[str] = None,
        metadata_filters: Optional[dict[str, Any]] = None,
    ) -> Optional[dict]:
        """Run Evolve retention and return its entity-linked report."""
        args: dict[str, Any] = {
            "policy": json.dumps(policy),
            "dry_run": dry_run,
        }
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
    async def _call_structured_tool(cls, tool_name: str, args: dict[str, Any]) -> Optional[dict]:
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
