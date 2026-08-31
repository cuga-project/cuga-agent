#!/usr/bin/env python3
"""Fail when a value taken from an exception is placed in a response sent to the caller.

Background
----------
#681 established one rule for the server: nothing taken from an exception is ever put in
the response a caller receives. No ``str(exc)``, no ``repr(exc)``, no ``exc.args``, no
stack trace. The message a caller sees is always fixed text; the details go to the log
behind a reference code. See ``src/cuga/backend/server/error_responses.py``.

That rule lived only in docstrings, so a future edit could reintroduce a leak by writing
``detail=str(e)`` again. This check enforces it. It reads the server source and fails when
an exception-derived value flows into one of the places a response is built:

* ``HTTPException(detail=...)``
* ``JSONResponse(...)`` (the body, including every value in a body dict)
* the A2A ``_rpc_error(id, code, message, data)`` helper
* a streaming ``StreamEvent(data=...)`` frame

A value counts as exception-derived when it is ``str(exc)`` / ``repr(exc)``, an f-string
that interpolates the exception, ``exc.args`` / ``exc.errors()`` / ``exc.json()``, or
``traceback.format_exc()``. Identifiers that hold that value — ``err = e``,
``msg = str(e)``, ``msg = f"...{e}..."`` — are followed the same way, so storing the
text in a local first does not hide it. The class name alone — ``type(exc).__name__`` —
says nothing about the cause and is allowed.

Introducing the check
---------------------
The server already has many pre-existing offenders (#722 tracks fixing them). To turn the
check on without a large remediation, it compares against a baseline file of the offenders
present when it was introduced: only a *new* offender fails the check. Regenerate the
baseline with ``--update`` after an intentional change.

Intentional cases (for example a 400 that echoes a validation message the caller sent) can
be marked with ``# noqa: exc-in-response`` on any line of the call.

Usage
-----
    python scripts/checks/no_exc_in_responses.py [PATHS ...]
    python scripts/checks/no_exc_in_responses.py --update   # regenerate the baseline
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CODE = "exc-in-response"
NOQA = f"# noqa: {CODE}"

# Attributes of an exception whose value carries the original text/detail.
EXC_ATTRS = frozenset({"args", "errors", "json", "message", "detail", "msg"})

# traceback helpers that render a stack trace or exception text.
TRACEBACK_FUNCS = frozenset({"format_exc", "print_exc", "format_exception", "format_tb"})

# Node types that stop the walk-up when resolving which exception aliases are in scope
# for a given expression — an `except ... as x` binding does not cross a function boundary.
_SCOPE_BOUNDARIES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.Module)

# Default source tree to scan.
DEFAULT_TARGET = "src/cuga/backend/server"
DEFAULT_BASELINE = "scripts/checks/exc_in_responses_baseline.json"


@dataclass(frozen=True)
class Violation:
    line: int
    col: int
    code: str
    message: str
    snippet: str


# --- detecting an exception-derived value ---------------------------------------


def _parent_map(root: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _assigned_simple_names(stmt: ast.AST) -> tuple[ast.expr, list[str]] | None:
    """If ``stmt`` assigns to plain names, return ``(value, target names)``.

    Covers ``x = ...``, ``x: str = ...``, and ``x += ...``. Tuple unpacking is ignored.
    """
    if isinstance(stmt, ast.Assign):
        names = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
        if names:
            return stmt.value, names
    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
        return stmt.value, [stmt.target.id]
    elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
        return stmt.value, [stmt.target.id]
    return None


def _except_handler_aliases(handler: ast.ExceptHandler) -> frozenset[str]:
    """Names in this handler that hold the caught exception or text taken from it.

    Starts with the ``except ... as x`` binding (if any) and closes over assignments
    whose value is that exception or text derived from it: ``err = e``, ``msg = str(e)``,
    ``msg = f"...{e}..."``, ``msg = traceback.format_exc()``. Chains (``err = e`` then
    ``msg = str(err)``) are resolved to a fixed point. A handler with no binding is still
    scanned, so ``except Exception: msg = traceback.format_exc()`` is not missed.
    """
    names: set[str] = {handler.name} if handler.name else set()
    changed = True
    while changed:
        changed = False
        for stmt in ast.walk(handler):
            bound = _assigned_simple_names(stmt)
            if bound is None:
                continue
            value, targets = bound
            if not _contains_exception_value(value, frozenset(names)):
                continue
            for name in targets:
                if name not in names:
                    names.add(name)
                    changed = True
    return frozenset(names)


def _except_alias_scopes(tree: ast.AST) -> dict[ast.AST, frozenset[str]]:
    """Map each ``ExceptHandler`` node to the exception-alias names bound within it."""
    scopes: dict[ast.AST, frozenset[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        aliases = _except_handler_aliases(node)
        if aliases:
            scopes[node] = aliases
    return scopes


def _enclosing_exception_names(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    alias_scopes: dict[ast.AST, frozenset[str]],
) -> frozenset[str]:
    """The exception aliases bound by an ``except ... as x`` block enclosing ``node``,
    without crossing into an outer function — a caught exception does not leak that far."""
    names: set[str] = set()
    current = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, ast.ExceptHandler):
            names |= alias_scopes.get(parent, frozenset())
        if isinstance(parent, _SCOPE_BOUNDARIES):
            break
        current = parent
    return frozenset(names)


def _name_use_is_banned(name: ast.Name, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(name)
    # type(exc)... extracts only the class identity — allowed.
    if (
        isinstance(parent, ast.Call)
        and isinstance(parent.func, ast.Name)
        and parent.func.id == "type"
        and name in parent.args
    ):
        return False
    # str(exc) / repr(exc)
    if (
        isinstance(parent, ast.Call)
        and isinstance(parent.func, ast.Name)
        and parent.func.id in ("str", "repr")
        and name in parent.args
    ):
        return True
    # exc.args / exc.errors() / exc.json() / exc.message ...
    if isinstance(parent, ast.Attribute) and parent.value is name and parent.attr in EXC_ATTRS:
        return True
    # f"...{exc}..." — the exception interpolated directly.
    if isinstance(parent, ast.FormattedValue) and parent.value is name:
        return True
    # detail=exc — the exception passed straight through as the content.
    if isinstance(parent, ast.keyword) and parent.value is name:
        return True
    # JSONResponse({"traceback": msg}) / JSONResponse([msg]) — a local holding
    # exception text filed under any key or in a list is still sent to the caller.
    if isinstance(parent, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
        return True
    return False


def _contains_exception_value(expr: ast.AST, exc_names: frozenset[str]) -> bool:
    """True when ``expr`` reads text or a trace from one of the caught exceptions bound
    by ``exc_names`` (the aliases actually in scope at this call site — see
    ``_enclosing_exception_names``)."""
    # traceback.format_exc() and friends (traceback is a module, not a bound alias).
    for node in ast.walk(expr):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "traceback"
            and node.attr in TRACEBACK_FUNCS
        ):
            return True
    if not exc_names:
        return False
    # The expression itself being a bare exception name (detail=e).
    if isinstance(expr, ast.Name) and expr.id in exc_names:
        return True
    parents = _parent_map(expr)
    for node in ast.walk(expr):
        if isinstance(node, ast.Name) and node.id in exc_names:
            if _name_use_is_banned(node, parents):
                return True
    return False


# --- locating the response-building calls ---------------------------------------


def _func_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _content_exprs(call: ast.Call) -> list[ast.expr]:
    """The argument expressions of ``call`` that end up in the caller's response."""
    name = _func_name(call)
    exprs: list[ast.expr] = []

    if name == "HTTPException":
        detail = _keyword(call, "detail")
        if detail is not None:
            exprs.append(detail)

    elif name == "JSONResponse":
        body = _keyword(call, "content")
        if body is None and call.args:
            body = call.args[0]
        if body is not None:
            # Inspect the whole body, not just values under a fixed set of known keys —
            # `JSONResponse({"debug": str(exc)})` leaks just as much as `{"message": ...}`.
            exprs.append(body)

    elif name == "_rpc_error":
        # _rpc_error(id, code, message, data=None)
        if len(call.args) >= 3:
            exprs.append(call.args[2])
        if len(call.args) >= 4:
            exprs.append(call.args[3])
        for key in ("message", "data"):
            value = _keyword(call, key)
            if value is not None:
                exprs.append(value)

    elif name == "StreamEvent":
        data = _keyword(call, "data")
        if data is not None:
            exprs.append(data)

    return exprs


