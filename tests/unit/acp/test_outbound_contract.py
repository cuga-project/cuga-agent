"""Real-pipe contracts for CUGA as an outbound ACP v1 client."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[3]
FAKE_AGENT = ROOT / "tests" / "fixtures" / "acp" / "fake_agent.py"
RAW_AGENT = ROOT / "tests" / "fixtures" / "acp" / "raw_agent.py"


@pytest.fixture(autouse=True)
def _workspace_root(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem import paths

    monkeypatch.setattr(paths, "local_base_dir", lambda: ROOT)


def _config(
    scenario: str = "normal",
    *,
    raw: bool = False,
    env: tuple[str, ...] = (),
    ready_file: Path | None = None,
    **overrides: Any,
) -> Any:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.config import ACPProcessConfig

    script = RAW_AGENT if raw else FAKE_AGENT
    args = (str(script), scenario) if raw else (str(script), "--scenario", scenario)
    if ready_file is not None:
        args = (*args, "--ready-file", str(ready_file))
    values = {
        "command": sys.executable,
        "args": args,
        "cwd": ROOT,
        "env": env,
        "startup_timeout": 2,
        "prompt_timeout": 2,
        "shutdown_grace_period": 0.5,
    }
    values.update(overrides)
    return ACPProcessConfig(**values)


@pytest.mark.asyncio
async def test_complete_lifecycle_accumulates_ordered_text() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import delegate_task_via_acp

    result = await delegate_task_via_acp(config=_config(), task="contract prompt")

    assert result == {"result": "first:contract prompt", "status": "success", "variables": {}}


@pytest.mark.asyncio
async def test_cwd_and_allowlisted_environment_reach_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import delegate_task_via_acp

    monkeypatch.setenv("ACP_ALLOWED_VALUE", "forwarded")
    monkeypatch.setenv("ACP_OMITTED_VALUE", "must-not-cross")
    result = await delegate_task_via_acp(
        config=_config("cwd-env", env=("ACP_ALLOWED_VALUE",)), task="inspect"
    )

    assert result["status"] == "success"
    assert f"cwd={ROOT.resolve()}" in result["result"]
    assert "allowed=forwarded" in result["result"]
    assert "omitted=<missing>" in result["result"]
    assert "must-not-cross" not in result["result"]


@pytest.mark.asyncio
async def test_no_output_and_stderr_diagnostics_do_not_change_results() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import delegate_task_via_acp

    empty, diagnostic = await asyncio.gather(
        delegate_task_via_acp(config=_config("no-output"), task="empty"),
        delegate_task_via_acp(config=_config("stderr"), task="log"),
    )

    assert empty == {
        "result": "ACP agent completed without text output.",
        "status": "success",
        "variables": {},
    }
    assert diagnostic == {"result": "stderr-ok", "status": "success", "variables": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("abrupt", "ACP agent process exited unexpectedly."),
        ("malformed", "ACP agent protocol communication failed."),
        ("startup-timeout", "ACP agent could not be started."),
    ],
)
async def test_startup_and_wire_failures_are_normalized(scenario: str, expected: str) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import delegate_task_via_acp

    timeout = 0.1 if scenario == "startup-timeout" else 2
    result = await delegate_task_via_acp(
        config=_config(scenario, raw=True, startup_timeout=timeout, shutdown_grace_period=0.1),
        task="work",
    )

    assert result == {"result": expected, "status": "failed", "variables": {}}


@pytest.mark.asyncio
async def test_prompt_timeout_cancels_and_reaps_real_process() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    spawned = []

    async def spawn(*args: Any, **kwargs: Any) -> Any:
        process = await asyncio.create_subprocess_exec(*args, **kwargs)
        spawned.append(process)
        return process

    result = await _delegate_task_via_acp(
        config=_config("delay", prompt_timeout=0.1, shutdown_grace_period=0.1),
        task="slow",
        process_factory=spawn,
    )

    assert result == {
        "result": "ACP agent did not respond before the timeout.",
        "status": "failed",
        "variables": {},
    }
    assert len(spawned) == 1
    assert spawned[0].returncode is not None


@pytest.mark.asyncio
async def test_local_cancellation_propagates_after_real_process_is_reaped(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    spawned = []

    async def spawn(*args: Any, **kwargs: Any) -> Any:
        process = await asyncio.create_subprocess_exec(*args, **kwargs)
        spawned.append(process)
        return process

    ready_file = tmp_path / "agent-prompt-ready"
    delegation = asyncio.create_task(
        _delegate_task_via_acp(
            config=_config("delay", ready_file=ready_file, shutdown_grace_period=0.1),
            task="cancel me",
            process_factory=spawn,
        )
    )

    async def wait_until_prompt_is_active() -> None:
        while not ready_file.exists():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait_until_prompt_is_active(), timeout=5)
    delegation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await delegation

    assert spawned[0].returncode is not None


@pytest.mark.asyncio
async def test_concurrent_delegations_use_independent_processes() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import delegate_task_via_acp

    tasks = [delegate_task_via_acp(config=_config(), task=f"task-{index}") for index in range(4)]
    results = await asyncio.gather(*tasks)

    assert [result["result"] for result in results] == [
        "first:task-0",
        "first:task-1",
        "first:task-2",
        "first:task-3",
    ]
    assert all(result["status"] == "success" for result in results)


@pytest.mark.asyncio
async def test_prompt_arguments_are_data_not_shell_syntax(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import delegate_task_via_acp

    marker = tmp_path / "acp-shell-marker"
    task = f"literal ; touch {marker} $(touch {marker}) `touch {marker}`"
    result = await delegate_task_via_acp(config=_config(), task=task)

    assert result["result"] == f"first:{task}"
    assert not marker.exists()
