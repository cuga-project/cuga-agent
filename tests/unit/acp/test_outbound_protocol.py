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


@pytest.fixture(autouse=True)
def _workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem import paths

    monkeypatch.setattr(paths, "local_base_dir", lambda: tmp_path)


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
def test_process_config_defaults_cwd_to_shared_workspace(tmp_path: Path) -> None:
    assert _config(tmp_path, cwd=None).cwd == tmp_path.resolve()


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
    with pytest.raises(ValueError, match=r"cwd must resolve to an existing workspace directory$"):
        _config(tmp_path, cwd=missing)


@pytest.mark.unit
def test_process_config_allows_workspace_relative_directory(tmp_path: Path) -> None:
    child = tmp_path / "project"
    child.mkdir()
    assert _config(tmp_path, cwd="project").cwd == child.resolve()


@pytest.mark.unit
@pytest.mark.parametrize("cwd", ["../escape", "/etc"])
def test_process_config_rejects_workspace_escape(tmp_path: Path, cwd: str) -> None:
    with pytest.raises(ValueError, match="configured CUGA workspace"):
        _config(tmp_path, cwd=cwd)


@pytest.mark.unit
def test_process_config_rejects_symlink_workspace_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="configured CUGA workspace"):
        _config(tmp_path, cwd="link")


@pytest.mark.unit
@pytest.mark.parametrize(
    "legacy_key", ["endpoint", "agent_name", "verify_tls", "auth", "bearer_token", "poll_interval"]
)
def test_mapping_rejects_obsolete_remote_keys(tmp_path: Path, legacy_key: str) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.config import (
        acp_process_config_from_mapping,
    )

    mapping = {"enabled": True, "command": "agent", "cwd": str(tmp_path), legacy_key: "legacy"}
    with pytest.raises(ValueError, match="Obsolete remote ACP.*command"):
        acp_process_config_from_mapping(mapping, name="worker", description="Worker")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mapping", "message"),
    [
        (None, "acp_protocol must be a mapping"),
        ({"enabled": "true", "command": "agent"}, "enabled must be a boolean"),
        ({"enabled": True, "command": "agent", "prompt_timout": 1}, "Unknown acp_protocol"),
        ({"enabled": False, "endpoint": "https://legacy.example"}, "Obsolete remote ACP"),
    ],
)
def test_mapping_rejects_malformed_types_unknown_and_disabled_legacy_keys(mapping: Any, message: str) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.config import (
        acp_process_config_from_mapping,
        validate_acp_protocol_mapping,
    )

    validator = (
        validate_acp_protocol_mapping
        if isinstance(mapping, dict) and mapping.get("enabled") is False
        else acp_process_config_from_mapping
    )
    with pytest.raises(ValueError, match=message):
        validator(mapping)


