#!/usr/bin/env python3
"""Fail when production code reads or writes LangGraph state through the synchronous API.

Background
----------
#774 makes the checkpointer durable (``AsyncSqliteSaver`` / ``AsyncPostgresSaver``). The
async savers do not support the synchronous saver methods when called from the event loop
thread: ``graph.get_state(...)`` calls ``saver.get_tuple`` and raises
``InvalidStateError``, and ``graph.update_state(...)`` calls ``saver.put`` the same way.
``MemorySaver`` supports both APIs, so a sync call passes every test that uses the
default saver and then breaks once a durable saver is configured. Code that runs on the
event loop must use ``await graph.aget_state(...)`` / ``await graph.aupdate_state(...)``.

This check reads the source and fails on any synchronous call of a LangGraph state method:

* ``get_state`` / ``update_state``
* ``get_state_history`` / ``bulk_update_state`` (same saver path, same failure)

It works on the AST, so a mention in a docstring, string literal or comment is never
flagged, and ``aget_state`` / ``aupdate_state`` are different attribute names. Any
receiver matches (``self.graph``, ``agent.graph``, ``run_agent.graph``,
``self.agent_loop_obj.graph``, a bare ``graph``, or a compiled graph bound to some other
name): ``src/cuga`` defines no other ``get_state`` / ``update_state`` method, so matching
the attribute name alone has no false positives today and does not miss a graph held
under an unexpected name. An unrelated method with the same name can be marked with
``# noqa: sync-graph-state`` on any line of the call.

Files under a ``tests`` directory (top-level ``tests/`` or a colocated ``**/tests/**``)
are skipped: synchronous tests on ``MemorySaver`` may keep using the sync API.

Usage
-----
    python scripts/checks/no_sync_graph_state.py [PATHS ...]   # default: src/cuga

Exit status is 0 when nothing is found and 1 when a synchronous call is found.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CODE = "sync-graph-state"
NOQA = f"# noqa: {CODE}"

# Synchronous LangGraph state methods and the async method that replaces each one.
SYNC_STATE_METHODS: dict[str, str] = {
    "get_state": "aget_state",
    "update_state": "aupdate_state",
    "get_state_history": "aget_state_history",
    "bulk_update_state": "abulk_update_state",
}

DEFAULT_TARGETS = ("src/cuga",)

# A path with one of these directory components is test code and is not checked.
EXCLUDED_DIR_NAMES = frozenset({"tests"})


@dataclass(frozen=True)
class Violation:
    line: int
    col: int
    code: str
    message: str
    snippet: str


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _call_source_segment(source: str, node: ast.Call, lines: list[str]) -> str:
    """The full (possibly multi-line) source text of ``node``, normalized to one line."""
    segment = ast.get_source_segment(source, node)
    if segment is None:
        segment = lines[node.lineno - 1] if 0 <= node.lineno - 1 < len(lines) else ""
    return _normalize(segment)


def _receiver(func: ast.Attribute, source: str) -> str:
    """The receiver expression of ``x.get_state`` as source text (``self.graph``)."""
    segment = ast.get_source_segment(source, func.value)
    return _normalize(segment) if segment else "<expr>"


def find_violations(source: str, filename: str) -> list[Violation]:
    """Return the synchronous LangGraph state calls in ``source``."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    lines = source.splitlines()
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in SYNC_STATE_METHODS:
            continue
        line = node.lineno
        # A noqa pragma can sit on any line the call spans, not just the first.
        end_line = getattr(node, "end_lineno", line) or line
        if any(NOQA in raw for raw in lines[line - 1 : end_line]):
            continue
        receiver = _receiver(func, source)
        violations.append(
            Violation(
                line=line,
                col=node.col_offset,
                code=CODE,
                message=(
                    f"synchronous {receiver}.{func.attr}(...) fails on an async checkpointer; "
                    f"use `await {receiver}.{SYNC_STATE_METHODS[func.attr]}(...)`"
                ),
                snippet=_call_source_segment(source, node, lines),
            )
        )
    return sorted(violations, key=lambda v: (v.line, v.col))


# --- CLI -------------------------------------------------------------------------


def is_excluded(path: Path) -> bool:
    """True when ``path`` lies under a ``tests`` directory.

    Judged on the path relative to the working directory, so a checkout that itself sits
    under some ``.../tests/...`` directory is not skipped wholesale.
    """
    return any(part in EXCLUDED_DIR_NAMES for part in Path(_relpath(path)).parts[:-1])


def _iter_py_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            candidates = sorted(p.rglob("*.py"))
        elif p.suffix == ".py":
            candidates = [p]
        else:
            candidates = []
        files.extend(f for f in candidates if not is_excluded(f))
    return files


def _relpath(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_TARGETS))
    args = parser.parse_args(argv)

    paths = args.paths or list(DEFAULT_TARGETS)
    failures: list[tuple[str, Violation]] = []
    for file in _iter_py_files(paths):
        try:
            source = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relpath = _relpath(file)
        failures.extend((relpath, v) for v in find_violations(source, str(file)))

    if failures:
        print("Synchronous LangGraph state calls (see #774):\n", file=sys.stderr)
        for relpath, v in sorted(failures, key=lambda rv: (rv[0], rv[1].line, rv[1].col)):
            print(f"  {relpath}:{v.line}: {v.message}", file=sys.stderr)
            print(f"      {v.snippet}", file=sys.stderr)
        print(
            f"\n{len(failures)} finding(s). Use `await graph.aget_state(...)` / "
            "`await graph.aupdate_state(...)`, or mark an unrelated method of the same "
            f"name with `{NOQA}`.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
