"""Strict-mode code gets an asyncio facade, not the real module.

The real asyncio module exposes process-spawn entry points and the running event
loop. In strict mode, both the injected ``asyncio`` global and ``import asyncio``
return a facade that keeps only sleep / gather / wait_for / TimeoutError. The
strict validator also rejects frame and await-chain attributes, because a
coroutine made in a real module leads back to real builtins and the loop.
"""

import asyncio
import importlib
import inspect
import keyword
import sys
from collections import deque

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.code_wrapper import CodeWrapper
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.restricted_environment import (
    RestrictedEnvironment,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.security import SecurityValidator
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_executor import LocalExecutor
from cuga.config import settings

# AGENTS.md: every test in this module is a fast, isolated unit test.
pytestmark = pytest.mark.unit

FACADE_MEMBERS = {'sleep', 'gather', 'wait_for', 'TimeoutError'}
SPAWN_OR_LOOP_NAMES = [
    'create_subprocess_exec',
    'create_subprocess_shell',
    'subprocess',
    'get_running_loop',
    'get_event_loop',
    'create_task',
    'to_thread',
    'events',
    'base_events',
]


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(settings.execution, "enforce_code_security", True)


def _strict_globals():
    restricted_import = RestrictedEnvironment.create_restricted_import(LocalExecutor.ALLOWED_MODULES)
    safe_builtins = RestrictedEnvironment.create_safe_builtins(restricted_import)
    return RestrictedEnvironment.create_restricted_globals(safe_builtins, {}), restricted_import


def _run_strict(code: str, wrap) -> dict:
    wrapped = wrap(code)
    SecurityValidator.validate_imports(code)
    SecurityValidator.validate_wrapped_code(wrapped)
    context_locals: dict = {}
    asyncio.run(LocalExecutor().execute(wrapped, context_locals=context_locals, timeout=10))
    return context_locals


def test_injected_asyncio_is_facade(strict):
    restricted_globals, _ = _strict_globals()
    facade = restricted_globals["asyncio"]
    assert facade is not asyncio
    assert {n for n in dir(facade) if not n.startswith('_')} == FACADE_MEMBERS
    for name in SPAWN_OR_LOOP_NAMES:
        assert not hasattr(facade, name), name


def test_import_asyncio_returns_facade(strict):
    _, restricted_import = _strict_globals()
    facade = restricted_import("asyncio")
    assert facade is not asyncio
    assert {n for n in dir(facade) if not n.startswith('_')} == FACADE_MEMBERS


@pytest.mark.parametrize("name", ["asyncio.subprocess", "asyncio.events", "asyncio.base_events"])
def test_import_asyncio_submodule_refused(strict, name):
    _, restricted_import = _strict_globals()
    with pytest.raises(ImportError):
        restricted_import(name)


@pytest.mark.parametrize(
    "attr", ["cr_frame", "f_builtins", "cr_await", "gi_yieldfrom", "gi_frame", "ag_frame", "ag_await"]
)
def test_frame_and_await_chain_rejected(strict, attr):
    code = f"c = asyncio.sleep(0)\nx = c.{attr}\n"
    with pytest.raises(PermissionError):
        SecurityValidator.validate_wrapped_code(CodeWrapper.wrap_code(code))
    with pytest.raises(PermissionError):
        SecurityValidator.validate_wrapped_code(CodeWrapper.wrap_code_for_code_agent(code))


@pytest.mark.parametrize(
    "code",
    [
        "x = asyncio.create_subprocess_exec\n",
        "x = asyncio.create_subprocess_shell\n",
        "x = loop.subprocess_exec\n",
        "x = loop.subprocess_shell\n",
        "x = asyncio.get_running_loop()\n",
        "x = asyncio.get_event_loop()\n",
        "x = fut.get_loop()\n",
        "x = fut._loop\n",
        "x = get_running_loop()\n",
        "x = loop._make_subprocess_transport\n",
    ],
)
def test_backstop_rejects_spawn_and_loop_names(strict, code):
    with pytest.raises(PermissionError):
        SecurityValidator.validate_wrapped_code(CodeWrapper.wrap_code(code))


@pytest.mark.parametrize("attr", ["get_loop", "_loop"])
def test_loop_accessors_rejected_without_backstop(strict, monkeypatch, attr):
    # A facade coroutine stepped by hand yields the real Future it awaits, so the loop
    # accessors are part of the required set, not only the backstop.
    monkeypatch.setattr(SecurityValidator, "FORBIDDEN_SPAWN_ATTRS", frozenset())
    code = f"c = asyncio.sleep(1)\nf = c.send(None)\nx = f.{attr}\n"
    for wrap in (CodeWrapper.wrap_code, CodeWrapper.wrap_code_for_code_agent):
        with pytest.raises(PermissionError):
            SecurityValidator.validate_wrapped_code(wrap(code))


def test_facade_timeout_error_is_asyncio_class(strict):
    # On 3.10 asyncio.TimeoutError is not the builtin, and wait_for raises the asyncio one.
    restricted_globals, restricted_import = _strict_globals()
    assert restricted_globals["asyncio"].TimeoutError is asyncio.TimeoutError
    assert restricted_import("asyncio").TimeoutError is asyncio.TimeoutError


def test_facade_left_in_locals_is_replaced(strict):
    restricted_import = RestrictedEnvironment.create_restricted_import(LocalExecutor.ALLOWED_MODULES)
    safe_builtins = RestrictedEnvironment.create_safe_builtins(restricted_import)
    stale = object()
    restricted_globals = RestrictedEnvironment.create_restricted_globals(safe_builtins, {"asyncio": stale})
    assert restricted_globals["asyncio"] is not stale
    assert {n for n in dir(restricted_globals["asyncio"]) if not n.startswith('_')} == FACADE_MEMBERS


ASYNCIO_USE = """\
r = await asyncio.gather(asyncio.sleep(0.01, 1), asyncio.sleep(0, 2))
try:
    await asyncio.wait_for(asyncio.sleep(1), 0.01)
    timed_out = False
except asyncio.TimeoutError:
    timed_out = True
"""


@pytest.mark.parametrize(
    "wrap", [CodeWrapper.wrap_code, CodeWrapper.wrap_code_for_code_agent], ids=["cuga_lite", "code_agent"]
)
def test_facade_supports_generated_code(strict, wrap):
    out = _run_strict(ASYNCIO_USE, wrap)
    assert out["r"] == [1, 2]
    assert out["timed_out"] is True


def test_from_asyncio_import_works(strict):
    code = "from asyncio import sleep, TimeoutError\nv = await sleep(0, 7)\nok = TimeoutError is asyncio.TimeoutError\n"
    out = _run_strict(code, CodeWrapper.wrap_code)
    assert out["v"] == 7
    assert out["ok"] is True


def test_relaxed_mode_keeps_real_asyncio(monkeypatch):
    monkeypatch.setattr(settings.execution, "enforce_code_security", False)
    restricted_globals, restricted_import = _strict_globals()
    assert restricted_globals["asyncio"] is asyncio
    assert restricted_import("asyncio") is asyncio


# How far the reach-through walk follows modules and classes. Depth 1 is the
# allowlisted module's own functions (asyncio.create_subprocess_*). Depth 3 also
# reaches a class method two modules down (BaseEventLoop.subprocess_exec).
_WALK_DEPTH = 3

# Attribute names that are process-spawn callables on a specific owner.
# Kept per owner so a shared name like "run" does not pull in asyncio.run.
_SPAWN_OWNERS = (
    (
        "os",
        (
            "system",
            "popen",
            "execl",
            "execle",
            "execlp",
            "execlpe",
            "execv",
            "execve",
            "execvp",
            "execvpe",
            "posix_spawn",
            "posix_spawnp",
            "spawnl",
            "spawnle",
            "spawnlp",
            "spawnlpe",
            "spawnv",
            "spawnve",
            "spawnvp",
            "spawnvpe",
        ),
    ),
    ("subprocess", ("Popen", "call", "run", "check_call", "check_output")),
    ("asyncio", ("create_subprocess_exec", "create_subprocess_shell")),
    ("loop", ("subprocess_exec", "subprocess_shell")),
    ("multiprocessing", ("Process",)),
    ("pty", ("spawn",)),
    ("_posixsubprocess", ("fork_exec",)),
)


def _record_posix_fork(monkeypatch) -> list:
    """Record ``_posixsubprocess.fork_exec``. Skip that check off POSIX."""
    if sys.platform == "win32":
        return []
    posix = pytest.importorskip("_posixsubprocess")
    calls: list = []
    real = posix.fork_exec

    def recording_fork(*args, **kwargs):
        calls.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(posix, "fork_exec", recording_fork)
    # subprocess binds the function at import time, so patch that name too or the
    # recorder never sees a real spawn.
    subprocess_mod = importlib.import_module("subprocess")
    if getattr(subprocess_mod, "_fork_exec", None) is real:
        monkeypatch.setattr(subprocess_mod, "_fork_exec", recording_fork)
    return calls


def _spawn_attempt(executable: str, canary: str) -> str:
    script = f"open({canary!r}, 'w').write('x')"
    return (
        f"proc = await asyncio.create_subprocess_exec({executable!r}, '-c', {script!r})\nawait proc.wait()\n"
    )


def _run_strict_chain(code: str, wrap) -> None:
    wrapped = wrap(code)
    SecurityValidator.validate_imports(code)
    SecurityValidator.validate_wrapped_code(wrapped)
    asyncio.run(LocalExecutor().execute(wrapped, context_locals={}, timeout=10))


@pytest.mark.parametrize("empty_spawn_denylist", [False, True])
def test_strict_execution_cannot_start_host_process(strict, tmp_path, monkeypatch, empty_spawn_denylist):
    """Both wrappers run the strict chain. No child process, on every OS.

    The chain is wrap, validate_imports, validate_wrapped_code, then
    LocalExecutor.execute. A child would write a canary file under tmp_path.
    That file must not exist. Recorders on the real spawn entry points must not
    be called. The _posixsubprocess recorder is skipped on Windows.

    With the spawn-name denylist in place, validate_wrapped_code raises
    PermissionError before execution. With FORBIDDEN_SPAWN_ATTRS emptied, that
    name check does not run and the facade raises AttributeError. The canary
    assertion still holds.
    """
    if empty_spawn_denylist:
        # raising=False: on a tree without the denylist the case still runs and fails
        # on the real behavior, not on test setup.
        monkeypatch.setattr(SecurityValidator, "FORBIDDEN_SPAWN_ATTRS", frozenset(), raising=False)

    canary = tmp_path / "canary"
    code = _spawn_attempt(sys.executable, str(canary))
    exec_calls: list = []
    shell_calls: list = []
    real_exec = asyncio.create_subprocess_exec
    real_shell = asyncio.create_subprocess_shell

    async def recording_exec(*args, **kwargs):
        exec_calls.append(True)
        return await real_exec(*args, **kwargs)

    async def recording_shell(*args, **kwargs):
        shell_calls.append(True)
        return await real_shell(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", recording_shell)
    fork_calls = _record_posix_fork(monkeypatch)

    accepted = AttributeError if empty_spawn_denylist else PermissionError
    seen: list[BaseException] = []
    for wrap in (CodeWrapper.wrap_code, CodeWrapper.wrap_code_for_code_agent):
        try:
            _run_strict_chain(code, wrap)
        except accepted as e:
            seen.append(e)

    assert [type(e) for e in seen] == [accepted, accepted]
    if empty_spawn_denylist:
        # The facade, not some other AttributeError, must be what stopped the spawn.
        assert all("_AsyncioFacade" in str(e) for e in seen), [str(e) for e in seen]
    assert not canary.exists()
    assert exec_calls == []
    assert shell_calls == []
    assert fork_calls == []


def _spawn_callable_ids() -> set:
    import os
    import subprocess

    owners = {
        "os": os,
        "subprocess": subprocess,
        "asyncio": asyncio,
        "loop": asyncio.base_events.BaseEventLoop,
        "multiprocessing": importlib.import_module("multiprocessing"),
    }
    try:
        owners["pty"] = importlib.import_module("pty")
    except ImportError:
        pass
    try:
        owners["_posixsubprocess"] = importlib.import_module("_posixsubprocess")
    except ImportError:
        pass
    ids: set = set()
    for owner_name, attr_names in _SPAWN_OWNERS:
        owner = owners.get(owner_name)
        if owner is None:
            continue
        for name in attr_names:
            value = getattr(owner, name, None)
            if value is not None:
                ids.add(value)
    return ids


def _validator_allows(chain: tuple[str, ...]) -> bool:
    if any(keyword.iskeyword(part) for part in chain):
        return False
    code = f"x = {'.'.join(chain)}\n"
    try:
        SecurityValidator.validate_wrapped_code(CodeWrapper.wrap_code(code))
    except (PermissionError, SyntaxError):
        return False
    return True


def _reachable_spawn_chains() -> list[str]:
    """Attribute chains from the strict namespace to a spawn callable.

    Roots are the strict globals and ``restricted_import(name)`` for each
    allowlisted module. Links the strict validator rejects are not followed.
    A hit is an identity match, and only when the validator accepts the chain.
    """
    spawn_ids = _spawn_callable_ids()
    restricted_globals, restricted_import = _strict_globals()
    allowed = set(LocalExecutor.ALLOWED_MODULES) | set(SecurityValidator.ALLOWED_IMPORTS)
    roots: list[tuple[str, object]] = []
    for name in sorted(allowed):
        try:
            roots.append((name, restricted_import(name)))
        except ImportError:
            continue
    for name, value in restricted_globals.items():
        if isinstance(name, str) and name.isidentifier():
            roots.append((name, value))

    hits: list[str] = []
    seen: set[int] = set()
    pending: deque[tuple[object, tuple[str, ...], int]] = deque((obj, (name,), 0) for name, obj in roots)
    while pending:
        obj, chain, depth = pending.popleft()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        func = getattr(obj, "__func__", None)
        try:
            is_spawn = obj in spawn_ids or func in spawn_ids
        except TypeError:
            is_spawn = False
        if is_spawn and _validator_allows(chain):
            hits.append(".".join(chain))
        if depth >= _WALK_DEPTH or not (inspect.ismodule(obj) or inspect.isclass(obj)):
            continue
        for attr in dir(obj):
            if (attr.startswith("__") and attr.endswith("__")) or not attr.isidentifier():
                continue
            if keyword.iskeyword(attr):
                continue
            child_chain = chain + (attr,)
            if not _validator_allows(child_chain):
                continue
            try:
                child = getattr(obj, attr)
            except Exception:
                continue
            pending.append((child, child_chain, depth + 1))
    return hits


def test_allowlisted_modules_expose_no_spawn_callable(strict):
    """No allowlisted module exposes a process-spawn callable the validator accepts.

    The walk starts at the strict globals and at restricted_import(name). On
    origin/main 13ce8126 it reports the asyncio.create_subprocess_* chains.
    """
    hits = _reachable_spawn_chains()
    assert not hits, "reachable spawn callables:\n" + "\n".join(hits)