# --- public API ------------------------------------------------------------------


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _call_source_segment(source: str, node: ast.Call, lines: list[str]) -> str:
    """The full (possibly multi-line) source text of ``node``, normalized to one line.

    Falls back to just the start line if the source segment can't be recovered (should not
    happen for a node parsed from ``source`` itself, but ``ast.get_source_segment`` is
    documented as best-effort).
    """
    segment = ast.get_source_segment(source, node)
    if segment is None:
        segment = lines[node.lineno - 1] if 0 <= node.lineno - 1 < len(lines) else ""
    return _normalize(segment)


def find_violations(source: str, filename: str) -> list[Violation]:
    """Return the response-building calls in ``source`` fed an exception-derived value."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    lines = source.splitlines()
    parents = _parent_map(tree)
    alias_scopes = _except_alias_scopes(tree)
    seen: set[tuple[int, int]] = set()
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        exc_names = _enclosing_exception_names(node, parents, alias_scopes)
        content = _content_exprs(node)
        if not content or not any(_contains_exception_value(expr, exc_names) for expr in content):
            continue
        line = node.lineno
        col = node.col_offset
        if (line, col) in seen:
            continue
        seen.add((line, col))
        # A noqa pragma can sit on any line the call spans, not just the first.
        end_line = getattr(node, "end_lineno", line) or line
        span = lines[line - 1 : end_line]
        if any(NOQA in raw for raw in span):
            continue
        violations.append(
            Violation(
                line=line,
                col=col,
                code=CODE,
                message=(
                    f"{_func_name(node)}(...) is given a value taken from an exception; "
                    "send fixed text and log the detail behind a reference code "
                    "(see server/error_responses.py)"
                ),
                snippet=_call_source_segment(source, node, lines),
            )
        )
    return violations


def build_baseline(by_file: dict[str, list[Violation]]) -> dict[str, dict[str, int]]:
    """A baseline maps each file to how many times each normalized call is allowed to
    remain. Counting occurrences (rather than deduplicating into a set) means a baselined
    call can't be copy-pasted to a new site, or a new violation smuggled in behind an
    existing one that happens to normalize to the same text, without tripping the check.
    """
    baseline: dict[str, dict[str, int]] = {}
    for path, violations in by_file.items():
        if not violations:
            continue
        counts: dict[str, int] = {}
        for v in violations:
            counts[v.snippet] = counts.get(v.snippet, 0) + 1
        baseline[path] = dict(sorted(counts.items()))
    return baseline


def new_violations(
    violations: list[Violation], relpath: str, baseline: dict[str, dict[str, int]]
) -> list[Violation]:
    """Violations in ``violations`` not covered by ``baseline``, consuming each baselined
    occurrence at most once so a repeated or duplicated violation still counts as new."""
    remaining = dict(baseline.get(relpath, {}))
    new: list[Violation] = []
    for v in violations:
        available = remaining.get(v.snippet, 0)
        if available > 0:
            remaining[v.snippet] = available - 1
        else:
            new.append(v)
    return new


# --- CLI -------------------------------------------------------------------------


def _iter_py_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.py")))
        elif p.suffix == ".py":
            files.append(p)
    return files


def _relpath(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=[DEFAULT_TARGET])
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument("--update", action="store_true", help="regenerate the baseline from current sources")
    args = parser.parse_args(argv)

    paths = args.paths or [DEFAULT_TARGET]
    by_file: dict[str, list[Violation]] = {}
    for file in _iter_py_files(paths):
        try:
            source = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        violations = find_violations(source, str(file))
        if violations:
            by_file[_relpath(file)] = violations

    baseline_path = Path(args.baseline)

    if args.update:
        baseline = build_baseline(by_file)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        total = sum(sum(counts.values()) for counts in baseline.values())
        print(f"wrote baseline with {total} allowed entries across {len(baseline)} files -> {baseline_path}")
        return 0

    baseline: dict[str, dict[str, int]] = {}
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    failures: list[tuple[str, Violation]] = []
    for relpath, violations in by_file.items():
        for v in new_violations(violations, relpath, baseline):
            failures.append((relpath, v))

    if failures:
        print("New exception detail reaching a caller response (see #681):\n", file=sys.stderr)
        for relpath, v in sorted(failures, key=lambda rv: (rv[0], rv[1].line, rv[1].col)):
            print(f"  {relpath}:{v.line}: {v.message}", file=sys.stderr)
            print(f"      {v.snippet}", file=sys.stderr)
        print(
            f"\n{len(failures)} new finding(s). Send fixed text and log the detail behind a "
            "reference code, or mark an intentional case with `# noqa: exc-in-response`.\n"
            "If you deliberately changed a baselined line, run "
            "`python scripts/checks/no_exc_in_responses.py --update`.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
