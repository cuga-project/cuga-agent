"""Regression tests for the ghost-files bug fixed in commit a75670f0.

Prior to the fix, ``copy_source_files`` only APPENDED into the target dir.
After a failed migration (e.g. reindex raised after copy succeeded), the
target dir kept its half-baked contents. On the user's next retry — even
after they had DELETED a document from the source — that document persisted
in the target dir as a ghost, got re-embedded, and reappeared in retrieval.

The fix mirrors the source's file set: files in target NOT present in source
are unlinked before the new copy. Source == target is refused (would delete
source's own files).

The audit explicitly flagged the absence of a unit test for this behavior
as a Medium finding — without it, a refactor of ``copy_source_files`` could
re-introduce the bug silently.
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile

from cuga.backend.knowledge.engine import KnowledgeEngine


def _make_engine_skel(files_root: pathlib.Path) -> KnowledgeEngine:
    """Build a bare engine with only the attribute ``copy_source_files`` reads."""
    eng = KnowledgeEngine.__new__(KnowledgeEngine)
    eng._files_dir = files_root
    return eng


def test_mirror_clears_stale_files_in_target():
    """Source has doc1; target has stale doc2 (from a prior failed migration).
    Mirror copy must end with target == source's set (just doc1)."""
    with tempfile.TemporaryDirectory() as tmp:
        files = pathlib.Path(tmp) / "files"
        files.mkdir()
        src, dst = files / "kb_agent_x_a", files / "kb_agent_x_b"
        src.mkdir()
        dst.mkdir()
        (src / "doc1.pdf").write_text("one")
        (dst / "doc2.pdf").write_text("ghost-from-prior-failed-migration")

        eng = _make_engine_skel(files)
        n = asyncio.run(eng.copy_source_files("kb_agent_x_a", "kb_agent_x_b"))

        assert n == 1
        assert sorted(f.name for f in dst.iterdir()) == ["doc1.pdf"], (
            "ghost not removed — copy_source_files regressed to append-only semantics"
        )


def test_source_equal_target_returns_zero_without_touching_files():
    """source == target must refuse — clearing would delete source's own files."""
    with tempfile.TemporaryDirectory() as tmp:
        files = pathlib.Path(tmp) / "files"
        files.mkdir()
        d = files / "kb_agent_x_a"
        d.mkdir()
        (d / "doc1.pdf").write_text("one")

        eng = _make_engine_skel(files)
        n = asyncio.run(eng.copy_source_files("kb_agent_x_a", "kb_agent_x_a"))

        assert n == 0
        # The whole point: source's files survive unscathed.
        assert (d / "doc1.pdf").exists()


def test_missing_source_returns_zero_unchanged_contract():
    """Source dir doesn't exist (legacy state, manual delete): early-return 0,
    no error, no side effects. Migration helper handles the missing case
    upstream via active_snapshot_missing."""
    with tempfile.TemporaryDirectory() as tmp:
        files = pathlib.Path(tmp) / "files"
        files.mkdir()
        # Note: NO source dir created.

        eng = _make_engine_skel(files)
        n = asyncio.run(eng.copy_source_files("kb_agent_x_missing", "kb_agent_x_b"))

        assert n == 0


def test_same_named_file_is_overwritten():
    """Source and target both have ``doc1.pdf`` with different contents.
    Mirror overwrites with source's version (shutil.copy2 default)."""
    with tempfile.TemporaryDirectory() as tmp:
        files = pathlib.Path(tmp) / "files"
        files.mkdir()
        src, dst = files / "kb_agent_x_a", files / "kb_agent_x_b"
        src.mkdir()
        dst.mkdir()
        (src / "doc1.pdf").write_text("source-version")
        (dst / "doc1.pdf").write_text("target-stale-version")

        eng = _make_engine_skel(files)
        n = asyncio.run(eng.copy_source_files("kb_agent_x_a", "kb_agent_x_b"))

        assert n == 1
        assert (dst / "doc1.pdf").read_text() == "source-version"
