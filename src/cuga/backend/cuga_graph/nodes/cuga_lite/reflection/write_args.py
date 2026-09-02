"""Static analysis of the write arguments in a proposed code block.

VERIFY used to see only the source text, so a wrong value assigned to a
variable first became invisible to it: ``amount=share_per_person`` carries no
literal, and both the "ungrounded literal" and the "contradiction" rule are
written in terms of literals. This module resolves each write argument back to
the expression it will actually evaluate to, folding it to a constant when the
whole expression is constant, so the verifier compares values rather than
syntax.
"""

from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional, Tuple

# Calls that never mutate state. Anything not listed here and not ending in
# ``_get`` counts as a write, so an unrecognised tool is verified rather than
# skipped.
READ_ONLY_CALLS = frozenset(
    {
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "dir",
        "divmod",
        "enumerate",
        "filter",
        "find_tools",
        "float",
        "format",
        "frozenset",
        "getattr",
        "hasattr",
        "int",
        "isinstance",
        "items",
        "iter",
        "join",
        "keys",
        "len",
        "list",
        "lower",
        "map",
        "max",
        "min",
        "next",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "setdefault",
        "sorted",
        "split",
        "str",
        "strip",
        "sum",
        "tuple",
        "type",
        "upper",
        "values",
        "zip",
    }
)

# Only these may run during constant folding.
_FOLD_NAMESPACE: Dict[str, Any] = {
    "abs": abs,
    "divmod": divmod,
    "float": float,
    "int": int,
    "len": len,
    "max": max,
    "min": min,
    "round": round,
    "str": str,
    "sum": sum,
}

_MAX_EXPANSION_DEPTH = 8
_MAX_EXPR_CHARS = 300
_MAX_ROWS = 20


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


# Container and string methods. ``d.get(...)``/``s.add(...)`` mutate local
# objects, never remote state, and reporting them buries the real write row.
CONTAINER_METHODS = frozenset(
    {
        "add",
        "append",
        "clear",
        "copy",
        "count",
        "encode",
        "endswith",
        "extend",
        "find",
        "format",
        "get",
        "index",
        "insert",
        "items",
        "join",
        "keys",
        "lower",
        "lstrip",
        "pop",
        "remove",
        "replace",
        "reverse",
        "rsplit",
        "rstrip",
        "sort",
        "split",
        "startswith",
        "strip",
        "title",
        "update",
        "upper",
        "values",
    }
)


def _is_write_call(node: ast.Call) -> bool:
    name = _call_name(node)
    if not name:
        return True
    if isinstance(node.func, ast.Attribute) and name in CONTAINER_METHODS:
        return False
    if name.endswith("_get"):
        return False
    return name not in READ_ONLY_CALLS


def has_write_call(code: Optional[str]) -> bool:
    """True when the block may mutate state. Unparseable code counts as a write."""
    text = (code or "").strip()
    if not text:
        return False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_write_call(node):
            return True
    return False


def _assignments(tree: ast.AST) -> List[Tuple[int, str, ast.expr]]:
    """Single-target name assignments, in source order."""
    out: List[Tuple[int, str, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                out.append((getattr(node, "lineno", 0), target.id, node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            out.append((getattr(node, "lineno", 0), node.target.id, node.value))
    out.sort(key=lambda item: item[0])
    return out


def _env_before(assigns: List[Tuple[int, str, ast.expr]], lineno: int) -> Dict[str, ast.expr]:
    env: Dict[str, ast.expr] = {}
    for line, name, value in assigns:
        if line < lineno:
            env[name] = value
    return env


class _Expander(ast.NodeTransformer):
    def __init__(self, env: Dict[str, ast.expr], seen: frozenset, depth: int):
        self.env = env
        self.seen = seen
        self.depth = depth

    def visit_Name(self, node: ast.Name) -> ast.AST:  # noqa: N802
        if self.depth >= _MAX_EXPANSION_DEPTH or node.id in self.seen:
            return node
        value = self.env.get(node.id)
        if value is None:
            return node
        return _Expander(self.env, self.seen | {node.id}, self.depth + 1).visit(
            ast.parse(ast.unparse(value), mode="eval").body
        )


def _expand(node: ast.expr, env: Dict[str, ast.expr]) -> ast.expr:
    try:
        return _Expander(env, frozenset(), 0).visit(ast.parse(ast.unparse(node), mode="eval").body)
    except Exception:
        return node


def _free_names(node: ast.expr) -> set:
    bound: set = set()
    for sub in ast.walk(node):
        if isinstance(sub, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in sub.generators:
                for name in ast.walk(gen.target):
                    if isinstance(name, ast.Name):
                        bound.add(name.id)
        elif isinstance(sub, ast.Lambda):
            for arg in sub.args.args:
                bound.add(arg.arg)
    return {s.id for s in ast.walk(node) if isinstance(s, ast.Name)} - bound


def _fold(node: ast.expr) -> Optional[str]:
    """Evaluate the expression when it depends on nothing outside the block."""
    try:
        if _free_names(node) - set(_FOLD_NAMESPACE):
            return None
        value = eval(  # noqa: S307 - only constants and _FOLD_NAMESPACE reach here
            compile(ast.Expression(body=node), "<verify>", "eval"),
            {"__builtins__": {}},
            dict(_FOLD_NAMESPACE),
        )
    except Exception:
        return None
    if isinstance(value, (int, float, str, bool)) or value is None:
        return repr(value)
    return None


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_EXPR_CHARS else text[: _MAX_EXPR_CHARS - 3] + "..."


def describe_write_arguments(code: Optional[str]) -> str:
    """Render each write argument as the value it will actually be given."""
    text = (code or "").strip()
    if not text:
        return "(no write calls)"
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return "(code does not parse; verify the source directly)"

    assigns = _assignments(tree)
    rows: List[str] = []
    unresolved: set = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_write_call(node):
            continue
        name = _call_name(node) or "<call>"
        env = _env_before(assigns, getattr(node, "lineno", 0))
        args: List[Tuple[str, ast.expr]] = [(kw.arg or "**kwargs", kw.value) for kw in node.keywords]
        args += [(f"arg{i}", value) for i, value in enumerate(node.args)]
        if not args:
            rows.append(f"{name}() — no arguments")
            continue
        for arg_name, value in args:
            try:
                source = ast.unparse(value)
            except Exception:
                continue
            expanded = _expand(value, env)
            try:
                expanded_src = ast.unparse(expanded)
            except Exception:
                expanded_src = source
            folded = _fold(expanded)
            if folded is not None:
                rows.append(f"{name}({arg_name}=) -> {folded}")
            elif expanded_src != source:
                rows.append(f"{name}({arg_name}=) -> {_clip(expanded_src)}")
                unresolved |= _free_names(expanded) - set(_FOLD_NAMESPACE)
            else:
                rows.append(f"{name}({arg_name}=) -> {_clip(source)}")
                unresolved |= _free_names(value) - set(_FOLD_NAMESPACE)
            if len(rows) >= _MAX_ROWS:
                break
        if len(rows) >= _MAX_ROWS:
            break

    if not rows:
        return "(no write calls)"
    out = "\n".join(rows)
    if unresolved:
        out += "\n\nFrom earlier blocks (check these against Variables): " + ", ".join(
            sorted(unresolved)[:15]
        )
    return out
