import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.native import native_sandbox_executor
from cuga.backend.server.workspace_sandbox import (
    NATIVE_DISPLAY_ROOT,
    NATIVE_WORKSPACE_ROOT,
    SANDBOX_WORKSPACE_ROOT,
    fetch_native_workspace_tree,
    native_workspace_text_preview,
    public_path_to_sandbox_abs,
    read_native_workspace_bytes,
    sandbox_paths_to_tree,
)


def test_workspace_tree_is_native_backed_when_host_shell_with_opensandbox_flag(monkeypatch) -> None:
    from cuga.backend.server import workspace_sandbox as ws

    class Adv:
        opensandbox_sandbox = True
        enable_shell_tool = True
        sandbox_mode = "native"
        enable_filesystem_tools = True

    class Settings:
        advanced_features = Adv()
        execution = type(
            "Exec",
            (),
            {
                "shell_backend": None,
                "filesystem_backend": None,
                "python_backend": None,
                "workspace_root": None,
            },
        )()

    monkeypatch.setattr(ws, "settings", Settings())
    assert ws.workspace_tree_is_native_backed() is True


def test_workspace_tree_is_not_native_when_opensandbox_shell_only(monkeypatch) -> None:
    from cuga.backend.server import workspace_sandbox as ws

    class Adv:
        opensandbox_sandbox = True
        enable_shell_tool = True
        sandbox_mode = "opensandbox"
        enable_filesystem_tools = True

    class Settings:
        advanced_features = Adv()
        execution = type(
            "Exec",
            (),
            {
                "shell_backend": None,
                "filesystem_backend": None,
                "python_backend": None,
                "workspace_root": None,
            },
        )()

    monkeypatch.setattr(ws, "settings", Settings())
    assert ws.workspace_tree_is_native_backed() is False


def test_sandbox_workspace_root() -> None:
    assert SANDBOX_WORKSPACE_ROOT == "/workspace"


def test_native_workspace_root_is_virtual_workspace_for_native_mode() -> None:
    assert NATIVE_WORKSPACE_ROOT == "/workspace"
    assert NATIVE_DISPLAY_ROOT == "workspace"


@pytest.mark.parametrize(
    ("public_path", "expected"),
    [
        ("workspace/foo.txt", "/workspace/foo.txt"),
        ("/workspace/foo.txt", "/workspace/foo.txt"),
        ("cuga_workspace/foo.txt", "/workspace/foo.txt"),
        ("/tmp/cuga_workspace/foo.txt", "/workspace/foo.txt"),
        ("tmp/foo.txt", "/workspace/foo.txt"),
        ("tmp/nested/foo.txt", "/workspace/nested/foo.txt"),
        ("/workspace", "/workspace"),
        ("workspace", "/workspace"),
    ],
)
def test_public_path_to_sandbox_abs_accepts_workspace_and_legacy_paths(
    public_path: str, expected: str
) -> None:
    assert public_path_to_sandbox_abs(public_path) == expected


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        "foo.txt",
        "tmp/../foo.txt",
        "tmp/.venv/bin/python",
        "/tmp/.venv/bin/python",
        "cuga_workspace/.secret",
        "/var/tmp/foo.txt",
    ],
)
def test_public_path_to_sandbox_abs_rejects_paths_outside_public_workspace(bad_path: str) -> None:
    with pytest.raises(ValueError):
        public_path_to_sandbox_abs(bad_path)


def test_sandbox_paths_to_tree_uses_workspace_public_paths_and_hides_dotfiles() -> None:
    tree = sandbox_paths_to_tree(
        [
            "/workspace",
            "/workspace/reports",
            "/workspace/.venv",
        ],
        [
            "/workspace/reports/deck.pptx",
            "/workspace/notes.txt",
            "/workspace/.venv/pyvenv.cfg",
        ],
    )

    assert tree == [
        {
            "name": "reports",
            "path": "workspace/reports",
            "type": "directory",
            "children": [{"name": "deck.pptx", "path": "workspace/reports/deck.pptx", "type": "file"}],
        },
        {"name": "notes.txt", "path": "workspace/notes.txt", "type": "file"},
    ]


