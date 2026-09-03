import ast
from typing import List, Optional, Sequence, Tuple


def _is_future_import(node: ast.stmt) -> bool:
    return isinstance(node, ast.ImportFrom) and node.module == "__future__"


def _is_print_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"


def _split_user_code(code: str) -> Tuple[List[ast.stmt], List[ast.stmt]]:
    tree = ast.parse(code)
    futures = [stmt for stmt in tree.body if _is_future_import(stmt)]
    body = [stmt for stmt in tree.body if not _is_future_import(stmt)]
    return futures, body


def _maybe_auto_print(body: List[ast.stmt]) -> List[ast.stmt]:
    if not body:
        return body
    last = body[-1]
    if not isinstance(last, ast.Expr) or _is_print_call(last.value):
        return body
    printed = ast.Expr(
        value=ast.Call(
            func=ast.Name(id="print", ctx=ast.Load()),
            args=[last.value],
            keywords=[],
        )
    )
    return [*body[:-1], printed]


def _return_locals() -> ast.Return:
    return ast.Return(value=ast.Call(func=ast.Name(id="locals", ctx=ast.Load()), args=[], keywords=[]))


_FREEZE_SETUP = """\
import datetime as _cuga_dtmod, time as _cuga_tmod, calendar as _cuga_cal
_cuga_odt = _cuga_dtmod.datetime
_cuga_odate = _cuga_dtmod.date
_cuga_ft = _cuga_odt.fromisoformat({fake_datetime!r})
_cuga_epoch = float(_cuga_cal.timegm(_cuga_ft.timetuple()))
_cuga_tt = _cuga_ft.timetuple()
_cuga_otime = (_cuga_tmod.time, _cuga_tmod.localtime, _cuga_tmod.gmtime, _cuga_tmod.strftime)
class _CugaDatetime:
    def __new__(cls, *a, **k): return _cuga_odt(*a, **k)
    @staticmethod
    def now(tz=None): return _cuga_ft.replace(tzinfo=tz) if tz else _cuga_ft
    @staticmethod
    def today(): return _cuga_ft
    @staticmethod
    def utcnow(): return _cuga_ft
    @staticmethod
    def fromisoformat(s): return _cuga_odt.fromisoformat(s)
    @staticmethod
    def strptime(s, f): return _cuga_odt.strptime(s, f)
    @staticmethod
    def combine(*a, **k): return _cuga_odt.combine(*a, **k)
    @staticmethod
    def fromtimestamp(ts, tz=None): return _cuga_odt.fromtimestamp(ts, tz)
    @staticmethod
    def utcfromtimestamp(ts): return _cuga_odt.utcfromtimestamp(ts)
    @staticmethod
    def fromordinal(o): return _cuga_odt.fromordinal(o)
_CugaDatetime.min = _cuga_odt.min
_CugaDatetime.max = _cuga_odt.max
_CugaDatetime.resolution = _cuga_odt.resolution
class _CugaDate:
    def __new__(cls, *a, **k): return _cuga_odate(*a, **k)
    @staticmethod
    def today(): return _cuga_odate(_cuga_ft.year, _cuga_ft.month, _cuga_ft.day)
    @staticmethod
    def fromisoformat(s): return _cuga_odate.fromisoformat(s)
    @staticmethod
    def fromtimestamp(ts): return _cuga_odate.fromtimestamp(ts)
    @staticmethod
    def fromordinal(o): return _cuga_odate.fromordinal(o)
_CugaDate.min = _cuga_odate.min
_CugaDate.max = _cuga_odate.max
_CugaDate.resolution = _cuga_odate.resolution
_cuga_dtmod.datetime = _CugaDatetime
_cuga_dtmod.date = _CugaDate
_cuga_tmod.time = lambda: _cuga_epoch
_cuga_tmod.localtime = lambda secs=None: (_cuga_tt if secs is None else _cuga_otime[1](secs))
_cuga_tmod.gmtime = lambda secs=None: (_cuga_tt if secs is None else _cuga_otime[2](secs))
_cuga_tmod.strftime = lambda fmt, t=None: _cuga_otime[3](fmt, _cuga_tt if t is None else t)
"""

_FREEZE_FINALLY = """\
_cuga_dtmod.datetime = _cuga_odt
_cuga_dtmod.date = _cuga_odate
_cuga_tmod.time, _cuga_tmod.localtime, _cuga_tmod.gmtime, _cuga_tmod.strftime = _cuga_otime
"""


