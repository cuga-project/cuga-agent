import pytest

from cuga.backend.server.workspace_sandbox import (
    SANDBOX_WORKSPACE_ROOT,
    public_path_to_sandbox_abs,
    sandbox_paths_to_tree,
)


def test_sandbox_workspace_root_is_tmp_for_skills_mode() -> None:
    assert SANDBOX_WORKSPACE_ROOT == "/tmp"


@pytest.mark.parametrize(
    ("public_path", "expected"),
    [
        ("tmp/foo.txt", "/tmp/foo.txt"),
        ("/tmp/foo.txt", "/tmp/foo.txt"),
        ("cuga_workspace/foo.txt", "/tmp/foo.txt"),
        ("/tmp/cuga_workspace/foo.txt", "/tmp/foo.txt"),
        ("tmp/nested/foo.txt", "/tmp/nested/foo.txt"),
        ("/tmp", "/tmp"),
        ("tmp", "/tmp"),
    ],
)
def test_public_path_to_sandbox_abs_accepts_tmp_and_legacy_paths(public_path: str, expected: str) -> None:
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


def test_sandbox_paths_to_tree_uses_tmp_public_paths_and_hides_dotfiles() -> None:
    tree = sandbox_paths_to_tree(
        [
            "/tmp",
            "/tmp/reports",
            "/tmp/.venv",
        ],
        [
            "/tmp/reports/deck.pptx",
            "/tmp/notes.txt",
            "/tmp/.venv/pyvenv.cfg",
        ],
    )

    assert tree == [
        {
            "name": "reports",
            "path": "tmp/reports",
            "type": "directory",
            "children": [{"name": "deck.pptx", "path": "tmp/reports/deck.pptx", "type": "file"}],
        },
        {"name": "notes.txt", "path": "tmp/notes.txt", "type": "file"},
    ]
