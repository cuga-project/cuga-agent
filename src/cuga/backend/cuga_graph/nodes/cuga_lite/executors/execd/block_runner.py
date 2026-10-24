"""How a code block runs in the execd kernel, and how its outcome comes back.

The local executor gives the agent more than the block's output: a block that
times out, calls ``exit()``, or has a tool call refused by the call budget keeps
the variables it computed up to that point, and the agent is told what
happened. Before this module execd lost all of that — the kernel's
``wait_for`` discarded the frame and only the traceback came back.

The runner below does in the kernel what ``LocalExecutor`` does in-process,
and reports in one framed JSON line, so values travel as data instead of
through ``repr``/``literal_eval``. Variables going *into* the kernel travel the
same way: a stored value without a Python-literal ``repr`` used to make the
next block a syntax error.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Optional

RESULT_MARKER = "CUGA_BLOCK_RESULT:"

# Signals CUGA's tool-call budget raises (tracking/tracker.py). The sandbox sees
# them by type name only — the exception classes live in CUGA.
_BUDGET_ERRORS = (
    "ToolCallBudgetExceeded",
    "BlockToolCallBudgetExceeded",
    "RunToolCallBudgetExceeded",
    "ThreadToolCallBudgetExceeded",
)


def variables_code(variables: dict[str, Any]) -> str:
    """Python that defines the stored variables in the kernel, from JSON.

    Values come out of ``VariablesManager`` already sanitized to JSON shapes;
    anything that still is not JSON is carried as its text, as a tool result is.
    """
    if not variables:
        return ""
    from .tool_callback import to_jsonable

    names = [name for name in variables if name.isidentifier()]
    payload = base64.b64encode(
        json.dumps({name: to_jsonable(variables[name]) for name in names}, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    lines = [
        "import base64 as _cuga_v_b64, json as _cuga_v_json",
        f"_cuga_v = _cuga_v_json.loads(_cuga_v_b64.b64decode({payload!r}).decode('utf-8'))",
    ]
    lines += [f"{name} = _cuga_v[{name!r}]" for name in names]
    lines.append("del _cuga_v")
    return "\n".join(lines) + "\n"


def runner_code(timeout: int) -> str:
    """Kernel code that runs ``_async_main`` and prints one framed result line.

    Mirrors ``LocalExecutor.execute``: the block runs as a task so a timeout can
    read the still-suspended frame before cancelling, ``SystemExit`` and budget
    refusals keep the locals of the frame they unwound, and stdout printed
    before an error is kept alongside it.
    """
    return f'''
import asyncio as _cuga_r_aio, base64 as _cuga_r_b64, json as _cuga_r_json, sys as _cuga_r_sys
import io as _cuga_r_io, traceback as _cuga_r_tb

def _cuga_r_jsonable(v, _depth=0):
    if _depth > 50:
        return str(v)
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, dict):
        return {{str(k): _cuga_r_jsonable(x, _depth + 1) for k, x in v.items()}}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_cuga_r_jsonable(x, _depth + 1) for x in v]
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            pass
    if hasattr(v, "model_dump"):
        try:
            return _cuga_r_jsonable(v.model_dump(), _depth + 1)
        except Exception:
            pass
    return str(v)

def _cuga_r_keep(frame_locals):
    out = {{}}
    for k, v in (frame_locals or {{}}).items():
        if k.startswith("_") or callable(v) or type(v).__name__ == "module":
            continue
        out[k] = _cuga_r_jsonable(v)
    return out

def _cuga_r_frame_locals(error, name="_async_main"):
    frame, tb = None, error.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_name == name:
            frame = tb.tb_frame
        tb = tb.tb_next
    return dict(frame.f_locals) if frame is not None else {{}}

def _cuga_r_coro_locals(coro, name="_async_main"):
    seen = set()
    while coro is not None and id(coro) not in seen:
        seen.add(id(coro))
        frame = getattr(coro, "cr_frame", None)
        while frame is not None:
            if frame.f_code.co_name == name:
                return dict(frame.f_locals)
            frame = frame.f_back
        nxt = getattr(coro, "cr_await", None)
        coro = nxt if _cuga_r_aio.iscoroutine(nxt) else None
    return {{}}

def _cuga_r_exit(*args):
    """exit()/quit() as plain Python has them. IPython's own just return None in a kernel."""
    raise SystemExit(*args)

def _cuga_r_emit(outcome):
    _cuga_r_sys.stdout.flush()
    print("\\n{RESULT_MARKER}" + _cuga_r_b64.b64encode(_cuga_r_json.dumps(outcome).encode("utf-8")).decode("ascii"), flush=True)

