"""In-process record of MCP Apps interactions waiting for the user.

The chat UI only ever names an ``interaction_id``. Which server, resource and
tools that interaction may reach, and what the app's tools returned, are kept
here on the server, so a browser cannot point an app at another server or
forge the result the agent receives.

The record lives in memory: after a server restart a pending form cannot be
continued (the user starts the task again).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

_MAX_INTERACTIONS = 512
_TTL_SECONDS = 6 * 60 * 60


@dataclass
class Interaction:
    interaction_id: str
    thread_id: Optional[str]
    user_id: Optional[str]
    agent_id: Optional[str]
    server: str
    tool: str
    agent_tool: str
    resource_uri: str
    tool_input: dict[str, Any]
    tool_result: dict[str, Any]
    status: str = "pending"  # pending | completed
    app_results: list[dict[str, Any]] = field(default_factory=list)
    model_context: Optional[dict[str, Any]] = None
    created: float = field(default_factory=time.time)


class InteractionStore:
    def __init__(self) -> None:
        self._items: dict[str, Interaction] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        thread_id: Optional[str],
        user_id: Optional[str],
        agent_id: Optional[str],
        server: str,
        tool: str,
        agent_tool: str,
        resource_uri: str,
        tool_input: dict[str, Any],
        tool_result: dict[str, Any],
    ) -> Interaction:
        item = Interaction(
            interaction_id=uuid.uuid4().hex,
            thread_id=thread_id,
            user_id=user_id,
            agent_id=agent_id,
            server=server,
            tool=tool,
            agent_tool=agent_tool,
            resource_uri=resource_uri,
            tool_input=tool_input,
            tool_result=tool_result,
        )
        with self._lock:
            self._prune()
            self._items[item.interaction_id] = item
        return item

    def get(self, interaction_id: str) -> Optional[Interaction]:
        with self._lock:
            return self._items.get(interaction_id)

    def record_app_result(self, interaction_id: str, tool: str, arguments: dict, result: dict) -> None:
        with self._lock:
            item = self._items.get(interaction_id)
            if item is not None:
                item.app_results.append({"tool": tool, "arguments": arguments, "result": result})

    def set_model_context(self, interaction_id: str, context: dict[str, Any]) -> None:
        with self._lock:
            item = self._items.get(interaction_id)
            if item is not None:
                item.model_context = context

    def complete(self, interaction_id: str) -> Optional[Interaction]:
        with self._lock:
            item = self._items.get(interaction_id)
            if item is None or item.status != "pending":
                return None
            item.status = "completed"
            return item

    def _prune(self) -> None:
        cutoff = time.time() - _TTL_SECONDS
        for key in [k for k, v in self._items.items() if v.created < cutoff]:
            del self._items[key]
        while len(self._items) >= _MAX_INTERACTIONS:
            del self._items[next(iter(self._items))]


interaction_store = InteractionStore()
