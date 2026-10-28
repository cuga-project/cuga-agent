"""One execd container holds every thread, so a bare ``/workspace`` is not safe.

The shared ``normalize_shell_command_paths`` rewrites ``/workspace/foo`` to
``./foo`` but leaves the root itself alone — correct for opensandbox, where the
sandbox is per thread and that root already belongs to the caller. Under execd
it would list every other thread's directory.
"""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd.execd_executor import ExecdExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd.kernel_templates import (
    filesystem_tools_code,
    shell_tool_code,
)

pytestmark = pytest.mark.unit


def _confine(cmd: str, thread_id: str = "thread-a") -> str:
    return ExecdExecutor._confine_to_thread_workspace(cmd, thread_id)


def test_bare_workspace_becomes_the_thread_directory():
    assert _confine("ls /workspace") == "ls /workspace/thread-a"


def test_workspace_as_an_argument_among_others():
    assert _confine("du -sh /workspace | sort") == "du -sh /workspace/thread-a | sort"


def test_a_path_under_workspace_is_relative_to_the_thread_directory():
    # The agent's /workspace is its own directory, so /workspace/x is ./x from there.
    assert _confine("python /workspace/x.py") == "python ./x.py"
    assert _confine("cat /workspace/a/b.txt > /workspace/c") == "cat ./a/b.txt > ./c"


def test_unrelated_words_are_left_alone():
    for cmd in ("echo my/workspace", "echo workspace", "echo /workspaces/x"):
        assert _confine(cmd) == cmd


def test_threads_get_different_roots():
    assert _confine("ls /workspace", "one") != _confine("ls /workspace", "two")


def _kernel_shell(root: str) -> dict:
    """The kernel's run_command definitions, exec'd as the kernel would."""
    kernel: dict = {}
    exec(shell_tool_code(root), kernel)
    return kernel


@pytest.mark.parametrize(
    "cmd",
    [
        "python /workspace/x.py",
        "ls /workspace",
        "cd /workspace && ls",
        "ls '/workspace/a b'",
        "ls /workspace/",
        "echo my/workspace",
        "ls /workspaces/x",
        "ls /tmp/cuga_workspace/abc/x",
    ],
)
def test_kernel_run_command_reads_paths_like_the_host_tool(cmd):
    # Both run_command variants share one rewrite, so they cannot drift.
    kernel = _kernel_shell("/workspace/thread-a")
    assert kernel["_cuga_sh_rewrite_paths"](cmd, "/workspace/thread-a") == _confine(cmd)


async def test_kernel_run_command_finds_a_file_write_file_wrote(tmp_path):
    """write_file('/workspace/x.py') then run_command('python /workspace/x.py') must find the file."""
    root = str(tmp_path / "thread-a")
    (tmp_path / "thread-a").mkdir()
    kernel: dict = {}
    exec(filesystem_tools_code(root), kernel)
    exec(shell_tool_code(root), kernel)

    await kernel["write_file"]("/workspace/x.py", "print('hello from x')")

    assert (await kernel["run_command"]("python3 /workspace/x.py")).strip() == "hello from x"
    assert (await kernel["run_command"]("ls /workspace")).split() == ["x.py"]


def test_kernel_rewrite_helpers_leave_no_other_globals():
    names = {k for k in _kernel_shell("/workspace/t") if not k.startswith("__")}
    assert {"re", "VIRTUAL_WORKSPACE_ROOT", "normalize_shell_command_paths"}.isdisjoint(names)


async def test_kernel_run_command_reports_a_failure_with_its_exit_code(tmp_path):
    """A non-zero exit is visible even when stderr is empty (review item 33)."""
    kernel = _kernel_shell(str(tmp_path))

    assert await kernel["run_command"]("false") == "\n[stderr]\n(exit code 1)"
    assert await kernel["run_command"]("echo boom >&2; exit 3") == "\n[stderr]\nboom\n(exit code 3)"
    assert (await kernel["run_command"]("echo ok")).strip() == "ok"


async def test_kernel_run_command_stops_a_hung_command(tmp_path):
    kernel: dict = {}
    exec(shell_tool_code(str(tmp_path), timeout=1), kernel)

    out = await kernel["run_command"]("sleep 10")

    assert "timed out after 1s" in out and "[stderr]" in out
