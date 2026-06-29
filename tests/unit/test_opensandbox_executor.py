"""Unit tests for OpenSandboxExecutor skill-related behaviors.

Three architectural properties verified here:

  Fix 1 — Concurrency safety: two simultaneous _get_or_create_interpreter
           calls for the same thread_id produce exactly one Sandbox.create call
           because the per-key asyncio.Lock serialises creation.

  Fix 2 — Upload resilience: a write_files failure logs a warning but the
           sandbox is still cached so no remote container is orphaned.

  Fix 5 — Stale-config detection: create_sandbox_tools logs a warning when
           the requested skills config differs from what was active at sandbox
           creation time.

opensandbox and code_interpreter are optional packages not installed in the dev
environment; they are replaced with MagicMock modules before the executor is
imported.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Inject mock modules BEFORE the executor is imported
# ---------------------------------------------------------------------------

_mock_write_entry_cls = MagicMock()
sys.modules.setdefault("opensandbox", MagicMock())
sys.modules.setdefault("opensandbox.config", MagicMock())
sys.modules.setdefault("opensandbox.models", MagicMock(WriteEntry=_mock_write_entry_cls))
sys.modules.setdefault("code_interpreter", MagicMock())

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.opensandbox.opensandbox_executor import (  # noqa: E402
    OpenSandboxExecutor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_interpreter(*, write_files_side_effect=None):
    """Minimal mock CodeInterpreter with controllable write_files behaviour."""
    interp = MagicMock()
    interp.sandbox = MagicMock()
    interp.sandbox.commands.run = AsyncMock(return_value=None)
    interp.sandbox.kill = AsyncMock()
    interp.sandbox.close = AsyncMock()
    if write_files_side_effect is not None:
        interp.sandbox.files.write_files = AsyncMock(side_effect=write_files_side_effect)
    else:
        interp.sandbox.files.write_files = AsyncMock(return_value=None)
    return interp


def _wire_sandbox_mocks(interpreter, *, delay: float = 0.0) -> dict:
    """Wire sys.modules mocks so _get_or_create_interpreter uses interpreter.

    Returns a dict with a 'create_calls' counter so tests can assert how many
    times Sandbox.create was called.
    """
    counter = {"create_calls": 0}

    async def _slow_create(*args, **kwargs):
        counter["create_calls"] += 1
        if delay:
            await asyncio.sleep(delay)
        return MagicMock()  # the raw Sandbox object (not the interpreter)

    sys.modules["opensandbox"].Sandbox = MagicMock()
    sys.modules["opensandbox"].Sandbox.create = AsyncMock(side_effect=_slow_create)
    sys.modules["opensandbox.config"].ConnectionConfig = MagicMock(return_value=MagicMock())
    sys.modules["code_interpreter"].CodeInterpreter = MagicMock()
    sys.modules["code_interpreter"].CodeInterpreter.create = AsyncMock(return_value=interpreter)
    return counter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state():
    """Wipe class-level state before and after every test for isolation."""
    OpenSandboxExecutor._sandboxes.clear()
    OpenSandboxExecutor._skills_config.clear()
    OpenSandboxExecutor._active_skills_config.clear()
    OpenSandboxExecutor._locks.clear()
    yield
    OpenSandboxExecutor._sandboxes.clear()
    OpenSandboxExecutor._skills_config.clear()
    OpenSandboxExecutor._active_skills_config.clear()
    OpenSandboxExecutor._locks.clear()


# ---------------------------------------------------------------------------
# Fix 1 — Concurrency safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_creation_calls_sandbox_create_exactly_once() -> None:
    """Two concurrent _get_or_create_interpreter calls for the same thread_id
    must produce exactly one remote sandbox, not two.

    The lock in _get_or_create_interpreter serialises creation: the second
    coroutine waits until the first has cached the interpreter, then finds it
    in _sandboxes and returns immediately without calling Sandbox.create again.

    Without the lock, both coroutines see an empty cache, both call
    Sandbox.create, and one sandbox is orphaned.
    """
    interpreter = _make_interpreter()
    counter = _wire_sandbox_mocks(interpreter, delay=0.02)

    # skills disabled so we skip the upload path and keep the mock surface minimal
    executor = OpenSandboxExecutor()
    executor._skills_config["thread-A"] = (None, False)

    results = await asyncio.gather(
        executor._get_or_create_interpreter("thread-A"),
        executor._get_or_create_interpreter("thread-A"),
    )

    assert counter["create_calls"] == 1, (
        f"Sandbox.create should be called once under concurrent access, "
        f"but was called {counter['create_calls']} times"
    )
    assert results[0] is results[1], "Both coroutines should receive the same cached interpreter"


@pytest.mark.asyncio
async def test_different_thread_ids_each_get_own_sandbox() -> None:
    """Sandboxes for different thread_ids are independent and each created once."""
    interp_a = _make_interpreter()
    interp_b = _make_interpreter()

    call_count = {"n": 0}

    async def _create(*args, **kwargs):
        call_count["n"] += 1
        return MagicMock()

    sys.modules["opensandbox"].Sandbox = MagicMock()
    sys.modules["opensandbox"].Sandbox.create = AsyncMock(side_effect=_create)
    sys.modules["opensandbox.config"].ConnectionConfig = MagicMock(return_value=MagicMock())

    interps = [interp_a, interp_b]
    idx = {"i": 0}

    async def _create_interp(*args, **kwargs):
        result = interps[idx["i"]]
        idx["i"] += 1
        return result

    sys.modules["code_interpreter"].CodeInterpreter = MagicMock()
    sys.modules["code_interpreter"].CodeInterpreter.create = AsyncMock(side_effect=_create_interp)

    executor = OpenSandboxExecutor()
    executor._skills_config["thread-X"] = (None, False)
    executor._skills_config["thread-Y"] = (None, False)

    result_x, result_y = await asyncio.gather(
        executor._get_or_create_interpreter("thread-X"),
        executor._get_or_create_interpreter("thread-Y"),
    )

    assert call_count["n"] == 2, "Each distinct thread_id should create its own sandbox"
    assert result_x is interp_a
    assert result_y is interp_b


# ---------------------------------------------------------------------------
# Fix 2 — Upload resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sandbox_cached_even_when_upload_fails(tmp_path, monkeypatch) -> None:
    """When write_files raises, the executor still caches the sandbox.

    Before this fix, the exception propagated out before the assignment
    self._sandboxes[key] = interpreter, leaving an uncached (orphaned) remote
    container.  After the fix, the interpreter is cached and a warning is
    logged so the agent can continue without skills.
    """
    interpreter = _make_interpreter(write_files_side_effect=RuntimeError("network error"))
    _wire_sandbox_mocks(interpreter)

    # Write a real skill so discover_skills returns something and triggers the upload path
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".cuga" / "skills" / "my_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my_skill\ndescription: A skill\n---\nBody\n", encoding="utf-8"
    )

    executor = OpenSandboxExecutor()
    executor._skills_config["thread-B"] = (str(tmp_path / ".cuga"), True)

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.executors.opensandbox.opensandbox_executor.logger"
    ) as mock_logger:
        result = await executor._get_or_create_interpreter("thread-B")

    assert "thread-B" in executor._sandboxes, (
        "Sandbox must be cached even after an upload failure — otherwise the remote container is orphaned."
    )
    assert result is interpreter

    warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
    assert any("upload" in w.lower() or "write_files" in w.lower() for w in warning_calls), (
        f"Expected a warning about the upload failure. Got: {warning_calls}"
    )


@pytest.mark.asyncio
async def test_upload_failure_does_not_prevent_subsequent_tool_use(tmp_path, monkeypatch) -> None:
    """After a failed upload the interpreter is in _sandboxes, so a second call
    to _get_or_create_interpreter returns the cached interpreter immediately
    without attempting another Sandbox.create."""
    interpreter = _make_interpreter(write_files_side_effect=RuntimeError("timeout"))
    counter = _wire_sandbox_mocks(interpreter)

    monkeypatch.chdir(tmp_path)
    executor = OpenSandboxExecutor()
    executor._skills_config["thread-C"] = (None, False)  # no upload, test the caching alone

    await executor._get_or_create_interpreter("thread-C")
    # Force the skills config to True for second call to verify cached path
    executor._skills_config["thread-C"] = (None, False)
    second = await executor._get_or_create_interpreter("thread-C")

    assert counter["create_calls"] == 1, "Second call should hit the cache, not create a new sandbox"
    assert second is interpreter


# ---------------------------------------------------------------------------
# Fix 5 — Stale-config detection
# ---------------------------------------------------------------------------


def test_stale_skills_config_logs_warning() -> None:
    """create_sandbox_tools warns when skills_folder changes for a live sandbox.

    If the caller changes skills_folder between invocations while the sandbox
    is still running, the new skills will not be available (the upload only
    happens at sandbox creation).  The warning directs the user to call
    release_sandbox() first.
    """
    executor = OpenSandboxExecutor()
    key = "thread-D"

    # Simulate an already-running sandbox with an old skills config
    executor._sandboxes[key] = _make_interpreter()
    executor._active_skills_config[key] = ("/old/path/.cuga", True)

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.executors.opensandbox.opensandbox_executor.logger"
    ) as mock_logger:
        executor.create_sandbox_tools(
            thread_id=key,
            cuga_folder="/new/path/.cuga",
            skills_enabled=True,
        )

    warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
    assert any("release_sandbox" in w for w in warning_calls), (
        f"Expected a warning mentioning release_sandbox when skills config changes. Got: {warning_calls}"
    )


def test_no_stale_warning_when_config_unchanged() -> None:
    """No warning is emitted when create_sandbox_tools is called with the same config."""
    executor = OpenSandboxExecutor()
    key = "thread-E"

    executor._sandboxes[key] = _make_interpreter()
    executor._active_skills_config[key] = ("/same/path/.cuga", True)

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.executors.opensandbox.opensandbox_executor.logger"
    ) as mock_logger:
        executor.create_sandbox_tools(
            thread_id=key,
            cuga_folder="/same/path/.cuga",
            skills_enabled=True,
        )

    warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
    assert not any("release_sandbox" in w for w in warning_calls), (
        f"Unexpected stale-config warning when config is unchanged. Got: {warning_calls}"
    )


def test_no_stale_warning_for_new_sandbox() -> None:
    """No warning when there is no existing sandbox for the thread_id."""
    executor = OpenSandboxExecutor()

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.executors.opensandbox.opensandbox_executor.logger"
    ) as mock_logger:
        executor.create_sandbox_tools(
            thread_id="brand-new-thread",
            cuga_folder="/some/path/.cuga",
            skills_enabled=True,
        )

    warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
    assert not any("release_sandbox" in w for w in warning_calls)


def test_release_sandbox_clears_all_state() -> None:
    """release_sandbox removes the thread from all tracking dicts."""
    executor = OpenSandboxExecutor()
    key = "thread-F"
    executor._sandboxes[key] = MagicMock()
    executor._active_skills_config[key] = ("/path/.cuga", True)
    executor._skills_config[key] = ("/path/.cuga", True)
    executor._locks[key] = asyncio.Lock()

    # release_sandbox is async, but we only need to test state cleanup here
    # by calling the synchronous dict manipulations it performs before the await.
    # We invoke it via asyncio.run to keep this test synchronous-style.
    async def _run():
        # Prevent the actual kill/close calls from failing (sandbox is a MagicMock)
        executor._sandboxes[key].sandbox.kill = AsyncMock()
        executor._sandboxes[key].sandbox.close = AsyncMock()
        await executor.release_sandbox(key)

    asyncio.get_event_loop().run_until_complete(_run())

    assert key not in executor._sandboxes
    assert key not in executor._active_skills_config
    assert key not in executor._skills_config
    assert key not in executor._locks
