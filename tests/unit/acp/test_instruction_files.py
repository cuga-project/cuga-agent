"""Host instruction files named by CUGA_INSTRUCTION_FILES."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.unit
def test_reads_existing_files_in_order_and_skips_the_rest(tmp_path) -> None:
    from cuga.backend.server.acp.instructions import read_instruction_files

    platform = tmp_path / "platform.md"
    platform.write_text("Platform rules.\n", encoding="utf-8")
    owner = tmp_path / "owner.md"
    owner.write_text("Owner rules.", encoding="utf-8")
    empty = tmp_path / "empty.md"
    empty.write_text("  \n", encoding="utf-8")
    missing = tmp_path / "missing.md"

    value = os.pathsep.join(str(p) for p in (platform, missing, empty, owner, platform))

    assert read_instruction_files(value) == "Platform rules.\n\nOwner rules."


@pytest.mark.unit
def test_nothing_named_is_no_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuga.backend.server.acp.instructions import read_instruction_files

    monkeypatch.delenv("CUGA_INSTRUCTION_FILES", raising=False)
    assert read_instruction_files() == ""
    assert read_instruction_files("") == ""


@pytest.mark.unit
def test_a_large_file_is_capped(tmp_path) -> None:
    from cuga.backend.server.acp.instructions import read_instruction_files

    big = tmp_path / "big.md"
    big.write_text("x" * 100_000, encoding="utf-8")

    assert len(read_instruction_files(str(big))) == 64 * 1024
