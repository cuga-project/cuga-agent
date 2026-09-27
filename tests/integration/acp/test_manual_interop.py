"""Opt-in interoperability checks for third-party ACP clients and agents."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.manual

ROOT = Path(__file__).parents[3]


def _command(name: str) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        pytest.skip(f"set {name} to a JSON command array to run this manual check")
    try:
        command = json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(f"{name} must be a JSON command array: {exc}")
    if not isinstance(command, list) or not command or any(not isinstance(item, str) for item in command):
        pytest.fail(f"{name} must be a non-empty JSON array of strings")
    return command


def _agent_config(command: list[str]) -> Any:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.config import ACPProcessConfig

    return ACPProcessConfig(
        command=command[0],
        args=tuple(command[1:]),
        cwd=ROOT,
        env=tuple(filter(None, os.environ.get("CUGA_ACP_INTEROP_ENV", "").split(","))),
        startup_timeout=30,
        prompt_timeout=180,
        shutdown_grace_period=5,
    )


@pytest.mark.asyncio
async def test_established_agent_prompt_permission_and_cancellation() -> None:
    """Drive an installed ACP coding agent; permission is approved when requested."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import delegate_task_via_acp

    command = _command("CUGA_ACP_INTEROP_AGENT_COMMAND")

    async def allow_once(request: Any) -> str | None:
        option = next((item for item in request.options if item.kind == "allow_once"), None)
        return option.option_id if option is not None else None

    normal = await delegate_task_via_acp(
        config=_agent_config(command),
        task=os.environ.get("CUGA_ACP_INTEROP_PROMPT", "Reply with: ACP interoperability OK"),
        permission_handler=allow_once,
    )
    assert normal["status"] == "success", normal
    assert normal["result"]

    cancellation = asyncio.create_task(
        delegate_task_via_acp(
            config=_agent_config(command),
            task=os.environ.get(
                "CUGA_ACP_INTEROP_CANCEL_PROMPT",
                "Wait for 30 seconds before replying so the client can cancel.",
            ),
            permission_handler=allow_once,
        )
    )
    await asyncio.sleep(1)
    cancellation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancellation


@pytest.mark.asyncio
async def test_established_client_can_launch_cuga_acp() -> None:
    """Run a configured client-side smoke script that launches ``cuga-acp``."""
    command = _command("CUGA_ACP_INTEROP_CLIENT_COMMAND")
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=ROOT,
        env=os.environ.copy(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
    except (TimeoutError, asyncio.TimeoutError):
        process.kill()
        await process.wait()
        pytest.fail("configured ACP client did not finish within 180 seconds")
    assert process.returncode == 0, stderr.decode(errors="replace")[:2000]
    assert len(stdout) <= 1024 * 1024