def _function_body(user_body: List[ast.stmt], fake_datetime: Optional[str]) -> List[ast.stmt]:
    payload = [*user_body, _return_locals()]
    if not fake_datetime:
        return payload
    setup = ast.parse(_FREEZE_SETUP.format(fake_datetime=fake_datetime)).body
    finally_body = ast.parse(_FREEZE_FINALLY).body
    try_stmt = ast.Try(body=payload, handlers=[], orelse=[], finalbody=finally_body)
    return [*setup, try_stmt]


def _render_async_def(
    func_name: str,
    func_body: List[ast.stmt],
    *,
    futures: Sequence[ast.stmt] = (),
    imports: Sequence[str] = (),
) -> str:
    import_src = "\n".join(f"import {name}" for name in imports)
    prefix = f"{import_src}\n" if import_src else ""
    tree = ast.parse(f"{prefix}async def {func_name}():\n    pass\n")
    tree.body = [*futures, *tree.body]
    func = tree.body[-1]
    if not isinstance(func, ast.AsyncFunctionDef):
        raise TypeError(f"expected AsyncFunctionDef, got {type(func).__name__}")
    func.body = func_body or [ast.Pass()]
    ast.fix_missing_locations(tree)
    return ast.unparse(tree) + "\n"


class CodeWrapper:
    """Handles wrapping user code for async execution."""

    @staticmethod
    def build_async_main(user_code: str, fake_datetime: Optional[str] = None) -> str:
        """Build the ``async def _async_main():`` block wrapping the user code.

        ``user_code`` is the original agent source (module-level, not pre-indented).
        The wrapper is assembled with ``ast`` so string-literal values are not
        rewritten by a textual indent.

        When ``fake_datetime`` is set (benchmark mode), freeze the **wall clock**
        that AppWorld freezes for the agent's own code — ``datetime.datetime``,
        ``datetime.date`` and the ``time`` module readouts (``time.time`` /
        ``localtime`` / ``gmtime`` / ``strftime``) — to the task datetime.

        Crucially it does NOT touch ``time.monotonic`` / ``perf_counter`` /
        ``sleep``. freezegun freezes those too, and asyncio's event loop uses
        ``monotonic`` for every timer — so freezing it makes any ``await`` inside
        the user code (e.g. the LLM-backed ``find_tools``) hang until the outer
        sandbox timeout. A monotonic-safe hand-rolled patch avoids that while
        still giving the agent the frozen date/time it needs. (Trade-off vs
        freezegun: ``isinstance(x, datetime)`` isn't preserved — the same
        limitation the original datetime-only shim had — which agent code
        effectively never depends on.)

        The patch is scoped with try/finally: originals are restored the moment
        the user code finishes, so nothing leaks into cuga's own clock.
        """
        futures, body = _split_user_code(user_code)
        return _render_async_def(
            "_async_main",
            _function_body(body, fake_datetime),
            futures=futures,
        )

    @staticmethod
    def wrap_in_async_def(code: str, func_name: str) -> str:
        """Splice ``code`` into ``async def {func_name}():`` without text-indent."""
        futures, body = _split_user_code(code)
        return _render_async_def(func_name, body or [ast.Pass()], futures=futures)

    @staticmethod
    def wrap_code(code: str, fake_datetime: Optional[str] = None) -> str:
        """Wrap user code in an async function for execution.

        Args:
            code: User's Python code
            fake_datetime: Optional ISO format date string to freeze time to
                (see build_async_main — freezes datetime, date and time module)

        Returns:
            Wrapped code ready for execution

        Note:
            Workspace CWD is managed externally (shell subprocess `cwd=`,
            filesystem MCP server `cwd=`, sandbox-tool path resolution) — not
            injected into the wrapped user code. Keeping `os` out of the user
            namespace means SecurityValidator.validate_imports() remains the
            sole gate on what imports the wrapped code can reach.
        """
        futures, body = _split_user_code(code)
        body = _maybe_auto_print(body)
        return _render_async_def(
            "_async_main",
            _function_body(body, fake_datetime),
            futures=futures,
            imports=("asyncio",),
        )

    @staticmethod
    def wrap_code_for_code_agent(code: str, fake_datetime: Optional[str] = None) -> str:
        """Wrap CodeAgent source: asyncio+json imports, no last-expression auto-print."""
        futures, body = _split_user_code(code)
        return _render_async_def(
            "_async_main",
            _function_body(body, fake_datetime),
            futures=futures,
            imports=("asyncio", "json"),
        )
