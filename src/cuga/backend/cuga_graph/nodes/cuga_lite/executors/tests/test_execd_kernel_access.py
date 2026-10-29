"""Agent code under execd must not reach the kernel's own helpers (review item 18).

The kernel is long-lived and shares its globals with CUGA's helpers: the tool
callback client, the shell tool's ``os``, the block runner's ``sys``. Through
them, code could run a tool that tool approval never saw, or a shell with the
shell tool off. Two things stop it: the tool callback answers only the tools a
block names, and code naming a kernel helper is refused before it is sent.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.code_wrapper import CodeWrapper
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import ExecdExecutor, tool_callback
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd.execd_executor import _check_kernel_access
from cuga.backend.cuga_graph.state.agent_state import VariablesManager
from cuga.config import settings

pytestmark = pytest.mark.unit


def _wrapped(code: str, fake_datetime: str | None = None) -> str:
    return CodeWrapper.wrap_code(code, fake_datetime=fake_datetime)


@pytest.mark.parametrize(
    "code",
    [
        # The review's approval bypass: call a tool by a name built at runtime.
        "await _cuga_cb_acall('send_' + 'email', [], {'to': 'x'})",
        "_cuga_cb_call('send_email', [], {})",
        # Shell and modules through the kernel tools' own aliases.
        "_cuga_sh_os.system('id')",
        "p = _CugaPath('/etc/passwd')",
        "_cuga_r_sys.modules",
        # Generic routes to the kernel's globals or a shell.
        "globals()['_cuga_os']",
        "vars()['x']",
        "__builtins__",
        "import asyncio\nawait asyncio.create_subprocess_shell('id')",
        "import asyncio\nawait asyncio.create_subprocess_exec('id')",
        "import asyncio\nasyncio.subprocess",
        "global _cuga_x\n_cuga_x = 1",
        "import json\njson._cuga_x",
    ],
)
def test_code_reaching_kernel_helpers_is_refused(code):
    with pytest.raises(PermissionError):
        _check_kernel_access(_wrapped(code))


@pytest.mark.parametrize(
    "code",
    [
        "x = await crm_get_contact(id=1)\nprint(x)",
        "r = await _fc_tools['my-tool.v2'](q='hi')",
        "m = _internal_re.match('a', 'abc')",
        "import asyncio\nawait asyncio.gather(*[t() for t in []])",
        "def helper(v):\n    return v * 2\nprint(helper(2))",
        # A name the code binds itself is its own, not the kernel's.
        "_cuga_mine = 3\nprint(_cuga_mine)",
    ],
)
def test_ordinary_agent_code_passes(code):
    _check_kernel_access(_wrapped(code))


def test_the_frozen_clock_wrapper_passes():
    # CodeWrapper binds its own _cuga_* names in benchmark mode; they are locals of the block.
    _check_kernel_access(_wrapped("import datetime\nprint(datetime.datetime.now())", "2024-01-01T00:00:00"))


def test_relaxed_execution_skips_the_check(monkeypatch):
    monkeypatch.setattr(settings.execution, "enforce_code_security", False, raising=False)
    _check_kernel_access(_wrapped("_cuga_sh_os.system('id')"))


async def _execute(code: str, tools: dict, monkeypatch) -> str:
    """Run execute_for_cuga_lite against a fake sandbox; return the code that was sent."""
    sent: list[str] = []

    async def fake_run_remote(kernel_code, thread_id):
        sent.append(kernel_code)
        return ""

    monkeypatch.setattr(ExecdExecutor, "_run_remote", staticmethod(fake_run_remote))
    monkeypatch.setattr(tool_callback, "callback_url", lambda: "http://cuga.test:7860")
    state = Mock()
    state.variables_manager = VariablesManager()
    state.service_scope = None
    await ExecdExecutor().execute_for_cuga_lite(_wrapped(code), tools, state, "t1")
    return sent[0]


async def test_only_tools_the_block_names_get_a_stub(monkeypatch):
    async def crm_get_contact(**kwargs):
        pass  # pragma: no cover

    async def send_email(**kwargs):
        pass  # pragma: no cover

    code = await _execute(
        "x = await crm_get_contact(id=1)",
        {"crm_get_contact": crm_get_contact, "send_email": send_email},
        monkeypatch,
    )

    assert "_cuga_cb_acall('crm_get_contact'" in code
    assert "send_email" not in code


async def test_a_tool_the_block_does_not_name_is_refused_by_the_callback(monkeypatch):
    """Even reached some other way, CUGA does not run a tool outside the block's namespace."""
    registered: list[dict] = []
    real = tool_callback.registered_block

    def spy(namespace, thread_id, timeout):
        registered.append(dict(namespace))
        return real(namespace, thread_id, timeout)

    monkeypatch.setattr(tool_callback, "registered_block", spy)

    async def crm_get_contact(**kwargs):
        pass  # pragma: no cover

    async def send_email(**kwargs):
        pass  # pragma: no cover

    await _execute(
        "x = await crm_get_contact(id=1)",
        {"crm_get_contact": crm_get_contact, "send_email": send_email},
        monkeypatch,
    )

    assert "crm_get_contact" in registered[0]
    assert "send_email" not in registered[0]


