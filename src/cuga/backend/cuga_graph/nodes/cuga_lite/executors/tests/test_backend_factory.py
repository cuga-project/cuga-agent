"""The single place that decides which sandbox holds the workspace.

Answering this per call site is how the halves drift: the mode that runs
generated Python in a sandbox has to put the files there too, or the agent
writes a file it cannot read back.
"""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import connection
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd.filesystem_backend import (
    ExecdFilesystemBackend,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem import factory
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.backends import (
    HostWorkspaceBackend,
    RemoteSandboxBackend,
)
from cuga.config import settings

pytestmark = pytest.mark.unit


@pytest.fixture
def mode(monkeypatch):
    def _set(sandbox_mode, *, opensandbox_sandbox=True):
        monkeypatch.setattr(settings.advanced_features, "sandbox_mode", sandbox_mode)
        monkeypatch.setattr(settings.advanced_features, "opensandbox_sandbox", opensandbox_sandbox)
        # The runtime toggle overrides sandbox_mode; pin it so other tests cannot leak execd in.
        monkeypatch.setattr(connection, "is_enabled", lambda: sandbox_mode == "execd")

    return _set


def test_execd_selects_the_execd_backend(mode):
    mode("execd")
    assert isinstance(factory.sandbox_workspace_backend("t1"), ExecdFilesystemBackend)


def test_execd_does_not_need_the_opensandbox_flag(mode):
    """``opensandbox_sandbox`` describes a different daemon."""
    mode("execd", opensandbox_sandbox=False)
    assert factory.workspace_is_sandbox_backed() is True


def test_opensandbox_selects_the_server_backend(mode):
    mode("opensandbox")
    assert isinstance(factory.sandbox_workspace_backend("t1"), RemoteSandboxBackend)


@pytest.mark.parametrize("sandbox_mode", ["native", "local"])
def test_host_modes_are_not_sandbox_backed(mode, sandbox_mode):
    mode(sandbox_mode)
    assert factory.workspace_is_sandbox_backed() is False
    assert isinstance(factory.workspace_backend("t1"), HostWorkspaceBackend)


def test_opensandbox_mode_without_its_flag_falls_back_to_the_host(mode):
    mode("opensandbox", opensandbox_sandbox=False)
    assert factory.workspace_is_sandbox_backed() is False


@pytest.mark.parametrize("method", ["walk", "read_bytes", "remove_tree", "preview_text"])
def test_both_sandbox_backends_answer_the_workspace_api(method):
    """The workspace API calls these on whichever backend it is handed."""
    assert callable(getattr(RemoteSandboxBackend, method))
    assert callable(getattr(ExecdFilesystemBackend, method))
