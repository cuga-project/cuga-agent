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
import operator
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


# Methods that only read their receiver. Exempt on any receiver: ``client.get``
# is a read even when the receiver came from outside the block.
READ_ONLY_METHODS = frozenset(
    {
        "copy",
        "count",
        "encode",
        "endswith",
        "find",
        "format",
        "get",
        "index",
        "items",
        "join",
        "keys",
        "lower",
        "lstrip",
        "replace",
        "rsplit",
        "rstrip",
        "split",
        "startswith",
        "strip",
        "title",
        "upper",
        "values",
    }
)

# Methods that mutate their receiver. Exempt only when the receiver is a name
# bound in this block: ``rows.append(x)`` on a local list is skipped, while
# ``client.update(...)`` on an object from outside the block is verified.
LOCAL_MUTATORS = frozenset(
    {
        "add",
        "append",
        "clear",
        "extend",
        "insert",
        "pop",
        "remove",
        "reverse",
        "setdefault",
        "sort",
        "update",
    }
)

CONTAINER_METHODS = READ_ONLY_METHODS | LOCAL_MUTATORS

_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _receiver_root(expr: ast.expr) -> Optional[str]:
    """Name at the root of ``a.b[0].c`` — None when the receiver is not a name."""
    while isinstance(expr, (ast.Attribute, ast.Subscript)):
        expr = expr.value
    return expr.id if isinstance(expr, ast.Name) else None


def _target_names(target: ast.expr) -> List[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for elt in target.elts for n in _target_names(elt)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


def _bound_names(tree: ast.AST) -> set:
    """Names this block binds itself (assignments, loop and with targets)."""
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign,)):
            for target in node.targets:
                names.update(_target_names(target))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor, ast.comprehension)):
            names.update(_target_names(node.target))
        elif isinstance(node, ast.NamedExpr):
            names.update(_target_names(node.target))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    names.update(_target_names(item.optional_vars))
    return names


def _is_write_call(node: ast.Call, local_names: set) -> bool:
    name = _call_name(node)
    if not name:
        return True
    if isinstance(node.func, ast.Attribute):
        if name in READ_ONLY_METHODS:
            return False
        if name in LOCAL_MUTATORS:
            root = _receiver_root(node.func.value)
            return root is None or root not in local_names
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
    local_names = _bound_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_write_call(node, local_names):
            return True
    return False


_Scope = Optional[ast.AST]
_Assignment = Tuple[int, str, ast.expr, _Scope]