async def test_code_reaching_a_kernel_helper_is_not_sent(monkeypatch):
    async def send_email(**kwargs):
        pass  # pragma: no cover

    with pytest.raises(RuntimeError, match="reserved for the sandbox runtime"):
        await _execute(
            "await _cuga_cb_acall('send_' + 'email', [], {})", {"send_email": send_email}, monkeypatch
        )


def test_bootstrap_leaves_no_helpers_in_the_kernel_globals(tmp_path, monkeypatch):
    """os, site, importlib and the urllib aliases stay inside the bootstrap function."""
    import asyncio

    captured: list[str] = []

    async def fake_execute(context_id, code):
        captured.append(code)
        return ""

    monkeypatch.setattr(ExecdExecutor, "_execute", staticmethod(fake_execute))
    asyncio.run(ExecdExecutor._bootstrap_context("ctx", "thread-a"))
    bootstrap = captured[0].replace('"/workspace"', repr(str(tmp_path)))

    kernel: dict = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
    monkeypatch.setattr("os.environ", dict())
    exec(bootstrap, kernel)

    leaked = {k for k in kernel if not k.startswith("__")}
    assert leaked == set()


async def test_a_block_that_never_reports_is_an_error(monkeypatch):
    """A kernel that dies before the runner reports is not a success (review item 37)."""

    async def dead_kernel(kernel_code, thread_id):
        return "partial line\n"

    monkeypatch.setattr(ExecdExecutor, "_run_remote", staticmethod(dead_kernel))
    monkeypatch.setattr(tool_callback, "callback_url", lambda: "http://cuga.test:7860")
    state = Mock()
    state.variables_manager = VariablesManager()
    state.service_scope = None

    out, new_vars = await ExecdExecutor().execute_for_cuga_lite(_wrapped("x = 1"), {}, state, "t1")

    assert out.startswith(
        "Output before error:\npartial line\n\nError during execution: the code block did not finish"
    )
    assert new_vars == {}


# Review item 18: routes that got past the name check. The check now refuses
# them, and the block runs in a namespace of its own, so they fail in the
# kernel too, as they do under the local executor.
_BYPASSES = [
    # A _cuga name bound in a nested scope only, read at the block's level.
    "def _u():\n    _cuga_os = None\nprint(_cuga_os.listdir('/workspace'))",
    # A runtime-built name through an alias of eval.
    "e = eval\nprint(e('_cuga_' + 'os'))",
    "x = exec",
    "c = compile('1', 's', 'eval')",
    # A shell with the shell tool off.
    "import asyncio\nawait asyncio.get_running_loop().subprocess_shell(asyncio.SubprocessProtocol, 'id')",
    "import asyncio\nawait asyncio.get_running_loop().subprocess_exec(asyncio.SubprocessProtocol, 'id')",
]


@pytest.mark.parametrize("code", _BYPASSES)
def test_review_item_18_bypasses_are_refused(code):
    with pytest.raises(PermissionError):
        _check_kernel_access(_wrapped(code))