def test_sandbox_paths_to_tree_can_render_native_workspace_public_paths() -> None:
    tree = sandbox_paths_to_tree(
        [
            "/workspace",
            "/workspace/reports",
            "/workspace/.cache",
        ],
        [
            "/workspace/reports/result.json",
            "/workspace/app.js",
            "/workspace/.cache/index",
        ],
        sandbox_root=NATIVE_WORKSPACE_ROOT,
        display_root=NATIVE_DISPLAY_ROOT,
    )

    assert tree == [
        {
            "name": "reports",
            "path": "workspace/reports",
            "type": "directory",
            "children": [{"name": "result.json", "path": "workspace/reports/result.json", "type": "file"}],
        },
        {"name": "app.js", "path": "workspace/app.js", "type": "file"},
    ]


def test_fetch_native_workspace_tree_is_per_thread_and_public_workspace(monkeypatch, tmp_path) -> None:
    def fake_workspace_root(thread_id: str | None):
        return tmp_path / (thread_id or "_default") / "workspace"

    monkeypatch.setattr(native_sandbox_executor, "native_thread_workspace_root", fake_workspace_root)

    thread_a_root = fake_workspace_root("thread-a")
    thread_b_root = fake_workspace_root("thread-b")
    (thread_a_root / "reports").mkdir(parents=True)
    (thread_b_root / "reports").mkdir(parents=True)
    (thread_a_root / "reports" / "a.txt").write_text("a", encoding="utf-8")
    (thread_b_root / "reports" / "b.txt").write_text("b", encoding="utf-8")
    (thread_a_root / ".uv-cache").mkdir()
    (thread_a_root / ".uv-cache" / "hidden").write_text("hidden", encoding="utf-8")

    assert fetch_native_workspace_tree("thread-a") == [
        {
            "name": "reports",
            "path": "workspace/reports",
            "type": "directory",
            "children": [{"name": "a.txt", "path": "workspace/reports/a.txt", "type": "file"}],
        }
    ]
    assert fetch_native_workspace_tree("thread-b") == [
        {
            "name": "reports",
            "path": "workspace/reports",
            "type": "directory",
            "children": [{"name": "b.txt", "path": "workspace/reports/b.txt", "type": "file"}],
        }
    ]


def test_native_workspace_file_access_maps_workspace_to_thread_root(monkeypatch, tmp_path) -> None:
    def fake_workspace_root(thread_id: str | None):
        return tmp_path / (thread_id or "_default") / "workspace"

    monkeypatch.setattr(native_sandbox_executor, "native_thread_workspace_root", fake_workspace_root)

    root = fake_workspace_root("thread-a")
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "note.txt").write_text("hello", encoding="utf-8")

    assert native_workspace_text_preview("thread-a", "/workspace/nested/note.txt") == "hello"
    assert native_workspace_text_preview("thread-a", "workspace/nested/note.txt") == "hello"
    assert native_workspace_text_preview("thread-a", "/tmp/nested/note.txt") == "hello"
    assert read_native_workspace_bytes("thread-a", "workspace/nested/note.txt") == (b"hello", "note.txt")

    with pytest.raises(FileNotFoundError):
        native_workspace_text_preview("thread-b", "/workspace/nested/note.txt")


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        "outside.txt",
        "/var/tmp/outside.txt",
        "workspace/../outside.txt",
        "workspace/.secret",
        "/workspace/.cache/file",
    ],
)
def test_native_workspace_file_access_rejects_paths_outside_public_workspace(
    monkeypatch, tmp_path, bad_path: str
) -> None:
    def fake_workspace_root(thread_id: str | None):
        return tmp_path / (thread_id or "_default") / "workspace"

    monkeypatch.setattr(native_sandbox_executor, "native_thread_workspace_root", fake_workspace_root)

    with pytest.raises(ValueError):
        native_workspace_text_preview("thread-a", bad_path)
