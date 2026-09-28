"""Deterministic official-SDK ACP agent executable for contract tests."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

from acp import PROTOCOL_VERSION, RequestError, run_agent
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AllowedOutcome,
    Implementation,
    McpCapabilities,
    NewSessionResponse,
    PermissionOption,
    PromptCapabilities,
    PromptResponse,
    SessionCapabilities,
    TextContentBlock,
    ToolCallUpdate,
)


class FakeAgent:
    """Small ACP v1 agent whose behavior is selected on the command line."""

    def __init__(self, scenario: str, ready_file: str | None = None) -> None:
        self.scenario = scenario
        self.ready_file = ready_file
        self.client: Any | None = None
        self.sessions: set[str] = set()
        self.cancelled: set[str] = set()

    def on_connect(self, client: Any) -> None:
        self.client = client

    async def initialize(self, protocol_version: int, **_kwargs: Any) -> Any:
        if protocol_version != PROTOCOL_VERSION:
            raise RequestError.invalid_params()
        from acp.schema import InitializeResponse

        return InitializeResponse(
            protocolVersion=PROTOCOL_VERSION,
            agentCapabilities=AgentCapabilities(
                loadSession=False,
                promptCapabilities=PromptCapabilities(image=False, audio=False, embeddedContext=False),
                mcpCapabilities=McpCapabilities(http=False, sse=False, acp=False),
                sessionCapabilities=SessionCapabilities(),
            ),
            authMethods=[],
            agentInfo=Implementation(name="cuga-test-agent", version="1"),
        )

    async def new_session(self, cwd: str, **_kwargs: Any) -> NewSessionResponse:
        session_id = uuid4().hex
        self.sessions.add(session_id)
        if self.scenario == "cwd-env":
            os.environ["ACP_FIXTURE_CWD"] = str(Path(cwd).resolve())
        return NewSessionResponse(sessionId=session_id)

    async def prompt(self, session_id: str, prompt: list[Any], **_kwargs: Any) -> PromptResponse:
        if session_id not in self.sessions:
            raise RequestError.resource_not_found()
        text = "\n\n".join(block.text for block in prompt if isinstance(block, TextContentBlock))
        if self.scenario == "delay":
            if self.ready_file is not None:
                Path(self.ready_file).write_text("ready", encoding="utf-8")
            await asyncio.sleep(60)
        elif self.scenario == "no-output":
            pass
        elif self.scenario == "stderr":
            print("bounded fake-agent diagnostic", file=sys.stderr, flush=True)
            await self._emit(session_id, "stderr-ok")
        elif self.scenario == "cwd-env":
            forwarded = os.environ.get("ACP_ALLOWED_VALUE", "<missing>")
            omitted = os.environ.get("ACP_OMITTED_VALUE", "<missing>")
            cwd = os.environ.get("ACP_FIXTURE_CWD", "<missing>")
            await self._emit(session_id, f"cwd={cwd};allowed={forwarded};omitted={omitted}")
        elif self.scenario == "permission":
            selected = await self._request_permission(session_id, "fixture-operation")
            await self._emit(session_id, "permission-allowed" if selected else "permission-denied")
        elif self.scenario == "permission-twice":
            first = await self._request_permission(session_id, "fixture-operation-1")
            second = await self._request_permission(session_id, "fixture-operation-2")
            await self._emit(session_id, f"first={'allowed' if first else 'denied'};")
            await self._emit(session_id, f"second={'allowed' if second else 'denied'}")
        else:
            await self._emit(session_id, "first:")
            await self._emit(session_id, text)
        return PromptResponse(stopReason="cancelled" if session_id in self.cancelled else "end_turn")

    async def cancel(self, session_id: str, **_kwargs: Any) -> None:
        self.cancelled.add(session_id)

    async def _request_permission(self, session_id: str, tool_call_id: str) -> bool:
        if self.client is None:
            raise RequestError.internal_error()
        response = await self.client.request_permission(
            session_id,
            ToolCallUpdate(
                toolCallId=tool_call_id,
                kind="execute",
                status="pending",
                title="Run deterministic fixture operation",
            ),
            [
                PermissionOption(optionId="allow-once", name="Allow once", kind="allow_once"),
                PermissionOption(optionId="reject-once", name="Reject once", kind="reject_once"),
            ],
        )
        return isinstance(response.outcome, AllowedOutcome) and response.outcome.option_id == "allow-once"

    async def _emit(self, session_id: str, text: str) -> None:
        if self.client is None:
            raise RequestError.internal_error()
        await self.client.session_update(
            session_id,
            AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=TextContentBlock(type="text", text=text),
            ),
        )

    async def load_session(self, **_kwargs: Any) -> None:
        raise RequestError.method_not_found("session/load")

    async def list_sessions(self, **_kwargs: Any) -> None:
        raise RequestError.method_not_found("session/list")

    async def set_session_mode(self, **_kwargs: Any) -> None:
        raise RequestError.method_not_found("session/set_mode")

    async def set_config_option(self, **_kwargs: Any) -> None:
        raise RequestError.method_not_found("session/set_config_option")

    async def authenticate(self, **_kwargs: Any) -> None:
        raise RequestError.method_not_found("authenticate")

    async def fork_session(self, **_kwargs: Any) -> None:
        raise RequestError.method_not_found("session/fork")

    async def resume_session(self, **_kwargs: Any) -> None:
        raise RequestError.method_not_found("session/resume")

    async def close_session(self, **_kwargs: Any) -> None:
        raise RequestError.method_not_found("session/close")

    async def ext_method(self, method: str, _params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError.method_not_found(f"_{method}")

    async def ext_notification(self, _method: str, _params: dict[str, Any]) -> None:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        choices=("normal", "no-output", "permission", "permission-twice", "delay", "stderr", "cwd-env"),
        default="normal",
    )
    parser.add_argument("--ready-file")
    args = parser.parse_args()
    asyncio.run(
        run_agent(
            FakeAgent(args.scenario, ready_file=args.ready_file),
            use_unstable_protocol=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
