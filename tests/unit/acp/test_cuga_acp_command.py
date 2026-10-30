"""`cuga acp` routing and the ACP Registry's launch contract."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_ROOT = Path(__file__).parents[3]
_SECRET_MARKERS = ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "WATSONX")

_ROUTING_PROBE = """
import sys
import cuga.backend.server.acp.stdio as stdio

def fake_main(runner=None):
    print("cli_main_imported=" + str("cuga.cli.main" in sys.modules))
    return 7

stdio.main = fake_main
sys.argv = ["cuga", "acp"]
from cuga.cli_entry import main
main()
"""
_CUGA = "import sys; sys.argv = ['cuga', *sys.argv[1:]]; from cuga.cli_entry import main; main()"


def _env() -> dict[str, str]:
    """The registry launches agents with no credentials; mirror that."""
    env = {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in _SECRET_MARKERS)}
    env["PYTHONPATH"] = str(_ROOT / "src")
    return env


async def test_cuga_acp_runs_the_stdio_server_without_importing_the_typer_cli() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _ROUTING_PROBE],
        env=_env(),
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 7
    assert "cli_main_imported=False" in completed.stdout


async def test_other_cuga_commands_still_reach_the_typer_cli() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _CUGA, "--help"],
        env=_env(),
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0
    assert "Usage" in completed.stdout
    assert "start" in completed.stdout


async def test_cuga_acp_answers_registry_initialize_with_an_auth_method(tmp_path: Path) -> None:
    # Same request the ACP Registry CI sends; it reads only the first stdout line.
    request = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": 1,
            "clientCapabilities": {
                "terminal": True,
                "fs": {"readTextFile": True, "writeTextFile": True},
                "_meta": {"terminal_output": True, "terminal-auth": True},
            },
        },
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CUGA,
        "acp",
        env=_env(),
        cwd=tmp_path,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()
        first_line = await asyncio.wait_for(process.stdout.readline(), timeout=60)
    finally:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except (TimeoutError, asyncio.TimeoutError):
            process.kill()
            await process.wait()

    frame = json.loads(first_line)
    assert frame["id"] == 0
    methods = frame["result"]["authMethods"]
    # The registry accepts "agent" or "terminal"; a method without a type is "agent".
    assert any(method.get("type", "agent") in {"agent", "terminal"} for method in methods)
