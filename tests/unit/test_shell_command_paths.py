from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
    normalize_shell_command_paths,
    relative_workspace_path,
    shell_workspace_path,
)


def test_relative_workspace_path_uploads() -> None:
    assert relative_workspace_path("uploads/foo.json") == "./uploads/foo.json"
    assert relative_workspace_path("./uploads/foo.json") == "./uploads/foo.json"


def test_shell_workspace_path_maps_virtual_uploads() -> None:
    assert shell_workspace_path("/workspace/uploads/foo.json") == "./uploads/foo.json"
    assert shell_workspace_path("/workspace") == "."


def test_shell_workspace_path_maps_legacy_tmp_thread_prefix() -> None:
    assert shell_workspace_path("/tmp/cuga_workspace/thread-1/uploads/foo.json") == "./uploads/foo.json"


def test_normalize_shell_command_paths_rewrites_workspace_in_head() -> None:
    cmd = "head -n 5 /workspace/uploads/instana-events.json"
    assert normalize_shell_command_paths(cmd) == "head -n 5 ./uploads/instana-events.json"


def test_normalize_shell_command_paths_rewrites_legacy_tmp_paths() -> None:
    cmd = "python /tmp/cuga_workspace/t1/uploads/parse.py"
    assert normalize_shell_command_paths(cmd) == "python ./uploads/parse.py"


def test_normalize_shell_command_paths_leaves_relative_paths() -> None:
    cmd = "head -n 5 ./uploads/foo.json"
    assert normalize_shell_command_paths(cmd) == cmd