def _run_in_kernel(code: str, helpers: str = "", names: tuple[str, ...] = ()) -> dict:
    """Run a block as the kernel does, past the host check; return its outcome."""
    import ast
    import asyncio
    import contextlib
    import io
    import types

    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import block_runner

    source = helpers + block_runner.block_code(_wrapped(code), list(names)) + block_runner.runner_code(5)
    code_obj = compile(source, "<cell>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        coro = types.FunctionType(code_obj, {"__name__": "__main__"})()
        if coro is not None:
            asyncio.run(coro)
    outcome = block_runner.parse_outcome(out.getvalue())
    assert outcome is not None, out.getvalue()
    return outcome


_KERNEL_HELPERS = "import os as _cuga_sh_os\n"


@pytest.mark.parametrize(
    "code, error_type",
    [
        # Were the host check to miss them, the kernel's helpers are still out of reach.
        ("def _u():\n    _cuga_sh_os = None\nprint(_cuga_sh_os.getcwd())", "NameError"),
        ("e = eval\nprint(e('_cuga_' + 'sh_os'))", "NameError"),
        ("print(globals()['_cuga_sh_os'])", "NameError"),
        ("g = getattr\nprint(g)", "NameError"),
        ("import os\nprint(os)", "ImportError"),
        ("import subprocess", "ImportError"),
    ],
)
def test_the_block_does_not_see_the_kernel_helpers(code, error_type):
    outcome = _run_in_kernel(code, helpers=_KERNEL_HELPERS)
    assert outcome["status"] == "error"
    assert outcome["error_type"] == error_type


def test_the_block_sees_its_tools_and_allowed_modules():
    tools = "async def crm_get_contact(id):\n    return {'id': id}\n"
    outcome = _run_in_kernel(
        "import math, statistics\nc = await crm_get_contact(id=2)\nprint(c['id'], math.sqrt(4), statistics.mean([1, 3]))",
        helpers=_KERNEL_HELPERS + tools,
        names=("crm_get_contact",),
    )
    assert outcome["status"] == "ok", outcome
    assert outcome["stdout"].strip() == "2 2.0 2"
    assert outcome["locals"] == {"c": {"id": 2}}


def test_relaxed_execution_keeps_the_kernel_globals(monkeypatch):
    monkeypatch.setattr(settings.execution, "enforce_code_security", False, raising=False)
    outcome = _run_in_kernel("print(_cuga_sh_os.sep)", helpers=_KERNEL_HELPERS)
    assert outcome["status"] == "ok", outcome


@pytest.mark.parametrize(
    "code",
    [
        # Bound in a class body, read in its method: a method does not see class names.
        "class A:\n    _cuga_x = 1\n    def m(self):\n        return _cuga_x",
        # Bound in a comprehension, read after it.
        "y = [_cuga_i for _cuga_i in range(3)]\nprint(_cuga_i)",
        # Bound inside a lambda only, read outside it.
        "f = lambda _cuga_a: _cuga_a\nprint(_cuga_a)",
    ],
)
def test_a_cuga_name_bound_in_another_scope_is_refused(code):
    with pytest.raises(PermissionError, match="reserved for the sandbox runtime"):
        _check_kernel_access(_wrapped(code))


@pytest.mark.parametrize(
    "code",
    [
        # A closure reads its enclosing function's name.
        "def outer():\n    _cuga_x = 1\n    def inner():\n        return _cuga_x\n    return inner()\nprint(outer())",
        "def f(_cuga_p):\n    return _cuga_p\nprint(f(1))",
        "print([_cuga_i * 2 for _cuga_i in range(3)])",
        "try:\n    pass\nexcept Exception as _cuga_e:\n    print(_cuga_e)",
    ],
)
def test_a_cuga_name_bound_in_its_own_scope_passes(code):
    _check_kernel_access(_wrapped(code))


def test_in_kernel_file_tools_still_work_from_the_restricted_block(tmp_path):
    """The file tools keep their own os/glob; the block reaches them only by their tool names."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd.kernel_templates import (
        CHARGE_NAME,
        charged_tools_code,
        filesystem_tools_code,
    )

    helpers = (
        filesystem_tools_code(str(tmp_path))
        + f"async def {CHARGE_NAME}(name):\n    pass\n"
        + charged_tools_code(["read_file", "write_file"])
    )
    outcome = _run_in_kernel(
        "await write_file('/workspace/a.txt', 'hello')\nprint(await read_file('/workspace/a.txt'))",
        helpers=helpers,
        names=("read_file", "write_file"),
    )
    assert outcome["status"] == "ok", outcome
    assert outcome["stdout"].strip() == "hello"
    assert (tmp_path / "a.txt").read_text() == "hello"
