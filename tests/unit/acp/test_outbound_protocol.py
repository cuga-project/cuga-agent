"""Unit coverage for outbound ACP subprocess delegation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    PermissionOption,
    TextContentBlock,
    ToolCallUpdate,
)

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


def _config(tmp_path: Path, **overrides: Any):
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.config import ACPProcessConfig

    values = {
        "command": "external-agent",
        "args": ("--acp",),
        "cwd": tmp_path,
        "env": ("PROVIDER_API_KEY",),
        "startup_timeout": 15,
        "prompt_timeout": 120,
        "shutdown_grace_period": 5,
        "display_name": "coding-agent",
        "description": "External ACP coding agent",
    }
    values.update(overrides)
    return ACPProcessConfig(**values)


@pytest.mark.unit
def test_process_config_is_immutable_and_normalized(tmp_path: Path) -> None:
    config = _config(tmp_path, args=["--acp"], env=["PROVIDER_API_KEY"])

    assert config.args == ("--acp",)
    assert config.env == ("PROVIDER_API_KEY",)
    assert config.cwd == tmp_path.resolve()
    with pytest.raises((AttributeError, TypeError)):
        config.command = "other"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"command": ""}, "command"),
        ({"args": "--acp"}, "sequence"),
        ({"args": ["ok", 1]}, "strings"),
        ({"env": ["KEY=value"]}, "variable names"),
        ({"env": ["NOT-VALID"]}, "variable names"),
        ({"startup_timeout": True}, "timeout"),
        ({"prompt_timeout": 0}, "timeout"),
        ({"shutdown_grace_period": 3601}, "timeout"),
    ],
)
def test_process_config_rejects_invalid_values(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _config(tmp_path, **overrides)


@pytest.mark.unit
def test_process_config_rejects_non_directory_cwd(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="existing directory"):
        _config(tmp_path, cwd=missing)


@pytest.mark.unit
@pytest.mark.parametrize(
    "legacy_key", ["endpoint", "agent_name", "verify_tls", "auth", "bearer_token", "poll_interval"]
)
def test_mapping_rejects_obsolete_beeai_keys(tmp_path: Path, legacy_key: str) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.config import (
        acp_process_config_from_mapping,
    )

    mapping = {"enabled": True, "command": "agent", "cwd": str(tmp_path), legacy_key: "legacy"}
    with pytest.raises(ValueError, match="BeeAI.*command"):
        acp_process_config_from_mapping(mapping, name="worker", description="Worker")


@pytest.mark.unit
def test_mapping_uses_exact_yaml_defaults_and_metadata(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.config import (
        acp_process_config_from_mapping,
    )

    config = acp_process_config_from_mapping(
        {"enabled": True, "command": "agent", "args": ["--acp"], "cwd": str(tmp_path)},
        name="worker",
        description="Does work",
    )
    assert (config.startup_timeout, config.prompt_timeout, config.shutdown_grace_period) == (15.0, 120.0, 5.0)
    assert (config.display_name, config.description) == ("worker", "Does work")


@pytest.mark.unit
def test_minimized_environment_forwards_only_baseline_and_allowlist(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.process import build_process_environment

    parent = {
        "PATH": "/bin",
        "HOME": "/home/test",
        "TMPDIR": "/tmp",
        "LANG": "en_US.UTF-8",
        "PROVIDER_API_KEY": "secret",
        "MISSING": "",
        "UNRELATED_SECRET": "never-forward",
    }
    env = build_process_environment(_config(tmp_path, env=("PROVIDER_API_KEY", "ABSENT")), parent)

    assert env == {
        "PATH": "/bin",
        "HOME": "/home/test",
        "TMPDIR": "/tmp",
        "LANG": "en_US.UTF-8",
        "PROVIDER_API_KEY": "secret",
    }


@pytest.mark.unit
def test_stderr_is_bounded_and_configured_secret_values_are_redacted() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.process import sanitize_stderr

    value = "top-secret-token"
    diagnostic = sanitize_stderr((f"before {value} after" * 100).encode(), [value], byte_limit=40)
    assert value not in diagnostic
    assert len(diagnostic.encode()) <= 40


class _FakeProcess:
    def __init__(self, *, returncode: int | None = None, waits: list[Any] | None = None) -> None:
        self.stdin = object()
        self.stdout = object()
        self.stderr = SimpleNamespace(read=self._read_stderr)
        self.returncode = returncode
        self.waits = list(waits or [0])
        self.terminated = 0
        self.killed = 0
        self.wait_count = 0

    async def _read_stderr(self, _limit: int = -1) -> bytes:
        return b""

    async def wait(self) -> int:
        self.wait_count += 1
        value = self.waits.pop(0) if self.waits else 0
        if isinstance(value, BaseException):
            raise value
        self.returncode = int(value)
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1


class _FakeConnection:
    def __init__(self, *, prompt_error: BaseException | None = None) -> None:
        self.client = None
        self.prompt_error = prompt_error
        self.initialized: list[tuple[Any, ...]] = []
        self.sessions: list[tuple[Any, ...]] = []
        self.prompts: list[tuple[Any, ...]] = []
        self.cancelled: list[str] = []
        self.closed = 0

    async def initialize(self, protocol_version: int, client_capabilities: Any, client_info: Any) -> Any:
        self.initialized.append((protocol_version, client_capabilities, client_info))
        return SimpleNamespace(protocol_version=protocol_version)

    async def new_session(self, cwd: str, mcp_servers: list[Any]) -> Any:
        self.sessions.append((cwd, mcp_servers))
        return SimpleNamespace(session_id="session-1")

    async def prompt(self, session_id: str, prompt: list[Any]) -> Any:
        self.prompts.append((session_id, prompt))
        if self.prompt_error is not None:
            raise self.prompt_error
        await self.client.session_update(
            session_id,
            AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=TextContentBlock(type="text", text="first "),
            ),
        )
        await self.client.session_update(
            session_id,
            AgentThoughtChunk(
                sessionUpdate="agent_thought_chunk",
                content=TextContentBlock(type="text", text="private"),
            ),
        )
        await self.client.session_update(
            session_id,
            AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=TextContentBlock(type="text", text="second"),
            ),
        )
        return SimpleNamespace(stop_reason="end_turn")

    async def cancel(self, session_id: str) -> None:
        self.cancelled.append(session_id)

    async def close(self) -> None:
        self.closed += 1


@pytest.mark.unit
async def test_callbacks_collect_only_owned_agent_text_and_fail_permissions_closed() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.callbacks import ACPClientCallbacks

    callbacks = ACPClientCallbacks()
    callbacks.bind_session("owned")
    text = TextContentBlock(type="text", text="visible")
    await callbacks.session_update(
        "other", AgentMessageChunk(sessionUpdate="agent_message_chunk", content=text)
    )
    await callbacks.session_update(
        "owned", AgentThoughtChunk(sessionUpdate="agent_thought_chunk", content=text)
    )
    await callbacks.session_update(
        "owned", AgentMessageChunk(sessionUpdate="agent_message_chunk", content=text)
    )
    response = await callbacks.request_permission(
        "owned",
        ToolCallUpdate(toolCallId="call-1", title="Run tests", kind="execute"),
        [PermissionOption(optionId="allow", name="Allow once", kind="allow_once")],
    )

    assert callbacks.text == "visible"
    assert callbacks.permission_required is True
    assert response.outcome.outcome == "cancelled"


@pytest.mark.unit
async def test_callbacks_permission_seam_uses_frozen_safe_dtos() -> None:
    from dataclasses import FrozenInstanceError

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.callbacks import ACPClientCallbacks

    seen = []

    async def handler(request):
        seen.append(request)
        return request.options[0].option_id

    callbacks = ACPClientCallbacks(permission_handler=handler)
    callbacks.bind_session("owned")
    response = await callbacks.request_permission(
        "owned",
        ToolCallUpdate(
            toolCallId="call-1",
            title="Run tests",
            kind="execute",
            rawInput={"secret": "must-not-cross-seam"},
        ),
        [PermissionOption(optionId="allow", name="Allow once", kind="allow_once")],
    )

    request = seen[0]
    assert request.session_id == "owned"
    assert request.tool_call_id == "call-1"
    assert request.title == "Run tests"
    assert request.kind == "execute"
    assert request.options[0].kind == "allow_once"
    assert "secret" not in repr(request)
    with pytest.raises(FrozenInstanceError):
        request.title = "changed"
    assert response.outcome.option_id == "allow"


@pytest.mark.unit
async def test_delegate_direct_exec_initializes_prompts_closes_and_reaps(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    connection = _FakeConnection()
    spawn_calls = []

    async def process_factory(*args: Any, **kwargs: Any) -> _FakeProcess:
        spawn_calls.append((args, kwargs))
        return process

    def connection_factory(client: Any, stdin: Any, stdout: Any) -> _FakeConnection:
        assert stdin is process.stdin
        assert stdout is process.stdout
        connection.client = client
        return connection

    result = await _delegate_task_via_acp(
        config=_config(tmp_path),
        task="implement feature",
        process_factory=process_factory,
        connection_factory=connection_factory,
        environ={"PATH": "/bin", "PROVIDER_API_KEY": "secret", "OTHER": "not-forwarded"},
    )

    assert result == {"result": "first second", "status": "success", "variables": {}}
    args, kwargs = spawn_calls[0]
    assert args == ("external-agent", "--acp")
    assert kwargs["cwd"] == str(tmp_path.resolve())
    assert kwargs["env"] == {"PATH": "/bin", "PROVIDER_API_KEY": "secret"}
    assert "shell" not in kwargs
    capabilities = connection.initialized[0][1]
    assert capabilities.fs.read_text_file is False
    assert capabilities.fs.write_text_file is False
    assert capabilities.terminal is False
    assert connection.sessions == [(str(tmp_path.resolve()), [])]
    assert connection.prompts[0][0] == "session-1"
    assert connection.prompts[0][1][0].text == "implement feature"
    assert connection.closed == 1
    assert process.wait_count >= 1


@pytest.mark.unit
async def test_delegate_reports_no_output_and_permission_required(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    connection = _FakeConnection()

    async def empty_prompt(_session_id: str, _prompt: list[Any]) -> Any:
        return SimpleNamespace(stop_reason="end_turn")

    connection.prompt = empty_prompt

    async def process_factory(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        return process

    def connection_factory(client: Any, *_args: Any) -> _FakeConnection:
        connection.client = client
        return connection

    result = await _delegate_task_via_acp(
        config=_config(tmp_path),
        task="work",
        process_factory=process_factory,
        connection_factory=connection_factory,
    )
    assert result == {
        "result": "ACP agent completed without text output.",
        "status": "success",
        "variables": {},
    }

    async def permission_prompt(session_id: str, _prompt: list[Any]) -> Any:
        await connection.client.request_permission(
            session_id,
            ToolCallUpdate(toolCallId="call", title="write", kind="edit"),
            [PermissionOption(optionId="allow", name="Allow", kind="allow_once")],
        )
        return SimpleNamespace(stop_reason="cancelled")

    connection.prompt = permission_prompt
    process = _FakeProcess()
    result = await _delegate_task_via_acp(
        config=_config(tmp_path),
        task="work",
        process_factory=process_factory,
        connection_factory=connection_factory,
    )
    assert result == {
        "result": "ACP agent requires permission to continue.",
        "status": "failed",
        "variables": {},
    }


@pytest.mark.unit
async def test_prompt_timeout_cancels_session_and_reaps(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    connection = _FakeConnection(prompt_error=TimeoutError())

    result = await _delegate_task_via_acp(
        config=_config(tmp_path, prompt_timeout=0.01),
        task="work",
        process_factory=lambda *_args, **_kwargs: _async_value(process),
        connection_factory=lambda client, *_args: _bind(connection, client),
    )
    assert result == {
        "result": "ACP agent did not respond before the timeout.",
        "status": "failed",
        "variables": {},
    }
    assert connection.cancelled == ["session-1"]
    assert connection.closed == 1
    assert process.wait_count >= 1


@pytest.mark.unit
async def test_caller_cancellation_propagates_after_cleanup(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    connection = _FakeConnection(prompt_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await _delegate_task_via_acp(
            config=_config(tmp_path),
            task="work",
            process_factory=lambda *_args, **_kwargs: _async_value(process),
            connection_factory=lambda client, *_args: _bind(connection, client),
        )
    assert connection.cancelled == ["session-1"]
    assert connection.closed == 1
    assert connection.client.cancelled is True
    assert process.wait_count >= 1


@pytest.mark.unit
async def test_startup_timeout_is_safe_and_reaps(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    connection = _FakeConnection()

    async def blocked_initialize(*_args: Any) -> None:
        await asyncio.Event().wait()

    connection.initialize = blocked_initialize
    result = await _delegate_task_via_acp(
        config=_config(tmp_path, startup_timeout=0.01),
        task="work",
        process_factory=lambda *_args, **_kwargs: _async_value(process),
        connection_factory=lambda client, *_args: _bind(connection, client),
    )

    assert result == {"result": "ACP agent could not be started.", "status": "failed", "variables": {}}
    assert connection.closed == 1
    assert process.wait_count >= 1


@pytest.mark.unit
async def test_protocol_failure_is_sanitized_and_reaps(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    connection = _FakeConnection(prompt_error=ValueError("secret malformed frame"))
    result = await _delegate_task_via_acp(
        config=_config(tmp_path),
        task="work",
        process_factory=lambda *_args, **_kwargs: _async_value(process),
        connection_factory=lambda client, *_args: _bind(connection, client),
    )

    assert result == {
        "result": "ACP agent protocol communication failed.",
        "status": "failed",
        "variables": {},
    }
    assert "secret" not in repr(result)
    assert process.wait_count >= 1


@pytest.mark.unit
async def test_early_process_exit_is_normalized(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess(returncode=17)
    result = await _delegate_task_via_acp(
        config=_config(tmp_path),
        task="work",
        process_factory=lambda *_args, **_kwargs: _async_value(process),
        connection_factory=lambda *_args: pytest.fail("connection must not be created"),
    )
    assert result == {"result": "ACP agent process exited unexpectedly.", "status": "failed", "variables": {}}
    assert process.wait_count >= 1


@pytest.mark.unit
async def test_cleanup_escalates_from_wait_to_terminate_to_kill() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.process import _stop_and_reap

    process = _FakeProcess(waits=[TimeoutError(), TimeoutError(), 137])
    await _stop_and_reap(process, 0.01)

    assert process.terminated == 1
    assert process.killed == 1
    assert process.wait_count == 3
    assert process.returncode == 137


@pytest.mark.unit
async def test_process_lookup_during_signal_still_reaps() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.process import _stop_and_reap

    process = _FakeProcess(waits=[TimeoutError(), 0])

    def raced_terminate() -> None:
        process.terminated += 1
        raise ProcessLookupError

    process.terminate = raced_terminate
    await _stop_and_reap(process, 0.01)

    assert process.terminated == 1
    assert process.wait_count == 2


async def _async_value(value: Any) -> Any:
    return value


def _bind(connection: _FakeConnection, client: Any) -> _FakeConnection:
    connection.client = client
    return connection
