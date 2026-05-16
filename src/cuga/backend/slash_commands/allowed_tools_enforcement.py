"""Detect tool calls in generated code that fall outside a skill's whitelist.

The dispatcher attaches a skill's ``allowed-tools`` frontmatter list to
``RunnableConfig.configurable`` for the duration of the skill turn; the
cuga_lite tool-approval gate calls :func:`find_disallowed_calls` on the
generated code and, if any disallowed call is found, routes through the
existing HITL approval interrupt.

Uses ast.Call(func=Name(id=...)) so only bare function calls — not
.method() chains or other callables — are gated.

Python builtins are filtered via :data:`PYTHON_BUILTINS_SAFELIST` so the
whitelist only needs to enumerate domain tools, not ``print``/``len``/etc.
"""

from __future__ import annotations

import ast
import builtins as _py_builtins
from typing import Iterable, List, Optional, Set


PYTHON_BUILTINS_SAFELIST: frozenset[str] = frozenset(
    name for name in dir(_py_builtins) if not name.startswith("_")
) | frozenset(
    {
        # Common stdlib callables the sandbox treats as primitives; not tools.
        "asyncio",
        "json",
        "re",
        "math",
        "datetime",
        "os",
        "sys",
        "pathlib",
    }
)


def _bare_call_names(code: str) -> Set[str]:
    """Return the set of identifiers used as bare function calls in ``code``.

    Bare = ``Name(id=...)`` at ``Call.func``. Method calls (``Attribute``) and
    arbitrary callables (``Subscript``, lambdas, etc.) are excluded.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # The cuga_lite gate runs before the sandbox executes anything, so
        # unparseable code is the model's problem to fix on the next turn —
        # we don't want to block on it via the whitelist.
        return set()

    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


def find_disallowed_calls(code: str, allowed_tools: Optional[Iterable[str]]) -> List[str]:
    """Return tool calls in ``code`` that fall outside ``allowed_tools``.

    Semantics:
      * ``allowed_tools is None`` — no restriction; always returns ``[]``.
      * ``allowed_tools == ()`` — allow nothing; every bare call that isn't a
        Python builtin is disallowed.
      * non-empty iterable — anything not in it (and not a Python builtin) is
        disallowed.

    The returned list is sorted and de-duplicated so the approval-interrupt
    metadata is deterministic.
    """
    if allowed_tools is None:
        return []
    allowed = frozenset(allowed_tools)
    called = _bare_call_names(code)
    disallowed = called - allowed - PYTHON_BUILTINS_SAFELIST
    return sorted(disallowed)
