"""
A2A Protocol - Agent-to-Agent communication.

HTTP transport uses a2a-sdk (A2ACardResolver, A2AClient): fetch agent card from
well-known path, send message with task only (no variables). Other transports
use legacy A2AProtocol.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger

try:
    import httpx
    from a2a.client import A2ACardResolver, A2AClient
    from a2a.types import (
        AgentCard,
        JSONRPCErrorResponse,
        Message,
        MessageSendParams,
        Part,
        Role,
        SendMessageRequest,
        Task,
        TextPart,
    )
    from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
    from a2a.utils.message import get_message_text

    HAS_A2A_SDK = True
except ImportError:
    HAS_A2A_SDK = False
    AgentCard = None
    Task = None
    JSONRPCErrorResponse = None


def _agent_card_description(agent_card: "AgentCard") -> str:
    parts = []
    if getattr(agent_card, "name", None):
        parts.append(str(agent_card.name))
    if getattr(agent_card, "description", None):
        parts.append(str(agent_card.description))
    caps = getattr(agent_card, "capabilities", None)
    if caps:
        parts.append(f"Capabilities: {', '.join(caps)}")
    skills = getattr(agent_card, "skills", None)
    if skills:
        parts.append(f"Skills: {', '.join(skills)}")
    return " ".join(parts) if parts else "A2A agent"


def format_agent_card_for_prompt(agent_card: "AgentCard") -> str:
    """Format agent card as readable text for inclusion in the supervisor prompt."""
    if not HAS_A2A_SDK or agent_card is None:
        return ""
    lines = []
    if getattr(agent_card, "name", None):
        lines.append(f"**Name:** {agent_card.name}")
    if getattr(agent_card, "description", None):
        lines.append(f"**Description:** {agent_card.description}")
    if getattr(agent_card, "version", None):
        lines.append(f"**Version:** {agent_card.version}")
    caps = getattr(agent_card, "capabilities", None)
    if caps is not None:
        if hasattr(caps, "model_dump"):
            d = caps.model_dump(exclude_none=True)
            enabled = [k for k, v in d.items() if v is True]
            if enabled:
                lines.append("**Capabilities:** " + ", ".join(enabled))
        elif isinstance(caps, (list, tuple)):
            lines.append("**Capabilities:** " + ", ".join(str(c) for c in caps))
    skills = getattr(agent_card, "skills", None)
    if skills:
        skill_parts = []
        for s in skills:
            name = getattr(s, "name", None) or getattr(s, "id", "")
            desc = getattr(s, "description", None)
            skill_parts.append(f"{name}: {desc}" if desc else str(name))
        if skill_parts:
            lines.append("**Skills:** " + "; ".join(skill_parts))
    return "\n".join(lines) if lines else "A2A agent (no card details)."


async def fetch_agent_card(
    base_url: str,
    auth: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
) -> "AgentCard":
    if not HAS_A2A_SDK:
        raise ImportError("a2a-sdk is required for A2A HTTP. Install with: uv add a2a-sdk")
    headers = {}
    if auth and auth.get("type") == "bearer" and auth.get("token"):
        headers["Authorization"] = f"Bearer {auth['token']}"
    async with httpx.AsyncClient(timeout=timeout) as httpx_client:
        resolver = A2ACardResolver(
            httpx_client=httpx_client,
            base_url=base_url.rstrip("/"),
            agent_card_path=AGENT_CARD_WELL_KNOWN_PATH,
        )
        card = await resolver.get_agent_card(http_kwargs={"headers": headers} if headers else None)
    if card is None:
        raise RuntimeError(f"Failed to fetch agent card from {base_url}")
    return card


async def delegate_task_via_a2a_sdk(
    agent_card: "AgentCard",
    task: str,
    auth: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    variables: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Delegate by sending a user message. Optionally pass variables in request metadata (extension).
    Returns dict with keys: result (str), variables (dict), status (str).
    """
    if not HAS_A2A_SDK:
        raise ImportError("a2a-sdk is required. Install with: uv add a2a-sdk")
    headers = {}
    if auth and auth.get("type") == "bearer" and auth.get("token"):
        headers["Authorization"] = f"Bearer {auth['token']}"
    message_id = uuid4().hex
    user_msg = Message(
        role=Role.user,
        parts=[Part(root=TextPart(text=task))],
        message_id=message_id,
    )
    metadata = {"variables": variables} if variables else None
    params = MessageSendParams(message=user_msg, metadata=metadata)
    request = SendMessageRequest(id=str(uuid4()), params=params)
    async with httpx.AsyncClient(timeout=timeout) as httpx_client:
        client = A2AClient(httpx_client=httpx_client, agent_card=agent_card)
        response = await client.send_message(
            request,
            http_kwargs={"headers": headers} if headers else None,
        )
    root = getattr(response, "root", response)
    if isinstance(root, JSONRPCErrorResponse) and getattr(root, "error", None):
        raise RuntimeError(f"A2A send_message failed: {root.error}")
    result_obj = getattr(root, "result", None)
    if result_obj is None:
        return {"result": "", "variables": {}, "status": "success"}
    if isinstance(result_obj, Message):
        text = get_message_text(result_obj)
    elif isinstance(result_obj, Task) and result_obj.history:
        texts = [get_message_text(m) for m in result_obj.history if isinstance(m, Message)]
        text = "\n".join(texts) if texts else ""
    else:
        text = str(result_obj) if result_obj else ""
    return {"result": text or "", "variables": {}, "status": "success"}


