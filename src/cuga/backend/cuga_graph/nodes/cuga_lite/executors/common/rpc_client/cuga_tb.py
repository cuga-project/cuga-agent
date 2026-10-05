"""Tracebacks that show the model's own code and nothing else — stdlib only.

Generated code is ``exec``'d from a string, so a raw traceback names
``File "<string>", line 9`` with no source text, wrapped in frames of the
executor itself (task runners, budget wrappers). The model learns nothing from
those. Registering the source under :data:`CODE_FILENAME` lets ``linecache`` show
each failing statement, and :func:`format_code_error` keeps only those frames.

Shared by the in-process executor and the subprocess worker (``cuga_worker.py``).
"""

from __future__ import annotations

import itertools
import linecache
import threading
import traceback
from typing import List, Optional

CODE_FILENAME = "<execute_python>"

#: Frames shown at most (innermost kept); deep recursion is cut from the top.
MAX_FRAMES = 6

# In one process blocks may run concurrently (spawned agents), so each gets its own
# name — from a small ring, so ``linecache`` does not grow without bound.
_RING = 64
_counter = itertools.count()
_counter_lock = threading.Lock()


def _is_code_file(filename: str) -> bool:
    return filename.startswith(CODE_FILENAME[:-1])


def register_source(code: str, filename: Optional[str] = None) -> str:
    """Make ``code`` readable by ``linecache`` (and so by tracebacks); returns the name to compile it under."""
    if filename is None:
        with _counter_lock:
            filename = f"{CODE_FILENAME[:-1]}#{next(_counter) % _RING}>"
    linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
    return filename


def format_code_error(error: BaseException) -> str:
    """The failing statements of the model's code, innermost last, then the error line.

    Returns just the error line when no frame comes from the code (the error was
    raised before it ran, e.g. while building the namespace).
    """
    frames = [f for f in traceback.extract_tb(error.__traceback__) if _is_code_file(f.filename)]
    lines: List[str] = []
    if frames:
        lines.append("Traceback (your code, innermost last):")
        if len(frames) > MAX_FRAMES:
            lines.append(f"  ... {len(frames) - MAX_FRAMES} outer frame(s) omitted")
            frames = frames[-MAX_FRAMES:]
        for frame in frames:
            source = (frame.line or "").strip() or "<source unavailable>"
            where = "" if frame.name in ("_async_main", "<module>") else f"  (in {frame.name})"
            lines.append(f"  >>> {source}{where}")
    lines.extend(part.rstrip("\n") for part in traceback.format_exception_only(type(error), error))
    return "\n".join(lines)