@pytest.mark.unit
def test_process_config_rejects_excessive_env_names_before_iteration(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.config import _MAX_ENV_NAMES

    class GuardedNames(list[str]):
        def __init__(self) -> None:
            pass

        def __len__(self) -> int:
            return _MAX_ENV_NAMES + 1

        def __iter__(self):
            pytest.fail("excessive env names must be rejected before iteration")

    with pytest.raises(ValueError, match="env contains too many entries"):
        _config(tmp_path, env=GuardedNames())


@pytest.mark.unit
def test_mapping_rejects_many_and_long_environment_values(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.config import (
        _MAX_ENV_NAMES,
        acp_process_config_from_mapping,
    )

    with pytest.raises(ValueError, match="env contains too many entries"):
        acp_process_config_from_mapping(
            {
                "enabled": True,
                "command": "agent",
                "cwd": str(tmp_path),
                "env": [f"ENV_{index}" for index in range(_MAX_ENV_NAMES + 1)],
            }
        )
    with pytest.raises(ValueError, match="variable names"):
        acp_process_config_from_mapping(
            {"enabled": True, "command": "agent", "cwd": str(tmp_path), "env": ["E" * 10_000]}
        )


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
@pytest.mark.parametrize(
    ("payload", "secrets", "byte_limit", "forbidden"),
    [
        (b"1234567secret-tail", ["secret-tail"], 10, ("secret-tail", "sec")),
        (b"very-long-secret", ["very-long-secret"], 5, ("very-long-secret", "very-")),
        (b"ababa", ["aba"], 32, ("aba",)),
        (b"secret-xxsecret", ["secret"], 15, ("secret", "secre")),
        (b"1234567secre\xe2", ["secret"], 13, ("secret", "secre")),
        (b"REDACTED", ["REDACTED"], 32, ("REDACTED",)),
        ("start 密碼秘密 end".encode(), ["密碼秘密"], 10, ("密碼秘密", "密")),
    ],
)
def test_stderr_redaction_is_boundary_safe_and_byte_bounded(
    payload: bytes,
    secrets: list[str],
    byte_limit: int,
    forbidden: tuple[str, ...],
) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.process import sanitize_stderr

    diagnostic = sanitize_stderr(payload, secrets, byte_limit=byte_limit)

    assert len(diagnostic.encode()) <= byte_limit
    assert all(value not in diagnostic for value in forbidden)


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
@pytest.mark.parametrize(("session_id", "closed"), [("other", False), ("owned", True)])
async def test_callbacks_fail_closed_for_wrong_or_closed_session(session_id: str, closed: bool) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.callbacks import ACPClientCallbacks

    callbacks = ACPClientCallbacks(permission_handler=pytest.fail)
    callbacks.bind_session("owned")
    callbacks.closed = closed

    response = await callbacks.request_permission(
        session_id,
        ToolCallUpdate(toolCallId="call", title="write", kind="edit"),
        [PermissionOption(optionId="allow", name="Allow", kind="allow_once")],
    )

    assert response.outcome.outcome == "cancelled"
    assert callbacks.permission_required is False


@pytest.mark.unit
async def test_callbacks_invalid_permission_option_marks_permission_required() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.callbacks import ACPClientCallbacks

    async def invalid_handler(_request: Any) -> str:
        return "not-offered"

    callbacks = ACPClientCallbacks(permission_handler=invalid_handler)
    callbacks.bind_session("owned")
    response = await callbacks.request_permission(
        "owned",
        ToolCallUpdate(toolCallId="call", title="write", kind="edit"),
        [PermissionOption(optionId="allow", name="Allow", kind="allow_once")],
    )

    assert response.outcome.outcome == "cancelled"
    assert callbacks.permission_required is True


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
    assert request.options[0].option_id == "option-1"
    assert "secret" not in repr(request)
    with pytest.raises(FrozenInstanceError):
        request.title = "changed"
    assert response.outcome.option_id == "allow"
    assert callbacks.permission_required is False


@pytest.mark.unit
async def test_permission_option_token_round_trips_exact_peer_id() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.callbacks import ACPClientCallbacks

    original_id = "peer\n" + "x" * 1000

    async def handler(request: Any) -> str:
        assert request.options[0].option_id == "option-1"
        assert original_id not in repr(request)
        return request.options[0].option_id

    callbacks = ACPClientCallbacks(permission_handler=handler)
    callbacks.bind_session("owned")
    response = await callbacks.request_permission(
        "owned",
        ToolCallUpdate(toolCallId="call", title="run", kind="execute"),
        [PermissionOption(optionId=original_id, name="Allow", kind="allow_once")],
    )
    assert response.outcome.option_id == original_id


@pytest.mark.unit
async def test_permission_metadata_is_bounded_normalized_and_count_limited() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.callbacks import ACPClientCallbacks

    seen = []

    async def handler(request: Any) -> None:
        seen.append(request)

    callbacks = ACPClientCallbacks(permission_handler=handler, lifecycle_id="owned-lifecycle")
    callbacks.bind_session("owned")
    options = [
        PermissionOption(
            optionId=f"option-{index}\n" + "x" * 300, name="label\t" + "y" * 800, kind="allow_once"
        )
        for index in range(40)
    ]
    await callbacks.request_permission(
        "owned",
        ToolCallUpdate(toolCallId="call\n" + "z" * 300, title="title\x00" + "t" * 800, kind="execute\r"),
        options,
    )

    request = seen[0]
    assert request.lifecycle_id == "owned-lifecycle"
    assert len(request.tool_call_id) <= 128
    assert len(request.title) <= 512
    assert len(request.kind or "") <= 64
    assert len(request.options) == 32
    assert all(character.isprintable() for character in request.title)
    assert all(len(option.option_id) <= 128 and len(option.name) <= 512 for option in request.options)


@pytest.mark.unit
async def test_lifecycle_registration_uses_opaque_id_and_live_owner(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    connection = _FakeConnection()
    registrations = []

    async def registrar(lifecycle_id: str, owner: Any) -> None:
        registrations.append((lifecycle_id, owner))

    await _delegate_task_via_acp(
        config=_config(tmp_path),
        task="work",
        lifecycle_registrar=registrar,
        process_factory=_async_factory(process),
        connection_factory=_bound_factory(connection),
    )

    assert len(registrations) == 1
    lifecycle_id, owner = registrations[0]
    assert len(lifecycle_id) == 32
    assert owner.process is process
    assert owner.connection is connection
    assert lifecycle_id not in repr(owner)


@pytest.mark.unit
async def test_delegate_direct_exec_initializes_prompts_closes_and_reaps(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    connection = _FakeConnection()
    spawn_calls = []

    async def process_factory(*args: Any, **kwargs: Any) -> _FakeProcess:
        spawn_calls.append((args, kwargs))
        return process

    async def connection_factory(client: Any, stdin: Any, stdout: Any) -> _FakeConnection:
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

    async def connection_factory(client: Any, *_args: Any) -> _FakeConnection:
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
        return SimpleNamespace(stop_reason="end_turn")

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
async def test_approved_permission_then_agent_cancel_is_not_permission_required(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    connection = _FakeConnection()

    async def approved_handler(request: Any) -> str:
        return request.options[0].option_id

    async def cancelled_prompt(session_id: str, _prompt: list[Any]) -> Any:
        response = await connection.client.request_permission(
            session_id,
            ToolCallUpdate(toolCallId="call", title="write", kind="edit"),
            [PermissionOption(optionId="allow", name="Allow", kind="allow_once")],
        )
        assert response.outcome.outcome == "selected"
        return SimpleNamespace(stop_reason="cancelled")

    connection.prompt = cancelled_prompt
    result = await _delegate_task_via_acp(
        config=_config(tmp_path),
        task="work",
        permission_handler=approved_handler,
        process_factory=_async_factory(process),
        connection_factory=_bound_factory(connection),
    )

    assert result == {
        "result": "ACP agent completed without text output.",
        "status": "success",
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
        process_factory=_async_factory(process),
        connection_factory=_bound_factory(connection),
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
            process_factory=_async_factory(process),
            connection_factory=_bound_factory(connection),
        )
    assert connection.cancelled == ["session-1"]
    assert connection.closed == 1
    assert connection.client.cancelled is True
    assert process.wait_count >= 1


@pytest.mark.unit
@pytest.mark.parametrize("cancel_during", ["session_cancel", "connection_close"])
async def test_cleanup_defers_nested_cancellation_until_all_attempts_finish(
    tmp_path: Path, cancel_during: str
) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess(waits=[TimeoutError(), TimeoutError(), 137])
    connection = _FakeConnection(prompt_error=TimeoutError())

    if cancel_during == "session_cancel":

        async def cancel(_session_id: str) -> None:
            connection.cancelled.append("session-1")
            raise asyncio.CancelledError

        connection.cancel = cancel
    else:

        async def close() -> None:
            connection.closed += 1
            raise asyncio.CancelledError

        connection.close = close

    with pytest.raises(asyncio.CancelledError):
        await _delegate_task_via_acp(
            config=_config(tmp_path, prompt_timeout=0.01, shutdown_grace_period=0.01),
            task="work",
            process_factory=_async_factory(process),
            connection_factory=_bound_factory(connection),
        )

    assert connection.cancelled == ["session-1"]
    assert connection.closed == 1
    assert process.terminated == 1
    assert process.killed == 1
    assert process.wait_count == 3


@pytest.mark.unit
async def test_cleanup_defers_caller_cancellation_during_process_wait(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    wait_started = asyncio.Event()
    permit_exit = asyncio.Event()
    process = _FakeProcess()
    connection = _FakeConnection()

    async def blocking_wait() -> int:
        process.wait_count += 1
        wait_started.set()
        await permit_exit.wait()
        process.returncode = 0
        return 0

    process.wait = blocking_wait
    delegation = asyncio.create_task(
        _delegate_task_via_acp(
            config=_config(tmp_path),
            task="work",
            process_factory=_async_factory(process),
            connection_factory=_bound_factory(connection),
        )
    )
    await wait_started.wait()
    delegation.cancel()
    await asyncio.sleep(0)
    assert not delegation.done()
    permit_exit.set()

    with pytest.raises(asyncio.CancelledError):
        await delegation

    assert process.returncode == 0
    assert process.wait_count == 1


@pytest.mark.unit
async def test_cleanup_retries_process_reap_after_nested_cancellation() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.process import _cleanup_lifecycle

    process = _FakeProcess(waits=[asyncio.CancelledError(), 0])

    with pytest.raises(asyncio.CancelledError):
        await _cleanup_lifecycle(
            connection=None,
            session_id=None,
            process=process,
            stderr_task=None,
            grace=0.01,
            cancel_session=False,
        )

    assert process.returncode == 0
    assert process.wait_count == 2


@pytest.mark.unit
async def test_synchronous_blocking_process_factory_is_rejected_without_invocation(tmp_path: Path) -> None:
    import time

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    invoked = False

    def blocking_factory(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal invoked
        invoked = True
        time.sleep(1)
        return _FakeProcess()

    result = await asyncio.wait_for(
        _delegate_task_via_acp(
            config=_config(tmp_path, startup_timeout=0.01), task="work", process_factory=blocking_factory
        ),
        timeout=0.2,
    )

    assert invoked is False
    assert result == {"result": "ACP agent could not be started.", "status": "failed", "variables": {}}


@pytest.mark.unit
async def test_non_awaitable_process_factory_result_is_rejected_without_invocation(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    invoked = False

    def non_awaitable_factory(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        nonlocal invoked
        invoked = True
        return _FakeProcess()

    result = await _delegate_task_via_acp(
        config=_config(tmp_path), task="work", process_factory=non_awaitable_factory
    )

    assert invoked is False
    assert result == {"result": "ACP agent could not be started.", "status": "failed", "variables": {}}


@pytest.mark.unit
async def test_synchronous_connection_factory_is_rejected_without_invocation_and_reaps(
    tmp_path: Path,
) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    invoked = False

    def connection_factory(*_args: Any) -> _FakeConnection:
        nonlocal invoked
        invoked = True
        return _FakeConnection()

    result = await _delegate_task_via_acp(
        config=_config(tmp_path),
        task="work",
        process_factory=_async_factory(process),
        connection_factory=connection_factory,
    )

    assert invoked is False
    assert result == {"result": "ACP agent could not be started.", "status": "failed", "variables": {}}
    assert process.wait_count >= 1


@pytest.mark.unit
async def test_async_partial_factory_is_accepted(tmp_path: Path) -> None:
    from functools import partial

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    connection = _FakeConnection()

    async def process_factory(value: _FakeProcess, *_args: Any, **_kwargs: Any) -> _FakeProcess:
        return value

    result = await _delegate_task_via_acp(
        config=_config(tmp_path),
        task="work",
        process_factory=partial(process_factory, process),
        connection_factory=_bound_factory(connection),
    )

    assert result["status"] == "success"
    assert process.wait_count >= 1


@pytest.mark.unit
async def test_factory_ownership_capacity_is_reserved_before_concurrent_process_invocation(
    tmp_path: Path,
) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.process import (
        _MAX_PENDING_FACTORY_TASKS,
        _PENDING_FACTORY_CLEANUPS,
        _PENDING_FACTORY_TASKS,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    baseline = len(_PENDING_FACTORY_TASKS) + len(_PENDING_FACTORY_CLEANUPS)
    available = _MAX_PENDING_FACTORY_TASKS - baseline
    release = asyncio.Event()
    invoked = 0

    async def resistant_factory(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal invoked
        invoked += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            raise

    tasks = [
        asyncio.create_task(
            _delegate_task_via_acp(
                config=_config(tmp_path, startup_timeout=0.02),
                task="work",
                process_factory=resistant_factory,
            )
        )
        for _ in range(available + 2)
    ]
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=0.2)

    assert invoked == available
    assert all(result["status"] == "failed" for result in results)
    assert len(_PENDING_FACTORY_TASKS) + len(_PENDING_FACTORY_CLEANUPS) == _MAX_PENDING_FACTORY_TASKS
    release.set()
    for _ in range(10):
        await asyncio.sleep(0)
        if len(_PENDING_FACTORY_TASKS) + len(_PENDING_FACTORY_CLEANUPS) == baseline:
            break
    assert len(_PENDING_FACTORY_TASKS) + len(_PENDING_FACTORY_CLEANUPS) == baseline


@pytest.mark.unit
async def test_cancellation_suppressing_connection_factory_is_bounded_and_eventually_closed(
    tmp_path: Path,
) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    connection = _FakeConnection()
    release = asyncio.Event()
    closed = asyncio.Event()
    original_close = connection.close

    async def observed_close() -> None:
        await original_close()
        closed.set()

    connection.close = observed_close

    async def resistant_connection_factory(*_args: Any) -> _FakeConnection:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            return connection

    result = await asyncio.wait_for(
        _delegate_task_via_acp(
            config=_config(tmp_path, startup_timeout=0.01, shutdown_grace_period=0.01),
            task="work",
            process_factory=_async_factory(process),
            connection_factory=resistant_connection_factory,
        ),
        timeout=0.2,
    )

    assert result == {"result": "ACP agent could not be started.", "status": "failed", "variables": {}}
    assert process.wait_count >= 1
    release.set()
    await asyncio.wait_for(closed.wait(), timeout=0.2)


@pytest.mark.unit
async def test_blocked_process_factory_is_bounded_by_startup_timeout(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    async def blocked_factory(*_args: Any, **_kwargs: Any) -> Any:
        await asyncio.Event().wait()

    result = await _delegate_task_via_acp(
        config=_config(tmp_path, startup_timeout=0.01),
        task="work",
        process_factory=blocked_factory,
    )
    assert result == {"result": "ACP agent could not be started.", "status": "failed", "variables": {}}


@pytest.mark.unit
async def test_cancellation_suppressing_blocked_factory_does_not_defeat_timeout(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    release = asyncio.Event()

    async def resistant_factory(*_args: Any, **_kwargs: Any) -> Any:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            raise

    result = await asyncio.wait_for(
        _delegate_task_via_acp(
            config=_config(tmp_path, startup_timeout=0.01), task="work", process_factory=resistant_factory
        ),
        timeout=0.2,
    )
    assert result == {"result": "ACP agent could not be started.", "status": "failed", "variables": {}}
    release.set()
    await asyncio.sleep(0)


@pytest.mark.unit
async def test_caller_cancellation_returns_while_spawn_factory_suppresses_cancellation(
    tmp_path: Path,
) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    started = asyncio.Event()
    release = asyncio.Event()

    async def resistant_factory(*_args: Any, **_kwargs: Any) -> Any:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            raise

    task = asyncio.create_task(
        _delegate_task_via_acp(config=_config(tmp_path), task="work", process_factory=resistant_factory)
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.2)
    release.set()
    await asyncio.sleep(0)


@pytest.mark.unit
async def test_detached_late_spawn_is_owned_and_reaped(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    release = asyncio.Event()
    reaped = asyncio.Event()
    original_wait = process.wait

    async def observed_wait() -> int:
        result = await original_wait()
        reaped.set()
        return result

    process.wait = observed_wait

    async def late_factory(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            return process

    result = await asyncio.wait_for(
        _delegate_task_via_acp(
            config=_config(tmp_path, startup_timeout=0.01, shutdown_grace_period=0.01),
            task="work",
            process_factory=late_factory,
        ),
        timeout=0.2,
    )
    assert result == {"result": "ACP agent could not be started.", "status": "failed", "variables": {}}
    release.set()
    await asyncio.wait_for(reaped.wait(), timeout=0.2)


@pytest.mark.unit
async def test_cancellation_racing_late_spawn_reaps_and_propagates(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import _delegate_task_via_acp

    process = _FakeProcess()
    spawn_started = asyncio.Event()

    async def late_factory(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        spawn_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return process

    task = asyncio.create_task(
        _delegate_task_via_acp(config=_config(tmp_path), task="work", process_factory=late_factory)
    )
    await spawn_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.wait_count >= 1


@pytest.mark.unit
def test_secret_validation_stops_before_excessive_lazy_source_is_materialized() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.process import (
        _MAX_SECRET_COUNT,
        _validated_secret_values,
    )

    seen = 0

    def candidates():
        nonlocal seen
        while True:
            seen += 1
            if seen > _MAX_SECRET_COUNT + 1:
                pytest.fail("secret candidate source was traversed beyond the fixed bound")
            yield f"secret-{seen}"

    with pytest.raises(ValueError, match="too many"):
        _validated_secret_values(candidates())
    assert seen == _MAX_SECRET_COUNT + 1


@pytest.mark.unit
def test_secret_validation_rejects_oversized_value_before_encoding() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.process import (
        _MAX_SECRET_CHAR_LENGTH,
        _validated_secret_values,
    )

    class EncodingMustNotRun(str):
        def __hash__(self) -> int:
            pytest.fail("oversized secret must be rejected before hashing")

        def encode(self, *_args: Any, **_kwargs: Any) -> bytes:
            pytest.fail("oversized secret must be rejected before UTF-8 encoding")

    oversized = EncodingMustNotRun("s" * (_MAX_SECRET_CHAR_LENGTH + 1))
    with pytest.raises(ValueError, match="safe size limit"):
        _validated_secret_values([oversized])


@pytest.mark.unit
def test_secret_validation_is_fixed_bound_and_does_not_reflect_values(tmp_path: Path) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.process import (
        _MAX_SECRET_BYTE_LENGTH,
        _MAX_SECRET_COUNT,
        _validated_secret_values,
    )

    oversized = "s" * (_MAX_SECRET_BYTE_LENGTH + 1)
    with pytest.raises(ValueError, match="safe size limit") as error:
        _validated_secret_values([oversized])
    assert oversized not in str(error.value)
    with pytest.raises(ValueError, match="too many"):
        _validated_secret_values([f"overlap-{index}" for index in range(_MAX_SECRET_COUNT + 1)])


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
        process_factory=_async_factory(process),
        connection_factory=_bound_factory(connection),
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
        process_factory=_async_factory(process),
        connection_factory=_bound_factory(connection),
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
        process_factory=_async_factory(process),
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
@pytest.mark.parametrize("raced_signal", ["terminate", "kill"])
async def test_process_lookup_during_signal_still_reaps(raced_signal: str) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.process import _stop_and_reap

    waits = [TimeoutError(), 0] if raced_signal == "terminate" else [TimeoutError(), TimeoutError(), 0]
    process = _FakeProcess(waits=waits)

    counter = "terminated" if raced_signal == "terminate" else "killed"

    def raced() -> None:
        setattr(process, counter, getattr(process, counter) + 1)
        raise ProcessLookupError

    setattr(process, raced_signal, raced)
    await _stop_and_reap(process, 0.01)

    assert getattr(process, counter) == 1
    assert process.wait_count == len(waits)


def _bound_factory(connection: _FakeConnection):
    async def factory(client: Any, *_args: Any) -> _FakeConnection:
        return _bind(connection, client)

    return factory


def _async_factory(value: Any):
    async def factory(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return factory


async def _async_value(value: Any) -> Any:
    return value


def _bind(connection: _FakeConnection, client: Any) -> _FakeConnection:
    connection.client = client
    return connection
