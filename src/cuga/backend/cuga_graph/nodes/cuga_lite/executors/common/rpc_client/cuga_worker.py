"""``execute_python`` worker — runs one code block in its own process.

Started by ``SubprocessExecutor`` (``executors/subprocess``) as
``python -I cuga_worker.py``, usually under an OS sandbox (Seatbelt / bubblewrap)
with no network. It reads one job from stdin::

    {"code": "<wrapped code defining _async_main>", "variables": {...},
     "tools": ["name", ...], "timeout": 90}

Tools are async stubs that call the host over the tool channel (``cuga_tools``), so
every call is budgeted and tracked by the host. Printed output streams to the host
in chunks (it survives a hard kill); the block ends with one ``done`` message
carrying the variables the code left behind, encoded as plain JSON.

Same semantics as the in-process ``LocalExecutor``: variables computed before an
error, an ``exit()`` or a timeout are kept; a timeout reports what was printed.

Stdlib only — the code itself may import anything installed.
"""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import datetime as _dt
import io
import json
import os
import sys
import threading
import time
import _thread
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cuga_tb  # noqa: E402
import cuga_tools as rpc  # noqa: E402

# The channel is the worker's, not the code's: code calls tools as ``await tool(...)``,
# exactly as in-process, where ``cuga_tools`` is not importable either.
del sys.path[0]
for _name in ("cuga_tools", "cuga_tb"):
    sys.modules.pop(_name, None)

_CHUNK_BYTES = 8192
_CHUNK_SECONDS = 0.5


class _Output(io.TextIOBase):
    """Collects prints; ships chunks to the host so a killed block still shows its output.

    A daemon thread ships whatever is pending every ``_CHUNK_SECONDS`` — a block stuck
    in a CPU-bound loop never calls ``write`` again, but its earlier prints still reach
    the host before the kill.
    """

    def __init__(self) -> None:
        self._pending: List[str] = []
        self._size = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        if rpc.available():
            threading.Thread(target=self._flush_loop, name="cuga-stdout", daemon=True).start()

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if text:
            with self._lock:
                self._pending.append(text)
                self._size += len(text)
                big = self._size >= _CHUNK_BYTES
            if big:
                self.ship()
        return len(text)

    def _flush_loop(self) -> None:
        while not self._stop.wait(_CHUNK_SECONDS):
            self.ship()

    def ship(self) -> None:
        with self._lock:
            data, self._pending, self._size = "".join(self._pending), [], 0
        if data and rpc.available():
            rpc.client().send({"op": "stdout", "data": data})

    def close_and_take(self) -> str:
        """Stop shipping; return what was never shipped (it goes in the ``done`` message)."""
        self._stop.set()
        with self._lock:
            data, self._pending, self._size = "".join(self._pending), [], 0
        return data


