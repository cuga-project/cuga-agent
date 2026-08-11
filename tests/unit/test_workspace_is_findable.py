"""Can a user find what a conversation produced, without being told the GUID?

A thread id is a UUID that never appears in the chat. When an agent reports its
work properly this does not matter — it hands over an absolute path. When it
does not, the user is left globbing `cuga_workspace/*` and guessing which
directory is theirs, which is exactly what happened: a finished deck sat in a
workspace for 33 minutes while the agent asked, over and over, whether it
should start.

Two cheap affordances, both of which have to hold:

  * the real path is logged once, the first time a conversation writes anything
  * `cuga_workspace/latest` points at that directory, so the GUID is optional
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.native.native_sandbox_executor import (
    _point_latest_at,
)


@pytest.mark.unit
class TestTheLatestPointer:
    def test_it_points_at_the_newest_conversation(self, tmp_path: Path) -> None:
        first = tmp_path / "aaaa-1111"
        first.mkdir()
        _point_latest_at(first)

        link = tmp_path / "latest"
        assert link.is_symlink()
        assert link.resolve() == first.resolve()

    def test_a_second_conversation_takes_it_over(self, tmp_path: Path) -> None:
        """Stale is worse than absent: it would send someone to an old deck."""
        first, second = tmp_path / "aaaa-1111", tmp_path / "bbbb-2222"
        first.mkdir()
        second.mkdir()
        _point_latest_at(first)
        _point_latest_at(second)

        assert (tmp_path / "latest").resolve() == second.resolve()

    def test_it_is_relative_so_the_workspace_can_move(self, tmp_path: Path) -> None:
        """An absolute target breaks the moment the tree is copied elsewhere."""
        target = tmp_path / "aaaa-1111"
        target.mkdir()
        _point_latest_at(target)

        assert not Path((tmp_path / "latest").readlink()).is_absolute()

    def test_it_leaves_a_real_directory_alone(self, tmp_path: Path) -> None:
        """If someone already has a folder called `latest`, it is theirs.

        Replacing a real directory with a symlink would delete whatever they
        had put in it, to save them typing a UUID. Not a trade worth making.
        """
        real = tmp_path / "latest"
        real.mkdir()
        (real / "keep.txt").write_text("mine")

        _point_latest_at(tmp_path / "aaaa-1111")

        assert real.is_dir() and not real.is_symlink()
        assert (real / "keep.txt").read_text() == "mine"

    def test_it_never_raises(self, tmp_path: Path) -> None:
        """This is a convenience. It must not be able to fail a command."""
        _point_latest_at(tmp_path / "does" / "not" / "exist")  # parent missing


@pytest.mark.unit
class TestTheWorkspaceIsAnnouncedOnce:
    def test_the_first_write_logs_the_path(self) -> None:
        """Announced on creation, not per command — a log line every shell call
        is noise, and noise is what people learn to skip past."""
        source = Path(
            __file__
        ).resolve().parents[2] / "src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/native/native_sandbox_executor.py"
        text = source.read_text(encoding="utf-8")

        assert "workspace for this conversation" in text
        assert "first_use = not workspace_root.exists()" in text, (
            "the announcement is not gated on first use, so it repeats per command"
        )
        assert text.index("first_use = not workspace_root.exists()") < text.index(
            "workspace_root.mkdir"
        ), "existence is checked after mkdir, so first_use is always False"