async def _cuga_r_run():
    _buf = _cuga_r_io.StringIO()
    _real = _cuga_r_sys.stdout
    class _Tee:
        def write(self, s):
            _buf.write(s)
            return _real.write(s)
        def flush(self):
            _real.flush()
    _cuga_r_sys.stdout = _Tee()

    async def _block():
        try:
            return ("ok", await _async_main())
        except SystemExit as e:
            return ("exit", (e, _cuga_r_frame_locals(e)))

    # The block resolves exit/quit through globals first, before builtins.
    _saved_exit = {{_n: globals().get(_n) for _n in ("exit", "quit")}}
    globals()["exit"] = globals()["quit"] = _cuga_r_exit
    task = _cuga_r_aio.ensure_future(_block())
    try:
        done, _ = await _cuga_r_aio.wait({{task}}, timeout={int(timeout)})
        if not done:
            kept = _cuga_r_keep(_cuga_r_coro_locals(task.get_coro()))
            task.cancel()
            try:
                await task
            except BaseException:
                pass
            _cuga_r_sys.stdout = _real
            _cuga_r_emit({{"status": "timeout", "locals": kept, "stdout": _buf.getvalue()}})
            return
        try:
            kind, value = task.result()
        except Exception as e:
            _cuga_r_sys.stdout = _real
            name = type(e).__name__
            # Tool errors come back from CUGA as _CugaToolError carrying the original type.
            name = getattr(e, "type", None) or name
            outcome = {{
                "status": "budget" if name in {_BUDGET_ERRORS!r} else "error",
                "error_type": name,
                "error": str(e),
                "traceback": "".join(_cuga_r_tb.format_exception(type(e), e, e.__traceback__)),
                "stdout": _buf.getvalue(),
            }}
            if outcome["status"] == "budget":
                outcome["locals"] = _cuga_r_keep(_cuga_r_frame_locals(e))
            _cuga_r_emit(outcome)
            return
        _cuga_r_sys.stdout = _real
        if kind == "exit":
            exc, frame_locals = value
            reason = str(exc) if str(exc) and not str(exc).isdigit() else ""
            _cuga_r_emit({{"status": "exit", "reason": reason, "locals": _cuga_r_keep(frame_locals), "stdout": _buf.getvalue()}})
            return
        _cuga_r_emit({{"status": "ok", "locals": _cuga_r_keep(value), "stdout": _buf.getvalue()}})
    finally:
        _cuga_r_sys.stdout = _real
        for _n, _v in _saved_exit.items():
            if _v is None:
                globals().pop(_n, None)
            else:
                globals()[_n] = _v

await _cuga_r_run()
'''


def parse_outcome(raw: str) -> Optional[dict[str, Any]]:
    """The framed outcome line from the kernel's output, or ``None`` when absent."""
    for line in reversed(raw.splitlines()):
        if line.startswith(RESULT_MARKER):
            try:
                return json.loads(base64.b64decode(line[len(RESULT_MARKER) :]).decode("utf-8"))
            except Exception:
                return None
    return None


def strip_marker(raw: str) -> str:
    return "\n".join(line for line in raw.splitlines() if not line.startswith(RESULT_MARKER)).rstrip("\n")


def render(outcome: dict[str, Any], *, timeout: int) -> str:
    """The text the agent sees, worded as the local executor words it."""
    status = outcome.get("status")
    stdout = outcome.get("stdout") or ""
    kept = sorted((outcome.get("locals") or {}).keys())

    if status == "ok":
        return stdout or "<code ran, no output printed to stdout>"

    if status == "exit":
        reason = outcome.get("reason") or ""
        note = "Block ended early: the code called exit()/quit() or raised SystemExit"
        note += f" ({reason}).\n" if reason else ".\n"
        note += (
            "This ended the block only — variables defined before it were kept and "
            "are available in the next block. Nothing after the exit ran.\n"
        )
        return f"{note}{stdout}" if stdout.strip() else f"{note}(no output printed)"

    if status == "timeout":
        kept_note = (
            f"kept variables from this block: {', '.join(kept)} (available in the next block).\n"
            if kept
            else "no variables from this block were saved.\n"
        )
        text = (
            f"Error during execution: Execution timed out after {timeout} seconds.\n"
            f"{kept_note}"
            "Do NOT rerun the same code — it will time out again. Restructure instead: "
            "use a bulk/aggregate tool (find_tools), or process a small batch of items "
            "per code block and store partial progress in a variable.\n"
        )
        text += (
            f"Partial stdout before the timeout:\n{stdout}"
            if stdout.strip()
            else "(no stdout was printed before the timeout)"
        )
        return text

    # budget / error: worded as LocalExecutor.format_error words them.
    text = f"Error during execution: {outcome.get('error_type')}({outcome.get('error')!r})"
    if outcome.get("traceback"):
        text += f"\n{outcome['traceback']}"
    if stdout.strip():
        text = f"Output before error:\n{stdout.rstrip()}\n\n{text}"
    return text


__all__ = ["RESULT_MARKER", "parse_outcome", "render", "runner_code", "strip_marker", "variables_code"]