class _LazyModule:
    """``pd`` / ``pandas`` without paying pandas' import on every block that never uses it."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._module: Any = None

    def __getattr__(self, attr: str) -> Any:
        if self._module is None:
            import importlib

            self._module = importlib.import_module(self._name)
        return getattr(self._module, attr)


def _preloaded() -> Dict[str, Any]:
    """Names the in-process executor predefines, so code that relies on them runs the same here."""
    names: Dict[str, Any] = {"asyncio": asyncio, "json": json, "os": os, "sys": sys}
    pandas = _LazyModule("pandas")
    names["pd"] = names["pandas"] = pandas
    return names


def _stub(name: str) -> Any:
    async def tool(*args: Any, **kwargs: Any) -> Any:
        return await rpc.acall(name, *args, **kwargs)

    tool.__name__ = name
    tool.__qualname__ = name
    return tool


def _plain(value: Any, depth: int = 0) -> bool:
    """Data the host can store as a variable (the same rule as ``VariableUtils.is_serializable``)."""
    if depth > 100:
        return False
    if value is None or isinstance(value, (str, bool, int, float, _dt.date, _dt.time, _dt.timedelta)):
        return True
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(_plain(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, (str, int, float, bool)) and _plain(v, depth + 1) for k, v in value.items())
    return False


def _frame_locals(error: BaseException) -> Dict[str, Any]:
    frame = None
    tb = error.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_name == "_async_main":
            frame = tb.tb_frame
        tb = tb.tb_next
    return dict(frame.f_locals) if frame is not None else {}


def _coro_locals(coro: Any) -> Dict[str, Any]:
    seen = set()
    while coro is not None and id(coro) not in seen:
        seen.add(id(coro))
        frame = getattr(coro, "cr_frame", None)
        while frame is not None:
            if frame.f_code.co_name == "_async_main":
                return dict(frame.f_locals)
            frame = frame.f_back
        nxt = getattr(coro, "cr_await", None)
        coro = nxt if asyncio.iscoroutine(nxt) else None
    return {}


def _describe(error: BaseException) -> Dict[str, Any]:
    return {
        "type": type(error).__name__,
        "repr": repr(error),
        "message": str(error),
        "name": getattr(error, "name", None) if isinstance(error, NameError) else None,
        "text": cuga_tb.format_code_error(error),
    }


async def _run(main: Any, timeout: float) -> Tuple[str, Dict[str, Any], Optional[Dict[str, Any]]]:
    async def guarded() -> Tuple[str, Dict[str, Any], Optional[Dict[str, Any]]]:
        try:
            return "ok", dict(await main() or {}), None
        except KeyboardInterrupt as exc:  # the watchdog: the block never yielded
            return "timeout", _frame_locals(exc), None
        except SystemExit as exc:
            reason = str(exc) if str(exc) and not str(exc).isdigit() else ""
            return "exit", _frame_locals(exc), {"message": reason}
        except Exception as exc:
            return "error", _frame_locals(exc), _describe(exc)

    task = asyncio.ensure_future(guarded())
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        values = _coro_locals(task.get_coro())
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        return "timeout", values, None
    return task.result()


def _run_in_loop(coro: Any) -> Any:
    """``asyncio.run`` without its SIGINT handler: that handler turns the watchdog's
    interrupt into a task cancel, which a block that never yields never sees."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        with contextlib.suppress(BaseException):
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
        asyncio.set_event_loop(None)
        loop.close()


def main() -> int:
    job = json.loads(sys.stdin.buffer.read() or b"{}")
    code: str = job.get("code") or ""
    timeout = float(job.get("timeout") or 30)
    tool_names = list(job.get("tools") or [])

    namespace: Dict[str, Any] = {"__name__": "__cuga_code__", "__builtins__": builtins, **_preloaded()}
    for key, value in (job.get("variables") or {}).items():
        namespace[key] = rpc.decode(value)
    for name in tool_names:
        namespace[name] = _stub(name)
    given = set(namespace)

    output = _Output()
    status, values, error = "error", {}, None
    filename = cuga_tb.register_source(code, cuga_tb.CODE_FILENAME)
    started = time.monotonic()
    finished = threading.Event()

    def watchdog() -> None:
        # ``asyncio.wait`` can only stop a block at an ``await``. A block that never
        # yields (a CPU-bound loop) gets a KeyboardInterrupt in the main thread instead,
        # which still lets it hand back the variables it computed.
        if not finished.wait(timeout + 1.0):
            _thread.interrupt_main()

    threading.Thread(target=watchdog, name="cuga-watchdog", daemon=True).start()
    real_stdout = sys.stdout
    sys.stdout = output
    try:
        exec(compile(code, filename, "exec"), namespace)
        status, values, error = _run_in_loop(_run(namespace["_async_main"], timeout))
    except KeyboardInterrupt:
        status, error = "timeout", None
    except BaseException as exc:  # compile error, or the wrapper itself failed
        status, error = "error", _describe(exc)
    finally:
        finished.set()
        sys.stdout = real_stdout

    tail = output.close_and_take()
    variables: Dict[str, Any] = {}
    skipped: List[str] = []
    for key, value in values.items():
        if key.startswith("_") or (key in given and namespace.get(key) is value and key in tool_names):
            continue
        if callable(value) or isinstance(value, type(sys)):
            continue
        if _plain(value):
            variables[key] = rpc.encode(value)
        else:
            skipped.append(key)
    message = {
        "op": "done",
        "status": status,
        "stdout": tail,
        "variables": variables,
        "skipped": skipped,
        "error": error,
        "seconds": round(time.monotonic() - started, 3),
    }
    if rpc.available():
        rpc.client().send(message)
    else:  # run by hand: print the result instead
        print(json.dumps(message, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
