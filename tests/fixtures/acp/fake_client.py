"""Official-SDK client helper for exercising CUGA's inbound stdio entry point."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import os
from pathlib import Path
import sys
from typing import Any

from acp import spawn_agent_process
from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse


_RUNNER_SCRIPT = r'''
import asyncio
import logging
import os
from cuga.backend.server.acp.stdio import main
from cuga.backend.server.agent_protocol.events import AgentStreamEvent

class FixtureRunner:
    def __init__(self):
        self.turns = {}

    async def run(self, message, context_id=None, approval=None):
        scenario = os.environ.get("CUGA_ACP_FIXTURE_SCENARIO", "context")
        if scenario == "delay":
            yield AgentStreamEvent("agent_message", {"text": "started"})
            await asyncio.sleep(60)
        if scenario == "failure":
            raise RuntimeError("private fixture failure")
        if scenario == "noise":
            secret = os.environ.get("CUGA_ACP_FIXTURE_SECRET", "")
            print(secret)
            logging.getLogger("third.party").exception(secret)
        if scenario == "permission" and approval is None:
            yield AgentStreamEvent(
                "input_required",
                {"action_id": "fixture-action", "text": "Run fixture operation"},
            )
            return
        if scenario == "empty":
            return
        if scenario == "permission":
            text = "permission-allowed" if approval.get("confirmed") else "permission-denied"
            yield AgentStreamEvent("final_answer", {"text": text}, final=True)
            return
        count = self.turns.get(context_id, 0) + 1
        self.turns[context_id] = count
        yield AgentStreamEvent("agent_message", {"text": "first:"})
        yield AgentStreamEvent("final_answer", {"text": f"turn-{count}:{message}"}, final=True)

raise SystemExit(main(runner=FixtureRunner()))
'''


class FakeClient:
    """Capture updates and resolve permissions with a configured decision."""

    def __init__(self, permission: str = "allow") -> None:
        self.permission = permission
        self.updates: dict[str, list[Any]] = defaultdict(list)
        self.permission_requests: list[tuple[str, Any, list[Any]]] = []
        self._update_events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)

    async def session_update(self, session_id: str, update: Any, **_kwargs: Any) -> None:
        self.updates[session_id].append(update)
        self._update_events[session_id].set()

    async def request_permission(
        self, session_id: str, tool_call: Any, options: list[Any], **_kwargs: Any
    ) -> RequestPermissionResponse:
        self.permission_requests.append((session_id, tool_call, options))
        wanted = "allow_once" if self.permission == "allow" else "reject_once"
        if self.permission == "cancel":
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        option = next(item for item in options if item.kind == wanted)
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", optionId=option.option_id)
        )

    def text(self, session_id: str) -> str:
        return "".join(
            update.content.text
            for update in self.updates[session_id]
            if getattr(update, "session_update", None) == "agent_message_chunk"
        )

    async def wait_for_update(self, session_id: str, *, timeout: float = 5) -> None:
        """Wait for an agent update without relying on a scheduler timing guess."""
        await asyncio.wait_for(self._update_events[session_id].wait(), timeout=timeout)


class BoundedConnection:
    """Apply one deterministic deadline to every SDK request used by tests."""

    def __init__(self, connection: Any, timeout: float) -> None:
        self._connection = connection
        self._timeout = timeout

    async def initialize(self, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.wait_for(self._connection.initialize(*args, **kwargs), timeout=self._timeout)

    async def new_session(self, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.wait_for(self._connection.new_session(*args, **kwargs), timeout=self._timeout)

    async def prompt(self, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.wait_for(self._connection.prompt(*args, **kwargs), timeout=self._timeout)

    async def cancel(self, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.wait_for(self._connection.cancel(*args, **kwargs), timeout=self._timeout)


def cuga_agent_launch(scenario: str) -> tuple[tuple[str, ...], dict[str, str], Path]:
    """Return the deterministic CUGA fixture command, environment, and cwd."""
    root = Path(__file__).parents[3]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    env["CUGA_ACP_FIXTURE_SCENARIO"] = scenario
    return (sys.executable, "-c", _RUNNER_SCRIPT), env, root


@asynccontextmanager
async def spawn_cuga_agent(
    *,
    scenario: str = "context",
    permission: str = "allow",
    observers: list[Any] | None = None,
    receive_timeout: float = 10,
) -> AsyncIterator[tuple[FakeClient, Any, Any]]:
    """Launch the real CUGA stdio adapter with a deterministic injected runner."""
    command, env, root = cuga_agent_launch(scenario)
    client = FakeClient(permission=permission)
    async with spawn_agent_process(
        client,
        *command,
        env=env,
        cwd=root,
        observers=observers or [],
        receive_timeout=receive_timeout,
        transport_kwargs={"shutdown_timeout": 2},
    ) as (connection, process):
        yield client, BoundedConnection(connection, receive_timeout), process
