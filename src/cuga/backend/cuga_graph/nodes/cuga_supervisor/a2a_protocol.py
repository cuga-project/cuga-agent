"""
A2A Protocol - Agent-to-Agent communication.

HTTP transport speaks A2A v0.3 JSON-RPC directly to the peer's ``/a2a``
endpoint. We pull pydantic types from ``a2a.compat.v0_3.types`` (the
installed ``a2a-sdk`` 1.x ships v0.3 wire shapes there) and use the
SDK's ``A2ACardResolver`` for the well-known agent-card lookup. Other
transports (sse/websocket/stdio) still use the legacy ``A2AProtocol``
class below.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger

try:
    import httpx
    from a2a.client import A2ACardResolver
    from a2a.compat.v0_3.types import (
        AgentCard,
        Task,
    )
    from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

    HAS_A2A_SDK = True
except ImportError:
    HAS_A2A_SDK = False
    AgentCard = None
    Task = None


def _extract_text_from_task(task_dict: Dict[str, Any]) -> str:
    """Pull human-readable text from a v0.3 Task envelope.

    Prefers the most recent agent message in ``history`` since A2A returns
    the full conversation; falls back to the status message, then to the
    raw JSON if all else fails.
    """
    history = task_dict.get("history") or []
    for msg in reversed(history):
        if msg.get("role") != "agent":
            continue
        for part in msg.get("parts") or []:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str) and text:
                return text
    status = task_dict.get("status") or {}
    status_msg = status.get("message") or {}
    for part in status_msg.get("parts") or []:
        text = part.get("text") if isinstance(part, dict) else None
        if isinstance(text, str) and text:
            return text
    return ""


def _agent_card_description(agent_card: "AgentCard") -> str:
    parts = []
    if getattr(agent_card, "name", None):
        parts.append(str(agent_card.name))
    if getattr(agent_card, "description", None):
        parts.append(str(agent_card.description))
    caps = getattr(agent_card, "capabilities", None)
    if caps is not None:
        if hasattr(caps, "model_dump"):
            d = caps.model_dump(exclude_none=True)
            enabled = [k for k, v in d.items() if v is True]
            if enabled:
                parts.append(f"Capabilities: {', '.join(enabled)}")
        elif isinstance(caps, (list, tuple)):
            parts.append(f"Capabilities: {', '.join(str(c) for c in caps)}")
    skills = getattr(agent_card, "skills", None)
    if skills:
        skill_names = [getattr(s, "name", None) or getattr(s, "id", None) or str(s) for s in skills]
        parts.append(f"Skills: {', '.join(skill_names)}")
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


def _agent_card_rpc_url(agent_card: "AgentCard", fallback_base: Optional[str] = None) -> str:
    """Pick the JSON-RPC endpoint to POST to.

    The v0.3 AgentCard's ``url`` field points at the agent's RPC endpoint.
    Some servers advertise the base host instead; in that case we append
    ``/a2a`` (the convention used by CUGA's own router). If neither is
    usable, fall back to the caller-supplied base.
    """
    url = (getattr(agent_card, "url", None) or fallback_base or "").rstrip("/")
    if not url:
        raise RuntimeError("Agent card carries no URL and no fallback was supplied.")
    if url.endswith("/a2a"):
        return url
    # Heuristic: an agent card usually points at the agent's chat URL,
    # not the JSON-RPC endpoint. Append /a2a if it doesn't already end in
    # one of the standard transport paths.
    return url if any(url.endswith(p) for p in ("/jsonrpc", "/rpc")) else f"{url}/a2a"


async def delegate_task_via_a2a_sdk(
    agent_card: "AgentCard",
    task: str,
    auth: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    variables: Optional[Dict[str, Any]] = None,
    rpc_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Delegate a task to a remote A2A agent over v0.3 JSON-RPC.

    Returns dict with keys: ``result`` (str), ``variables`` (dict),
    ``status`` (str). When ``variables`` is provided, it is forwarded in
    request metadata as the A2A ``variables`` extension.

    ``rpc_url`` overrides the URL discovered from the agent card; mostly
    useful when callers already know the endpoint and want to skip the
    inference step.
    """
    if not HAS_A2A_SDK:
        raise ImportError("a2a-sdk is required. Install with: uv add a2a-sdk")

    target_url = rpc_url or _agent_card_rpc_url(agent_card)

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if auth and auth.get("type") == "bearer" and auth.get("token"):
        headers["Authorization"] = f"Bearer {auth['token']}"

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": task}],
                "messageId": uuid4().hex,
            }
        },
    }
    if variables:
        payload["params"]["metadata"] = {"variables": variables}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(target_url, json=payload, headers=headers)
        resp.raise_for_status()
        body = resp.json()

    err = body.get("error") if isinstance(body, dict) else None
    if err:
        raise RuntimeError(f"A2A send_message failed: {err}")

    result_obj = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result_obj, dict):
        return {"result": "", "variables": {}, "status": "success"}

    text = _extract_text_from_task(result_obj)
    return {"result": text, "variables": {}, "status": "success"}


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
