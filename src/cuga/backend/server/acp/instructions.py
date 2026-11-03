"""Instruction files a host asks CUGA to follow in every ACP session."""

from __future__ import annotations

import os
from pathlib import Path

INSTRUCTION_FILES_ENV = "CUGA_INSTRUCTION_FILES"
# One file is a host's instructions, not a corpus: cap what one file may add.
_MAX_FILE_BYTES = 64 * 1024


def read_instruction_files(value: str | None = None) -> str:
    """Join the readable files named in ``CUGA_INSTRUCTION_FILES``.

    The variable lists paths separated by ``os.pathsep``. A missing, unreadable
    or empty file is skipped, so a host can name files a user may never write.
    The files are read when a session starts, so an edit applies from the next
    session.
    """
    raw = os.environ.get(INSTRUCTION_FILES_ENV, "") if value is None else value
    parts: list[str] = []
    seen: set[Path] = set()
    for entry in raw.split(os.pathsep):
        if not entry.strip():
            continue
        path = Path(entry.strip()).expanduser()
        try:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            with path.open("rb") as handle:
                data = handle.read(_MAX_FILE_BYTES)
        except OSError:
            continue
        text = data.decode("utf-8", errors="replace").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)