class A2AProtocol:
    """
    Legacy A2A protocol (non-http or fallback).
    For HTTP transport with a2a-sdk use fetch_agent_card + delegate_task_via_a2a_sdk.
    """

    def __init__(
        self,
        endpoint: str,
        transport: str = "http",
        auth: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ):
        self.endpoint = endpoint
        self.transport = transport.lower()
        self.auth = auth or {}
        self.timeout = timeout
        self._session = None
        self._connection = None
        self._ws = None

    async def connect(self) -> None:
        if self.transport == "http":
            try:
                import aiohttp

                self._session = aiohttp.ClientSession()
                logger.info(f"Created HTTP session for A2A agent at {self.endpoint}")
            except ImportError:
                raise ImportError("aiohttp is required for HTTP transport. Install with: pip install aiohttp")
        elif self.transport == "sse":
            try:
                from cuga.backend.tools_env.registry.mcp_manager.sse_client import sse_client

                self._connection = sse_client(self.endpoint)
                await self._connection.__aenter__()
                logger.info(f"Connected to A2A agent via SSE at {self.endpoint}")
            except ImportError:
                raise ImportError("SSE client not available")
        elif self.transport == "websocket":
            try:
                import websockets

                self._ws = await websockets.connect(self.endpoint)
                logger.info(f"Connected to A2A agent via WebSocket at {self.endpoint}")
            except ImportError:
                raise ImportError(
                    "websockets is required for WebSocket transport. Install with: pip install websockets"
                )
        elif self.transport == "stdio":
            logger.info(f"Using STDIO transport for local agent: {self.endpoint}")
        else:
            raise ValueError(f"Unsupported transport type: {self.transport}")

    async def disconnect(self) -> None:
        if self.transport == "http" and self._session:
            await self._session.close()
            self._session = None
            logger.info("Closed HTTP session")
        elif self.transport == "sse" and self._connection:
            await self._connection.__aexit__(None, None, None)
            self._connection = None
            logger.info("Closed SSE connection")
        elif self.transport == "websocket" and self._ws:
            await self._ws.close()
            self._ws = None
            logger.info("Closed WebSocket connection")

    def _get_auth_headers(self) -> Dict[str, str]:
        headers = {}
        if self.auth.get("type") == "bearer" and self.auth.get("token"):
            headers["Authorization"] = f"Bearer {self.auth['token']}"
        return headers

    async def delegate_task(
        self,
        target_agent: str,
        task: str,
        context: Dict[str, Any],
        variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        message = {
            "protocol_version": "1.0",
            "message_type": "task_delegation",
            "from_agent": "supervisor",
            "to_agent": target_agent,
            "task": task,
            "context": context,
            "variables": variables or {},
        }

        if self.transport == "http":
            import aiohttp

            async with self._session.post(
                f"{self.endpoint}/delegate",
                json=message,
                headers=self._get_auth_headers(),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    error_text = await response.text()
                    raise Exception(f"A2A HTTP request failed with status {response.status}: {error_text}")
        elif self.transport == "sse":
            await self._connection.write(json.dumps(message))
            response = await self._connection.read()
            return json.loads(response)
        elif self.transport == "websocket":
            await self._ws.send(json.dumps(message))
            response = await self._ws.recv()
            return json.loads(response)
        elif self.transport == "stdio":
            logger.warning("STDIO transport for A2A not fully implemented - using mock response")
            return {
                "result": f"Mock response from {target_agent} for task: {task}",
                "variables": {},
                "status": "success",
            }
        else:
            raise ValueError(f"Unsupported transport type: {self.transport}")

    async def share_result(
        self,
        target_agent: str,
        result: Any,
        variables: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        message = {
            "protocol_version": "1.0",
            "message_type": "result_sharing",
            "from_agent": "supervisor",
            "to_agent": target_agent,
            "result": result,
            "variables": variables or {},
            "metadata": metadata or {},
        }

        if self.transport == "http":
            import aiohttp

            async with self._session.post(
                f"{self.endpoint}/share",
                json=message,
                headers=self._get_auth_headers(),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Failed to share result: {error_text}")
        elif self.transport == "sse":
            await self._connection.write(json.dumps(message))
        elif self.transport == "websocket":
            await self._ws.send(json.dumps(message))
        elif self.transport == "stdio":
            logger.warning("STDIO transport for result sharing not fully implemented")

    async def discover_capabilities(self, agent_name: str) -> List[str]:
        message = {
            "protocol_version": "1.0",
            "message_type": "capability_query",
            "from_agent": "supervisor",
            "to_agent": agent_name,
        }

        if self.transport == "http":
            import aiohttp

            async with self._session.post(
                f"{self.endpoint}/capabilities",
                json=message,
                headers=self._get_auth_headers(),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get("capabilities", [])
                else:
                    logger.warning(f"Failed to discover capabilities: {response.status}")
                    return []
        elif self.transport == "sse":
            await self._connection.write(json.dumps(message))
            response = await self._connection.read()
            result = json.loads(response)
            return result.get("capabilities", [])
        elif self.transport == "websocket":
            await self._ws.send(json.dumps(message))
            response = await self._ws.recv()
            result = json.loads(response)
            return result.get("capabilities", [])
        elif self.transport == "stdio":
            logger.warning("STDIO transport for capability discovery not fully implemented")
            return []

    async def get_agent_status(self, agent_name: str) -> Dict[str, Any]:
        message = {
            "protocol_version": "1.0",
            "message_type": "status_query",
            "from_agent": "supervisor",
            "to_agent": agent_name,
        }

        if self.transport == "http":
            import aiohttp

            async with self._session.post(
                f"{self.endpoint}/status",
                json=message,
                headers=self._get_auth_headers(),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.warning(f"Failed to get agent status: {response.status}")
                    return {"status": "unknown"}
        elif self.transport == "sse":
            await self._connection.write(json.dumps(message))
            response = await self._connection.read()
            return json.loads(response)
        elif self.transport == "websocket":
            await self._ws.send(json.dumps(message))
            response = await self._ws.recv()
            return json.loads(response)
        elif self.transport == "stdio":
            logger.warning("STDIO transport for status query not fully implemented")
            return {"status": "available"}