def _scope_chains(tree: ast.AST) -> Dict[int, Tuple[ast.AST, ...]]:
    """id(node) -> enclosing function/class/lambda nodes, innermost first.

    An empty chain means module level. A ``def`` statement itself belongs to
    the scope that contains it; only its body is inside the new scope.
    """
    chains: Dict[int, Tuple[ast.AST, ...]] = {}

    def visit(node: ast.AST, chain: Tuple[ast.AST, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            chains[id(child)] = chain
            visit(child, (child,) + chain if isinstance(child, _SCOPE_NODES) else chain)

    visit(tree, ())
    return chains


def _assignments(tree: ast.AST, chains: Dict[int, Tuple[ast.AST, ...]]) -> List[_Assignment]:
    """Single-target name assignments with their lexical scope, in source order."""
    out: List[_Assignment] = []
    for node in ast.walk(tree):
        chain = chains.get(id(node), ())
        scope: _Scope = chain[0] if chain else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                out.append((getattr(node, "lineno", 0), target.id, node.value, scope))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            out.append((getattr(node, "lineno", 0), node.target.id, node.value, scope))
    out.sort(key=lambda item: item[0])
    return out


def _env_before(assigns: List[_Assignment], lineno: int, chain: Tuple[ast.AST, ...]) -> Dict[str, ast.expr]:
    """Assignments visible at ``lineno`` from the call's own scope chain.

    A name bound inside a nested ``def`` is not visible to a call outside it,
    so a helper's local ``amount`` never overrides the module-level one.
    """
    visible = {id(scope) for scope in chain}
    env: Dict[str, ast.expr] = {}
    for line, name, value, scope in assigns:
        if line >= lineno:
            continue
        if scope is not None and id(scope) not in visible:
            continue
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


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_}
_MAX_POW_EXPONENT = 64
_MAX_FOLDED_STR = 4096
_MAX_FOLDED_INT_BITS = 4096
_MAX_FOLD_NODES = 128
_MAX_FOLDED_AGGREGATE_SIZE = 16_384


def _validate_folded_value(value: Any) -> Any:
    """Reject intermediates that could make host-side folding expensive."""
    remaining = _MAX_FOLDED_AGGREGATE_SIZE

    def charge(amount: int) -> None:
        nonlocal remaining
        remaining -= amount
        if remaining < 0:
            raise ValueError("folded aggregate too large")

    def visit(item: Any) -> None:
        if isinstance(item, bool) or item is None:
            charge(1)
            return
        if isinstance(item, int):
            if item.bit_length() > _MAX_FOLDED_INT_BITS:
                raise ValueError("folded integer too large")
            charge(max(1, (item.bit_length() + 7) // 8))
            return
        if isinstance(item, float):
            charge(8)
            return
        if isinstance(item, str):
            if len(item) > _MAX_FOLDED_STR:
                raise ValueError("folded string too large")
            charge(max(1, len(item)))
            return
        if isinstance(item, (list, tuple)):
            if len(item) > _MAX_FOLDED_STR:
                raise ValueError("folded sequence too large")
            charge(max(1, len(item)))
            for child in item:
                visit(child)
            return
        raise ValueError(f"unsupported folded value {type(item).__name__}")

    visit(value)
    return value


def _validate_percent_format(format_string: str) -> None:
    """Allow small scalar percent-formatting without unbounded widths."""
    if len(format_string) > 256:
        raise ValueError("format string too large")
    index = 0
    projected_width = 0
    conversions = 0
    while index < len(format_string):
        if format_string[index] != "%":
            index += 1
            continue
        index += 1
        if index < len(format_string) and format_string[index] == "%":
            index += 1
            continue
        if index < len(format_string) and format_string[index] == "(":
            raise ValueError("mapping percent-formatting is not folded")
        while index < len(format_string) and format_string[index] in "#0- +":
            index += 1
        if index < len(format_string) and format_string[index] == "*":
            raise ValueError("dynamic format width is not folded")
        width_start = index
        while index < len(format_string) and format_string[index].isdigit():
            index += 1
        width = int(format_string[width_start:index] or "0")
        precision = 0
        if index < len(format_string) and format_string[index] == ".":
            index += 1
            if index < len(format_string) and format_string[index] == "*":
                raise ValueError("dynamic format precision is not folded")
            precision_start = index
            while index < len(format_string) and format_string[index].isdigit():
                index += 1
            precision = int(format_string[precision_start:index] or "0")
        while index < len(format_string) and format_string[index] in "hlL":
            index += 1
        if index >= len(format_string) or format_string[index] not in "diouxXeEfFgGcrsa":
            raise ValueError("unsupported percent-format specifier")
        index += 1
        conversions += 1
        projected_width += max(1, width, precision)
        if conversions > 32 or projected_width > _MAX_FOLDED_STR:
            raise ValueError("formatted value too large")


def _validate_binop_before_eval(op: ast.operator, left: Any, right: Any) -> None:
    """Reject operations whose result would exceed the folding limits."""
    if isinstance(op, ast.Mod) and isinstance(left, str):
        _validate_percent_format(left)
    if isinstance(op, ast.Mult):
        sequence: Optional[Any] = None
        multiplier: Optional[int] = None
        if isinstance(left, (str, list, tuple)) and isinstance(right, int):
            sequence, multiplier = left, right
        elif isinstance(right, (str, list, tuple)) and isinstance(left, int):
            sequence, multiplier = right, left
        if sequence is not None and multiplier is not None:
            result_length = len(sequence) * max(multiplier, 0)
            if result_length > _MAX_FOLDED_STR:
                raise ValueError("folded sequence too large")
    if (
        isinstance(op, ast.Pow)
        and isinstance(left, int)
        and isinstance(right, int)
        and right > 0
        and left not in (-1, 0, 1)
        and left.bit_length() * right > _MAX_FOLDED_INT_BITS
    ):
        raise ValueError("folded integer too large")


def _safe_eval(node: ast.AST) -> Any:
    """Evaluate literals, arithmetic and direct ``_FOLD_NAMESPACE`` calls only.

    The proposed code is model-generated and has not run in the sandbox yet,
    so this never hands it to ``eval``: attribute access, subscripts,
    comprehensions, lambdas and indirect calls are rejected outright.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, str, bool)) or node.value is None:
            return _validate_folded_value(node.value)
        raise ValueError("unsupported constant")
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _validate_folded_value(_UNARY_OPS[type(node.op)](_safe_eval(node.operand)))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and (
            not isinstance(right, (int, float)) or abs(right) > _MAX_POW_EXPONENT
        ):
            raise ValueError("exponent too large")
        _validate_binop_before_eval(node.op, left, right)
        value = _BIN_OPS[type(node.op)](left, right)
        return _validate_folded_value(value)
    if isinstance(node, (ast.Tuple, ast.List)):
        items = [_safe_eval(elt) for elt in node.elts]
        return _validate_folded_value(tuple(items) if isinstance(node, ast.Tuple) else items)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FOLD_NAMESPACE:
        if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
            kw.arg is None for kw in node.keywords
        ):
            raise ValueError("star arguments are not folded")
        args = [_safe_eval(arg) for arg in node.args]
        kwargs = {kw.arg: _safe_eval(kw.value) for kw in node.keywords if kw.arg}
        if node.func.id == "sum":
            values = args[0] if args else kwargs.get("iterable")
            start = args[1] if len(args) > 1 else kwargs.get("start", 0)
            if not isinstance(values, (list, tuple)) or not all(
                isinstance(item, (int, float, bool)) for item in values
            ):
                raise ValueError("only numeric sequences are summed")
            if not isinstance(start, (int, float, bool)):
                raise ValueError("sum start must be numeric")
        if node.func.id == "round":
            ndigits = args[1] if len(args) > 1 else kwargs.get("ndigits")
            if ndigits is not None and (not isinstance(ndigits, int) or abs(ndigits) > 1000):
                raise ValueError("round precision too large")
        return _validate_folded_value(_FOLD_NAMESPACE[node.func.id](*args, **kwargs))
    raise ValueError(f"unsupported node {type(node).__name__}")


def _fold(node: ast.expr) -> Optional[str]:
    """Evaluate the expression when it depends on nothing outside the block."""
    try:
        if sum(1 for _ in ast.walk(node)) > _MAX_FOLD_NODES:
            return None
        value = _safe_eval(node)
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

    chains = _scope_chains(tree)
    assigns = _assignments(tree, chains)
    local_names = _bound_names(tree)
    rows: List[str] = []
    unresolved: set = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_write_call(node, local_names):
            continue
        name = _call_name(node) or "<call>"
        env = _env_before(assigns, getattr(node, "lineno", 0), chains.get(id(node), ()))
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
