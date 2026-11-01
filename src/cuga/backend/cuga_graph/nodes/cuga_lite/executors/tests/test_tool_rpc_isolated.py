"""Tool RPC over inherited pipes, ``run_command``'s tool bridge, and the ``subprocess`` Python backend."""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
import time
from pathlib import Path

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common import CodeWrapper
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.rpc_client import cuga_tools as wire
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.rpc_client.cuga_tb import (
    format_code_error,
    register_source,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.tool_rpc import SHELL_TOOLS, run_process
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.isolated import IsolatedPythonExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallBudgetExceeded

pytestmark = [pytest.mark.unit, pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipes and shells")]


async def _add(a: int, b: int):
    return {"sum": a + b}


async def _boom(**_):
    raise ValueError("server said no")


async def _spent(**_):
    raise ToolCallBudgetExceeded("max_tool_calls_per_run (3) reached")


TOOLS = {"add": _add, "boom": _boom, "spent": _spent}


# ── wire format ──────────────────────────────────────────────────────────────


def test_values_json_lacks_survive_the_round_trip():
    value = {
        "when": dt.datetime(2024, 5, 1, 9, 30),
        "day": dt.date(2024, 5, 1),
        "pair": (1, "a"),
        "ids": {3, 1, 2},
        "nested": [{"t": dt.time(8, 0)}],
    }
    assert wire.decode(wire.encode(value)) == value


def test_a_tool_search_result_keeps_its_names_across_the_wire():
    found = wire.decode(wire.encode(wire.FoundTools("sig a\nsig b", ["a", "b"])))
    assert isinstance(found, wire.FoundTools) and str(found) == "sig a\nsig b" and found.names == ["a", "b"]


# ── the channel ──────────────────────────────────────────────────────────────


def _python(script: str):
    return [sys.executable, "-c", script]


@pytest.mark.asyncio
async def test_a_child_calls_host_tools_and_gets_typed_errors():
    script = (
        "from cuga_tools import call, list_tools, ToolCallError, ToolCallBudgetExceeded\n"
        "print(call('add', a=2, b=3)['sum'], list_tools())\n"
        "for name, exc in (('boom', ToolCallError), ('nope', ToolCallError), ('spent', ToolCallBudgetExceeded)):\n"
        "    try:\n"
        "        call(name)\n"
        "    except exc as e:\n"
        "        print(type(e).__name__, e)\n"
    )
    out, err, rc = await run_process(_python(script), timeout=30, tools=TOOLS)
    assert rc == 0, err
    lines = out.strip().splitlines()
    assert lines[0] == "5 ['add', 'boom', 'spent']"
    assert lines[1] == "ToolCallError boom failed: server said no"
    assert lines[2].startswith("ToolCallError Unknown tool 'nope'")
    assert lines[3] == "ToolCallBudgetExceeded max_tool_calls_per_run (3) reached"


@pytest.mark.asyncio
async def test_concurrent_calls_from_one_child_are_all_answered():
    script = (
        "import asyncio\n"
        "from cuga_tools import acall\n"
        "async def main():\n"
        "    r = await asyncio.gather(*[acall('add', a=i, b=1) for i in range(20)])\n"
        "    print(sum(x['sum'] for x in r))\n"
        "asyncio.run(main())\n"
    )
    out, err, rc = await run_process(_python(script), timeout=30, tools=TOOLS)
    assert rc == 0, err
    assert out.strip() == str(sum(i + 1 for i in range(20)))


@pytest.mark.asyncio
async def test_without_tools_no_channel_is_opened():
    out, _, rc = await run_process(
        _python("import os; print(os.environ.get('CUGA_TOOL_RPC_OUT'))"), timeout=30, tools=None
    )
    assert rc == 0 and out.strip() == "None"


@pytest.mark.asyncio
async def test_a_command_that_outlives_its_timeout_is_killed():
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await run_process(["/bin/sh", "-c", "sleep 30"], timeout=1, tools=TOOLS)
    assert time.monotonic() - started < 10


# ── run_command: bash, with the tools when the caller allows it ──────────────


@pytest.mark.asyncio
async def test_run_command_scripts_reach_the_tools_only_when_bridged(tmp_path, monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local import LocalSandboxExecutor

    executor = LocalSandboxExecutor()

    async def no_venv(workspace_root):
        return tmp_path / ".venv"

    monkeypatch.setattr(executor, "_ensure_workspace_venv", no_venv)
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_sandbox_executor.local_thread_workspace_root",
        lambda thread_id: tmp_path,
    )
    run = executor.create_run_command_tool("t")

    token = SHELL_TOOLS.set(dict(TOOLS))
    try:
        assert "3" in await run("echo ${BASH_VERSION:0:1}")  # bash, not sh
        cli = await run("cuga-tool add '{\"a\": 2, \"b\": 3}'")
        script = await run(
            "printf 'from cuga_tools import call\\nprint(call(\"add\", a=10, b=5)[\"sum\"])\\n' > s.py && python3 s.py"
        )
    finally:
        SHELL_TOOLS.reset(token)
    assert '"sum": 5' in cli
    assert script.strip() == "15"
    assert "command not found" in await run("cuga-tool add '{}' 2>&1 || true")


# ── the subprocess Python backend ────────────────────────────────────────────


async def _run_isolated(code: str, context=None, timeout: float = 10):
    return await IsolatedPythonExecutor().execute_for_cuga_lite(
        CodeWrapper.wrap_code(code), dict(context or {}), thread_id="tests", timeout=timeout
    )


@pytest.mark.asyncio
async def test_a_block_calls_tools_reads_and_leaves_variables():
    out, new = await _run_isolated(
        "import statistics, datetime\nr = await add(a=prior, b=1)\nwhen = datetime.date(2024, 1, 2)\n"
        "print(r['sum'], statistics.mean([1, 3]), json.dumps(sorted(prior_set)))",
        {"add": _add, "prior": 41, "prior_set": {2, 1}, "a_module": dt},
    )
    assert out.strip() == "42 2 [1, 2]", "json is predefined, like in-process; a set arrives as a set"
    assert new == {"r": {"sum": 42}, "when": dt.date(2024, 1, 2)}


@pytest.mark.asyncio
async def test_pandas_is_there_without_an_import_like_in_process():
    out, _ = await _run_isolated("print(pd.DataFrame({'a': [1, 2]}).a.sum())", timeout=30)
    assert out.strip() == "3"


@pytest.mark.asyncio
async def test_an_error_shows_only_the_failing_statement_and_keeps_earlier_variables():
    out, new = await _run_isolated("x = 5\nd = {'a': 1}\nprint('before')\ny = d['songs']")
    assert out.startswith("Output before error:\nbefore")
    assert "Error during execution: KeyError('songs')" in out
    assert ">>> y = d['songs']" in out
    assert "cuga_worker" not in out and "asyncio" not in out, "no executor frames"
    assert new == {"x": 5, "d": {"a": 1}}


@pytest.mark.asyncio
async def test_a_failing_tool_raises_its_reason_inside_the_code():
    out, _ = await _run_isolated("await boom()", {"boom": _boom})
    assert "ToolCallError: boom failed: server said no" in out


@pytest.mark.asyncio
async def test_an_unknown_name_gets_the_tool_name_correction():
    out, _ = await _run_isolated("await ad(a=1, b=2)", {"add": _add})
    assert "NameError" in out and "[tool-name correction]" in out


@pytest.mark.asyncio
async def test_a_stalled_block_times_out_and_keeps_what_it_computed():
    out, new = await _run_isolated("done = [1, 2]\nawait asyncio.sleep(60)", timeout=1)
    assert "timed out after 1 seconds" in out and "kept variables from this block: done" in out
    assert new == {"done": [1, 2]}


@pytest.mark.asyncio
async def test_a_cpu_bound_loop_is_stopped():
    started = time.monotonic()
    out, new = await _run_isolated("n = 0\nprint('start')\nwhile True:\n    n += 1", timeout=1)
    assert time.monotonic() - started < 8
    assert "timed out" in out and "start" in out, "what it printed before the loop still arrives"


@pytest.mark.asyncio
async def test_exit_ends_the_block_and_keeps_variables():
    out, new = await _run_isolated("a = 1\nexit()\nb = 2")
    assert out.startswith("Block ended early") and new == {"a": 1}


@pytest.mark.asyncio
async def test_the_child_sees_no_host_credentials(monkeypatch):
    monkeypatch.setenv("FAKE_PROVIDER_API_KEY", "secret")
    out, _ = await _run_isolated("import os\nprint(os.environ.get('FAKE_PROVIDER_API_KEY'))")
    assert out.strip() == "None"


@pytest.mark.asyncio
async def test_under_an_os_sandbox_there_is_no_network_and_no_writes_outside_scratch(tmp_path):
    if not IsolatedPythonExecutor().os_sandboxed:
        pytest.skip("no OS sandbox on this host")
    target = tmp_path / "escape.txt"
    out, _ = await _run_isolated(f"open({str(target)!r}, 'w').write('x')")
    assert "PermissionError" in out and not target.exists()
    out, _ = await _run_isolated(
        "import socket\ns = socket.socket()\ns.settimeout(3)\ns.connect(('1.1.1.1', 80))\nprint('connected')"
    )
    assert "connected" not in out and "Error during execution" in out


@pytest.mark.asyncio
async def test_the_code_executor_routes_the_subprocess_backend_and_stores_variables(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor

    manager = MagicMock()
    manager.get_variable_names.return_value = []
    state = SimpleNamespace(variables_manager=manager, thread_id="tests")
    output, new = await CodeExecutor.eval_with_tools_async(
        "total = (await add(a=1, b=2))['sum']\nprint(total)",
        {"add": _add},
        state,
        mode="subprocess",
        timeout=10,
    )
    assert output.startswith("3") and new == {"total": 3}
    assert manager.add_variable.called


# ── in-process tracebacks ────────────────────────────────────────────────────


def test_in_process_tracebacks_show_the_failing_statements_only():
    code = CodeWrapper.wrap_code(
        "def f(v):\n    return v['missing']\nrows = [{'k': 1}]\nout = [f(r) for r in rows]"
    )
    filename = register_source(code)
    namespace: dict = {}
    exec(compile(code, filename, "exec"), namespace)
    try:
        asyncio.run(namespace["_async_main"]())
    except KeyError as exc:
        text = format_code_error(exc)
    assert ">>> out = [f(r) for r in rows]" in text
    assert ">>> return v['missing']  (in f)" in text
    assert text.endswith("KeyError: 'missing'")
    assert "File " not in text


@pytest.mark.asyncio
async def test_a_trailing_expression_is_echoed_but_not_none():
    out, _ = await _run_isolated("async def main():\n    print(15)\n\nawait main()")
    assert out.strip() == "15"
    out, _ = await _run_isolated("x = [1, 2]\nlen(x)")
    assert out.strip() == "2"


@pytest.mark.asyncio
async def test_code_cannot_import_the_worker_channel():
    out, _ = await _run_isolated("from cuga_tools import call")
    assert "ModuleNotFoundError" in out


# ── the subprocess backend shares run_command's workspace ────────────────────


def _plan(shell_backend: str):
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import ExecutionPlan

    return ExecutionPlan(
        requested_backend="subprocess",
        python_backend="subprocess",
        shell_backend=shell_backend,
        filesystem_backend="none",
    )


@pytest.mark.asyncio
async def test_a_block_starts_in_the_host_shells_workspace(tmp_path, monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import ExecutionRouter

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ExecutionRouter, "resolve", staticmethod(lambda *_a, **_k: _plan("native")))
    out, _ = await _run_isolated(
        "open('note.txt', 'w').write('from python')\nprint(os.getcwd(), os.environ['HOME'])"
    )
    workspace = (tmp_path / "cuga_workspace" / "tests").resolve()
    assert (workspace / "note.txt").read_text() == "from python"
    cwd, home = out.split()
    assert Path(cwd).resolve() == workspace
    assert Path(home).resolve() != workspace  # caches and HOME stay in the private scratch dir
    assert not (workspace / ".sandbox.sb").exists()


def test_a_remote_shell_keeps_the_private_scratch_dir(tmp_path, monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import ExecutionRouter
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.isolated import isolated_executor

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ExecutionRouter, "resolve", staticmethod(lambda *_a, **_k: _plan("opensandbox")))
    assert isolated_executor._shared_workspace("tests") is None
    assert not (tmp_path / "cuga_workspace").exists()


def test_sandbox_launchers_make_the_workspace_writable_and_start_there(tmp_path):
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.isolated import isolated_executor

    workdir, workspace = tmp_path / "scratch", tmp_path / "ws"
    workdir.mkdir()
    isolated_executor._sandbox_argv("seatbelt", workdir, workspace)
    policy = (workdir / ".sandbox.sb").read_text()
    assert f'(subpath "{workdir}")' in policy and f'(subpath "{workspace}")' in policy
    argv = isolated_executor._sandbox_argv("bwrap", workdir, workspace)
    assert ["--bind", str(workspace), str(workspace)] == argv[
        argv.index(str(workspace)) - 1 : argv.index(str(workspace)) + 2
    ]
    assert argv[argv.index("--chdir") + 1] == str(workspace)
    assert isolated_executor._sandbox_argv("bwrap", workdir)[-1] == str(workdir)
