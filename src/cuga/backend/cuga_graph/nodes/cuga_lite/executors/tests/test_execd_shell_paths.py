"""One execd container holds every thread, so a bare ``/workspace`` is not safe.

The shared ``normalize_shell_command_paths`` rewrites ``/workspace/foo`` to
``./foo`` but leaves the root itself alone — correct for opensandbox, where the
sandbox is per thread and that root already belongs to the caller. Under execd
it would list every other thread's directory.
"""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd.execd_executor import ExecdExecutor

pytestmark = pytest.mark.unit


def _confine(cmd: str, thread_id: str = "thread-a") -> str:
    return ExecdExecutor._confine_to_thread_workspace(cmd, thread_id)


def test_bare_workspace_becomes_the_thread_directory():
    assert _confine("ls /workspace") == "ls /workspace/thread-a"


def test_workspace_as_an_argument_among_others():
    assert _confine("du -sh /workspace | sort") == "du -sh /workspace/thread-a | sort"


def test_an_already_qualified_path_is_not_doubled():
    assert _confine("ls /workspace/thread-a") == "ls /workspace/thread-a"


def test_unrelated_words_are_left_alone():
    for cmd in ("echo my/workspace", "echo workspace", "echo /workspaces/x"):
        assert _confine(cmd) == cmd


def test_threads_get_different_roots():
    assert _confine("ls /workspace", "one") != _confine("ls /workspace", "two")
