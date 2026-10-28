"""The filesystem tools that run in the execd kernel answer as the host tools do (review items 34, 35)."""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd.execd_executor import _redact_url
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd.kernel_templates import filesystem_tools_code
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.workspace_fs import _apply_slice_and_grep

pytestmark = pytest.mark.unit


@pytest.fixture
def kernel(tmp_path):
    namespace: dict = {}
    exec(filesystem_tools_code(str(tmp_path)), namespace)
    return namespace


async def test_grep_numbers_matching_lines_like_the_host_tool(kernel):
    text = "alpha\nbeta\ngamma\nbeta2\n"
    await kernel["write_file"]("/workspace/a.txt", text)

    out = await kernel["read_file"]("/workspace/a.txt", grep_pattern="beta")

    assert out == "2|beta\n4|beta2"
    assert out == _apply_slice_and_grep(text, None, None, "beta")


@pytest.mark.parametrize(
    "start, end, pattern",
    [(2, 3, None), (None, None, "^g"), (3, None, "a"), (9, None, None), (None, None, "[")],
)
async def test_read_file_slices_exactly_as_the_host_tool(kernel, start, end, pattern):
    text = "alpha\nbeta\ngamma\nbeta2\n"
    await kernel["write_file"]("/workspace/a.txt", text)

    assert await kernel["read_file"]("/workspace/a.txt", start, end, pattern) == _apply_slice_and_grep(
        text, start, end, pattern
    )


async def test_edit_file_matches_each_edit_against_the_text_so_far(kernel, tmp_path):
    await kernel["write_file"]("/workspace/a.txt", "one two")

    # The second edit's text exists only after the first one ran.
    out = await kernel["edit_file"](
        "/workspace/a.txt",
        [{"oldText": "one", "newText": "three"}, {"oldText": "three two", "newText": "done"}],
    )

    assert "error" not in out
    assert (tmp_path / "a.txt").read_text() == "done"


async def test_edit_file_refuses_text_an_earlier_edit_made_ambiguous(kernel, tmp_path):
    await kernel["write_file"]("/workspace/a.txt", "a b")

    out = await kernel["edit_file"](
        "/workspace/a.txt", [{"oldText": "a", "newText": "b"}, {"oldText": "b", "newText": "c"}]
    )

    assert out.startswith("[edit_file error]")
    assert (tmp_path / "a.txt").read_text() == "a b"


async def test_get_file_info_has_the_host_fields(kernel):
    await kernel["write_file"]("/workspace/a.txt", "x")

    info = dict(
        line.split(": ", 1) for line in (await kernel["get_file_info"]("/workspace/a.txt")).splitlines()
    )

    assert set(info) == {
        "path",
        "size",
        "created",
        "modified",
        "accessed",
        "isDirectory",
        "isFile",
        "permissions",
    }
    assert info["path"] == "/workspace/a.txt"


@pytest.mark.parametrize(
    "call",
    [
        ("get_file_info", ("/workspace/missing",)),
        ("move_file", ("/workspace/missing", "/workspace/b")),
        ("search_files", ("/workspace/missing", "*")),
        ("read_file", ("/workspace/../../etc/passwd",)),
    ],
)
async def test_a_failing_tool_returns_an_error_instead_of_raising(kernel, tmp_path, call):
    name, args = call

    out = await kernel[name](*args)

    assert out.startswith(f"[{name} error] ")
    # The agent's own path, not where the sandbox keeps it.
    assert str(tmp_path) not in out


def test_package_index_credentials_are_kept_out_of_the_log():
    # Item 23: the URL itself must stay usable for pip/uv, but not be logged with a token.
    assert (
        _redact_url("https://user:s3cret@pypi.example/simple") == "https://***@pypi.example/simple"
    )  # pragma: allowlist secret
    assert _redact_url("https://tok@pypi.example:8443/simple") == "https://***@pypi.example:8443/simple"
    assert _redact_url("https://pypi.example/simple") == "https://pypi.example/simple"


async def test_recursive_search_excludes_nested_matches_like_the_host_tool(kernel, tmp_path):
    (tmp_path / "src" / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "node_modules" / "pkg" / "a.py").write_text("")
    (tmp_path / "src" / "b.py").write_text("")

    out = await kernel["search_files"]("/workspace", "**/*.py", excludePatterns=["node_modules"])

    assert out.splitlines() == ["/workspace/src/b.py"]
