"""Cross-process exclusive lock on a file (flock on Unix, msvcrt on Windows)."""

from __future__ import annotations

import sys
from typing import IO


def acquire_exclusive_nonblocking(lock_file: IO) -> None:
    lock_file.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)


def release_exclusive(lock_file: IO) -> None:
    try:
        lock_file.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(lock_file, fcntl.LOCK_UN)
    except OSError:
        pass
