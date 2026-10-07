"""Restricted candidate-code inspection and tool-call extraction.

Real tools are replaced with recorder stubs during the speculative dry run.
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import copy
import datetime as datetime_module
import json
import re
import time
import typing as typing_module
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class _ResolvedExpression:
    """Verifier-side representation of one Python expression.

    ``static_value`` is populated only when the expression can be evaluated
    deterministically without executing candidate code. ``dependency_call_ids``
    records explicit dependencies on earlier awaited calls in the same candidate.
    """

    rendered: str
    provenance: Literal[
        "literal",
        "local_static",
        "runtime_variable",
        "prior_call_result",
        "derived_from_prior_call_result",
        "unresolved",
    ]
    static_value: Any | None = None
    dependency_call_ids: tuple[str, ...] = ()
    source_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class _LocalBinding:
    resolved: _ResolvedExpression


_PYTHON_BLOCK_RE = re.compile(r"```python\s*(.*?)```", flags=re.IGNORECASE | re.DOTALL)


def _expr_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparseable>"


def _call_name(call: ast.Call) -> str:
    return _expr_text(call.func)


def _static_value_from_expr(
    node: ast.AST,
    bindings: dict[str, _LocalBinding],
) -> tuple[bool, Any, tuple[str, ...]]:
    """Safely evaluate a deliberately small, side-effect-free expression subset."""
    if isinstance(node, ast.Constant):
        return True, node.value, ()

    if isinstance(node, ast.Name):
        binding = bindings.get(node.id)
        if binding is not None and binding.resolved.provenance in {
            "literal",
            "local_static",
            "runtime_variable",
        }:
            return True, binding.resolved.static_value, (node.id,)
        return False, None, (node.id,)

    if isinstance(node, ast.List):
        values: list[Any] = []
        names: list[str] = []
        for item in node.elts:
            ok, value, used = _static_value_from_expr(item, bindings)
            if not ok:
                return False, None, tuple(dict.fromkeys([*names, *used]))
            values.append(value)
            names.extend(used)
        return True, values, tuple(dict.fromkeys(names))

    if isinstance(node, ast.Tuple):
        values: list[Any] = []
        names: list[str] = []
        for item in node.elts:
            ok, value, used = _static_value_from_expr(item, bindings)
            if not ok:
                return False, None, tuple(dict.fromkeys([*names, *used]))
            values.append(value)
            names.extend(used)
        return True, tuple(values), tuple(dict.fromkeys(names))

    if isinstance(node, ast.Set):
        values: list[Any] = []
        names: list[str] = []
        for item in node.elts:
            ok, value, used = _static_value_from_expr(item, bindings)
            if not ok:
                return False, None, tuple(dict.fromkeys([*names, *used]))
            values.append(value)
            names.extend(used)
        try:
            return True, set(values), tuple(dict.fromkeys(names))
        except TypeError:
            return False, None, tuple(dict.fromkeys(names))

    if isinstance(node, ast.Dict):
        result: dict[Any, Any] = {}
        names: list[str] = []
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                return False, None, tuple(dict.fromkeys(names))
            key_ok, key, key_used = _static_value_from_expr(key_node, bindings)
            value_ok, value, value_used = _static_value_from_expr(
                value_node,
                bindings,
            )
            names.extend(key_used)
            names.extend(value_used)
            if not key_ok or not value_ok:
                return False, None, tuple(dict.fromkeys(names))
            try:
                result[key] = value
            except TypeError:
                return False, None, tuple(dict.fromkeys(names))
        return True, result, tuple(dict.fromkeys(names))

    if isinstance(node, ast.UnaryOp):
        ok, value, used = _static_value_from_expr(node.operand, bindings)
        if not ok:
            return False, None, used
        try:
            if isinstance(node.op, ast.USub):
                return True, -value, used
            if isinstance(node.op, ast.UAdd):
                return True, +value, used
            if isinstance(node.op, ast.Not):
                return True, not value, used
        except Exception:
            return False, None, used
        return False, None, used

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left_ok, left, left_used = _static_value_from_expr(node.left, bindings)
        right_ok, right, right_used = _static_value_from_expr(node.right, bindings)
        used = tuple(dict.fromkeys([*left_used, *right_used]))
        if not left_ok or not right_ok:
            return False, None, used
        try:
            return True, left + right, used
        except Exception:
            return False, None, used

    if isinstance(node, ast.JoinedStr):
        pieces: list[str] = []
        names: list[str] = []
        for item in node.values:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                pieces.append(item.value)
                continue
            if not isinstance(item, ast.FormattedValue):
                return False, None, tuple(dict.fromkeys(names))
            if item.format_spec is not None:
                return False, None, tuple(dict.fromkeys(names))
            ok, value, used = _static_value_from_expr(item.value, bindings)
            names.extend(used)
            if not ok:
                return False, None, tuple(dict.fromkeys(names))
            if item.conversion == 114:  # !r
                pieces.append(repr(value))
            elif item.conversion == 97:  # !a
                pieces.append(ascii(value))
            else:
                pieces.append(str(value))
        return True, "".join(pieces), tuple(dict.fromkeys(names))

    return False, None, ()


def _dependency_call_ids_for_expr(
    node: ast.AST,
    bindings: dict[str, _LocalBinding],
) -> tuple[str, ...]:
    dependencies: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Name):
            continue
        binding = bindings.get(child.id)
        if binding is None:
            continue
        dependencies.extend(binding.resolved.dependency_call_ids)
    return tuple(dict.fromkeys(dependencies))


def _runtime_variable_source_names(
    used_names: tuple[str, ...],
    bindings: dict[str, _LocalBinding],
) -> tuple[str, ...]:
    """Return underlying CUGA runtime-variable names used by an expression."""
    runtime_names: list[str] = []
    for name in used_names:
        binding = bindings.get(name)
        if binding is None or binding.resolved.provenance != "runtime_variable":
            continue
        runtime_names.extend(binding.resolved.source_names or (name,))
    return tuple(dict.fromkeys(runtime_names))


def _resolve_expression(
    node: ast.AST,
    bindings: dict[str, _LocalBinding],
    *,
    direct_literal: bool = False,
) -> _ResolvedExpression:
    """Resolve a candidate expression without executing arbitrary Python."""
    # Preserve provenance for direct references rather than relabeling them as
    # generic locals. In particular, a direct name bound to an earlier tool call
    # remains a direct prior_call_result, while a name already produced by a local
    # transformation of that result remains derived_from_prior_call_result.
    if isinstance(node, ast.Name):
        binding = bindings.get(node.id)
        if binding is not None and binding.resolved.provenance in {
            "runtime_variable",
            "prior_call_result",
            "derived_from_prior_call_result",
        }:
            return binding.resolved

    ok, value, used_names = _static_value_from_expr(node, bindings)
    if ok:
        runtime_names = _runtime_variable_source_names(used_names, bindings)
        provenance: Literal[
            "literal",
            "local_static",
            "runtime_variable",
            "prior_call_result",
            "derived_from_prior_call_result",
            "unresolved",
        ]
        if runtime_names:
            provenance = "runtime_variable"
        else:
            provenance = "literal" if direct_literal and not used_names else "local_static"
        return _ResolvedExpression(
            rendered=repr(value),
            provenance=provenance,
            static_value=value,
            source_names=runtime_names or used_names,
        )

    dependencies = _dependency_call_ids_for_expr(node, bindings)
    source_names = tuple(dict.fromkeys(child.id for child in ast.walk(node) if isinstance(child, ast.Name)))
    rendered = _expr_text(node)

    if dependencies:
        # A non-name expression that depends on an earlier awaited call is not
        # unresolved: its concrete value is future, but its dataflow provenance is
        # known. We intentionally do not try to execute/interpret transformations
        # such as re.search(...), .group(), .strip(), indexing, or parsing here.
        return _ResolvedExpression(
            rendered=rendered,
            provenance="derived_from_prior_call_result",
            dependency_call_ids=dependencies,
            source_names=source_names,
        )

    return _ResolvedExpression(
        rendered=rendered,
        provenance="unresolved",
        source_names=source_names,
    )


def _simple_assignment_names(statement: ast.stmt) -> list[str]:
    targets: list[ast.AST] = []
    if isinstance(statement, ast.Assign):
        targets = list(statement.targets)
    elif isinstance(statement, ast.AnnAssign):
        targets = [statement.target]

    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
    return names


def _awaited_call_from_statement(
    statement: ast.stmt,
) -> tuple[ast.Call, str | None] | None:
    value: ast.AST | None = None
    assigned_to: str | None = None

    if isinstance(statement, ast.Assign):
        value = statement.value
        assigned_to = ", ".join(_expr_text(target) for target in statement.targets)
    elif isinstance(statement, ast.AnnAssign):
        value = statement.value
        assigned_to = _expr_text(statement.target)
    elif isinstance(statement, ast.Expr):
        value = statement.value

    if isinstance(value, ast.Await) and isinstance(value.value, ast.Call):
        return value.value, assigned_to
    return None


class _DryRunBlockedOperation(RuntimeError):
    """Candidate operation intentionally unavailable in verifier dry-run."""


class _DryRunSymbolicDependency(RuntimeError):
    """Concrete dry-run cannot continue because it needs a future tool result."""


@dataclass(frozen=True)
class _DryRunToolResult:
    """Opaque future value returned by a recorder stub instead of a real tool."""

    call_id: str
    tool_name: str

    def __bool__(self) -> bool:
        raise _DryRunSymbolicDependency(f"Control flow depends on future result_of({self.call_id})")

    def __str__(self) -> str:
        raise _DryRunSymbolicDependency(f"String conversion depends on future result_of({self.call_id})")

    def __format__(self, format_spec: str) -> str:
        raise _DryRunSymbolicDependency(f"Formatting depends on future result_of({self.call_id})")


class _DryRunAsyncioModule:
    """Small asyncio facade without network/subprocess/event-loop escape hatches."""

    gather = staticmethod(asyncio.gather)
    create_task = staticmethod(asyncio.create_task)
    wait = staticmethod(asyncio.wait)
    as_completed = staticmethod(asyncio.as_completed)
    sleep = staticmethod(asyncio.sleep)
    Queue = asyncio.Queue
    Lock = asyncio.Lock
    Event = asyncio.Event
    Semaphore = asyncio.Semaphore


_DRY_RUN_ALLOWED_MODULES: dict[str, Any] = {
    "re": re,
    "json": json,
    "typing": typing_module,
    "datetime": datetime_module,
    "time": time,
    "asyncio": _DryRunAsyncioModule(),
}

_DRY_RUN_FORBIDDEN_NAMES = {
    "open",
    "eval",
    "exec",
    "compile",
    "globals",
    "locals",
    "vars",
    "input",
    "breakpoint",
    "help",
    "getattr",
    "setattr",
    "delattr",
    "__import__",
}


class _DryRunSafetyValidator(ast.NodeVisitor):
    """Block capability-bearing code while permitting ordinary local Python."""

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root not in _DRY_RUN_ALLOWED_MODULES:
                raise _DryRunBlockedOperation(f"Import {alias.name!r} is unavailable in verifier dry-run")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        if node.level:
            raise _DryRunBlockedOperation("Relative imports are unavailable in verifier dry-run")
        root = str(node.module or "").split(".", 1)[0]
        if root not in _DRY_RUN_ALLOWED_MODULES:
            raise _DryRunBlockedOperation(f"Import from {node.module!r} is unavailable in verifier dry-run")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> Any:
        if isinstance(node.ctx, ast.Load) and node.id in _DRY_RUN_FORBIDDEN_NAMES:
            raise _DryRunBlockedOperation(f"Name {node.id!r} is unavailable in verifier dry-run")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        # Prevent reflection escapes such as ``obj.__class__.__mro__`` while
        # leaving normal methods (.group/.strip/.split/etc.) completely usable.
        if node.attr.startswith("__"):
            raise _DryRunBlockedOperation(
                f"Dunder attribute {node.attr!r} is unavailable in verifier dry-run"
            )
        self.generic_visit(node)


class _DryRunToolCallTransformer(ast.NodeTransformer):
    """Replace awaited real-tool calls with verifier recorder calls."""

    def __init__(self, *, tool_names: set[str], runtime_names: set[str]) -> None:
        self.tool_names = set(tool_names)
        self.runtime_names = set(runtime_names)
        self._assigned_to: str | None = None

    def _expr_meta(self, node: ast.AST) -> dict[str, Any]:
        try:
            ast.literal_eval(node)
            literal = True
        except Exception:
            literal = False
        names = [child.id for child in ast.walk(node) if isinstance(child, ast.Name)]
        return {
            "expr": _expr_text(node),
            "literal": literal,
            "direct_runtime_name": (
                node.id if isinstance(node, ast.Name) and node.id in self.runtime_names else None
            ),
            "source_names": list(dict.fromkeys(names)),
        }

    def visit_Assign(self, node: ast.Assign) -> Any:
        previous = self._assigned_to
        self._assigned_to = ", ".join(_expr_text(target) for target in node.targets)
        node.value = self.visit(node.value)
        self._assigned_to = previous
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        previous = self._assigned_to
        self._assigned_to = _expr_text(node.target)
        if node.value is not None:
            node.value = self.visit(node.value)
        self._assigned_to = previous
        return node

    def visit_Expr(self, node: ast.Expr) -> Any:
        previous = self._assigned_to
        self._assigned_to = None
        node.value = self.visit(node.value)
        self._assigned_to = previous
        return node

    def visit_Await(self, node: ast.Await) -> Any:
        value = node.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in self.tool_names
        ):
            metadata = {
                "positional": [self._expr_meta(arg) for arg in value.args],
                "keywords": [
                    {
                        "name": kw.arg,
                        **self._expr_meta(kw.value),
                    }
                    for kw in value.keywords
                ],
            }
            replacement = ast.Await(
                value=ast.Call(
                    func=ast.Name(id="__verifier_tool_call__", ctx=ast.Load()),
                    args=[
                        ast.Constant(value=value.func.id),
                        ast.Constant(value=self._assigned_to),
                        ast.Constant(
                            value=json.dumps(
                                metadata,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                        ),
                        *[self.visit(arg) for arg in value.args],
                    ],
                    keywords=[ast.keyword(arg=kw.arg, value=self.visit(kw.value)) for kw in value.keywords],
                )
            )
            return ast.copy_location(replacement, node)
        return self.generic_visit(node)


def _dry_run_import(
    name: str,
    globals: dict[str, Any] | None = None,
    locals: dict[str, Any] | None = None,
    fromlist: tuple[str, ...] | list[str] = (),
    level: int = 0,
) -> Any:
    if level:
        raise _DryRunBlockedOperation("Relative imports are unavailable in verifier dry-run")
    root = str(name or "").split(".", 1)[0]
    module = _DRY_RUN_ALLOWED_MODULES.get(root)
    if module is None:
        raise _DryRunBlockedOperation(f"Import {name!r} is unavailable in verifier dry-run")
    return module


def _dry_run_builtins() -> dict[str, Any]:
    """Broad local-computation builtins with capability-bearing entries removed."""
    allowed_names = {
        "abs",
        "all",
        "any",
        "ascii",
        "bin",
        "bool",
        "bytearray",
        "bytes",
        "callable",
        "chr",
        "complex",
        "dict",
        "divmod",
        "enumerate",
        "filter",
        "float",
        "format",
        "frozenset",
        "hash",
        "hex",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "map",
        "max",
        "memoryview",
        "min",
        "next",
        "object",
        "oct",
        "ord",
        "pow",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "type",
        "zip",
        "BaseException",
        "Exception",
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "IndexError",
        "KeyError",
        "LookupError",
        "RuntimeError",
        "StopIteration",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
    }
    result = {name: getattr(builtins, name) for name in allowed_names if hasattr(builtins, name)}
    result["__import__"] = _dry_run_import
    # Printing is execution-protocol mechanics here. Do not emit anything and do
    # not call str()/repr() on symbolic future tool results.
    result["print"] = lambda *args, **kwargs: None
    return result


def _dry_run_clone(value: Any) -> Any:
    """Avoid mutating live VariablesManager values during speculative execution."""
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _dry_run_dependency_call_ids(value: Any) -> tuple[str, ...]:
    dependencies: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, _DryRunToolResult):
            dependencies.append(item.call_id)
            return
        if isinstance(item, dict):
            for key, val in item.items():
                collect(key)
                collect(val)
            return
        if isinstance(item, (list, tuple, set, frozenset)):
            for child in item:
                collect(child)

    collect(value)
    return tuple(dict.fromkeys(dependencies))


def _dry_run_resolved_expression(
    value: Any,
    metadata: dict[str, Any] | None,
) -> _ResolvedExpression:
    metadata = dict(metadata or {})
    expression = str(metadata.get("expr") or repr(value))
    source_names = tuple(str(name) for name in metadata.get("source_names", []) if str(name))

    if isinstance(value, _DryRunToolResult):
        return _ResolvedExpression(
            rendered=f"result_of({value.call_id})",
            provenance="prior_call_result",
            dependency_call_ids=(value.call_id,),
            source_names=source_names,
        )

    dependencies = _dry_run_dependency_call_ids(value)
    if dependencies:
        return _ResolvedExpression(
            rendered=expression,
            provenance="derived_from_prior_call_result",
            dependency_call_ids=dependencies,
            source_names=source_names,
        )

    direct_runtime_name = str(metadata.get("direct_runtime_name") or "").strip()
    if direct_runtime_name:
        provenance: Literal[
            "literal",
            "local_static",
            "runtime_variable",
            "prior_call_result",
            "derived_from_prior_call_result",
            "unresolved",
        ] = "runtime_variable"
        source_names = (direct_runtime_name,)
    elif bool(metadata.get("literal", False)):
        provenance = "literal"
    else:
        provenance = "local_static"

    return _ResolvedExpression(
        rendered=repr(value),
        provenance=provenance,
        static_value=_dry_run_clone(value),
        source_names=source_names,
    )


async def _extract_candidate_calls_dry_run(
    candidate: str,
    *,
    runtime_variables: dict[str, Any] | None = None,
    tool_names: set[str],
) -> list[dict[str, Any]]:
    """Execute local Python faithfully while replacing every real tool with a stub.

    This is verifier-side speculative execution only. Runtime variables are copied
    into the dry-run function's local namespace. Ordinary deterministic Python is
    then allowed to run normally (regex, parsing, indexing, comprehensions,
    conditionals, helper functions, datetime formatting, etc.). Awaited calls whose
    direct function name is a currently callable CUGA tool are rewritten to an
    async recorder and are never executed against the real environment.
    """
    blocks = _PYTHON_BLOCK_RE.findall(candidate)
    if not blocks:
        return []
    if not tool_names:
        raise _DryRunBlockedOperation("Runtime tool inventory is unavailable for verifier dry-run")

    body: list[ast.stmt] = []
    runtime_names = {
        str(name) for name in (runtime_variables or {}) if isinstance(name, str) and name.isidentifier()
    }
    transformer = _DryRunToolCallTransformer(
        tool_names=tool_names,
        runtime_names=runtime_names,
    )

    for block in blocks:
        tree = ast.parse(block)
        _DryRunSafetyValidator().visit(tree)
        transformed = transformer.visit(tree)
        ast.fix_missing_locations(transformed)
        body.extend(transformed.body)

    runtime_assignments: list[ast.stmt] = []
    for name in sorted(runtime_names):
        runtime_assignments.append(
            ast.Assign(
                targets=[ast.Name(id=name, ctx=ast.Store())],
                value=ast.Subscript(
                    value=ast.Name(id="__runtime_variables__", ctx=ast.Load()),
                    slice=ast.Constant(value=name),
                    ctx=ast.Load(),
                ),
            )
        )

    dry_function = ast.AsyncFunctionDef(
        name="__verifier_dry_run__",
        args=ast.arguments(
            posonlyargs=[],
            args=[],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        ),
        body=[*runtime_assignments, *body] or [ast.Pass()],
        decorator_list=[],
        returns=None,
        type_comment=None,
    )
    module = ast.Module(body=[dry_function], type_ignores=[])
    ast.fix_missing_locations(module)

    extracted: list[dict[str, Any]] = []

    async def recorder(
        tool_name: str,
        assigned_to: str | None,
        metadata_json: str,
        *args: Any,
        **kwargs: Any,
    ) -> _DryRunToolResult:
        call_id = f"C{len(extracted) + 1}"
        try:
            metadata = json.loads(metadata_json)
        except Exception:
            metadata = {}
        positional_meta = list(metadata.get("positional") or [])
        keyword_meta_rows = list(metadata.get("keywords") or [])
        keyword_meta = {
            str(row.get("name")): row
            for row in keyword_meta_rows
            if isinstance(row, dict) and row.get("name") is not None
        }

        positional_args = [
            _dry_run_resolved_expression(
                value,
                positional_meta[index] if index < len(positional_meta) else None,
            )
            for index, value in enumerate(args)
        ]
        keyword_args = {
            name: _dry_run_resolved_expression(value, keyword_meta.get(name))
            for name, value in kwargs.items()
        }

        extracted.append(
            {
                "call_id": call_id,
                "call": str(tool_name),
                "positional_args": positional_args,
                "keyword_args": keyword_args,
                "assigned_to": assigned_to,
            }
        )
        return _DryRunToolResult(call_id=call_id, tool_name=str(tool_name))

    dry_globals: dict[str, Any] = {
        "__builtins__": _dry_run_builtins(),
        "__name__": "__prompt_verifier_dry_run__",
        "__runtime_variables__": {
            name: _dry_run_clone(value)
            for name, value in (runtime_variables or {}).items()
            if isinstance(name, str) and name.isidentifier()
        },
        "__verifier_tool_call__": recorder,
    }
    compiled = compile(module, "<prompt-verifier-dry-run>", "exec")
    exec(compiled, dry_globals, dry_globals)
    await dry_globals["__verifier_dry_run__"]()
    return extracted


def _extract_candidate_calls_static(
    candidate: str,
    *,
    runtime_variables: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Conservative AST extraction when restricted execution cannot proceed."""
    blocks = _PYTHON_BLOCK_RE.findall(candidate)
    if not blocks:
        return []

    extracted: list[dict[str, Any]] = []
    bindings: dict[str, _LocalBinding] = {
        name: _LocalBinding(
            resolved=_ResolvedExpression(
                rendered=repr(value),
                provenance="runtime_variable",
                static_value=value,
                source_names=(name,),
            )
        )
        for name, value in (runtime_variables or {}).items()
        if isinstance(name, str) and name.isidentifier()
    }

    for block in blocks:
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue

        for statement in tree.body:
            awaited = _awaited_call_from_statement(statement)
            if awaited is not None:
                call, assigned_to = awaited
                call_id = f"C{len(extracted) + 1}"

                positional_args = [
                    _resolve_expression(arg, bindings, direct_literal=True) for arg in call.args
                ]
                keyword_args = {
                    (kw.arg or "**"): _resolve_expression(
                        kw.value,
                        bindings,
                        direct_literal=True,
                    )
                    for kw in call.keywords
                }

                extracted.append(
                    {
                        "call_id": call_id,
                        "call": _call_name(call),
                        "positional_args": positional_args,
                        "keyword_args": keyword_args,
                        "assigned_to": assigned_to,
                    }
                )

                for name in _simple_assignment_names(statement):
                    bindings[name] = _LocalBinding(
                        resolved=_ResolvedExpression(
                            rendered=f"result_of({call_id})",
                            provenance="prior_call_result",
                            dependency_call_ids=(call_id,),
                            source_names=(name,),
                        )
                    )
                continue

            assignment_value: ast.AST | None = None
            if isinstance(statement, ast.Assign):
                assignment_value = statement.value
            elif isinstance(statement, ast.AnnAssign):
                assignment_value = statement.value

            if assignment_value is None:
                continue

            names = _simple_assignment_names(statement)
            if not names:
                continue

            resolved = _resolve_expression(
                assignment_value,
                bindings,
                direct_literal=False,
            )
            for name in names:
                if resolved.provenance in {"literal", "local_static"}:
                    bound = _ResolvedExpression(
                        rendered=resolved.rendered,
                        provenance="local_static",
                        static_value=resolved.static_value,
                        dependency_call_ids=resolved.dependency_call_ids,
                        source_names=tuple(dict.fromkeys([name, *resolved.source_names])),
                    )
                elif resolved.provenance == "runtime_variable":
                    bound = _ResolvedExpression(
                        rendered=resolved.rendered,
                        provenance="runtime_variable",
                        static_value=resolved.static_value,
                        dependency_call_ids=resolved.dependency_call_ids,
                        source_names=resolved.source_names or (name,),
                    )
                elif resolved.provenance in {
                    "prior_call_result",
                    "derived_from_prior_call_result",
                }:
                    bound = _ResolvedExpression(
                        rendered=resolved.rendered,
                        provenance=resolved.provenance,
                        dependency_call_ids=resolved.dependency_call_ids,
                        source_names=tuple(dict.fromkeys([name, *resolved.source_names])),
                    )
                else:
                    bound = _ResolvedExpression(
                        rendered=resolved.rendered,
                        provenance="unresolved",
                        source_names=tuple(dict.fromkeys([name, *resolved.source_names])),
                    )
                bindings[name] = _LocalBinding(resolved=bound)

    return extracted
