"""Call the agent's tools from a child process — stdlib only.

A child that the host starts with a tool channel (``tool_rpc.ToolChannel``) gets two
inherited pipe file descriptors, named by ``CUGA_TOOL_RPC_OUT`` (requests, child →
host) and ``CUGA_TOOL_RPC_IN`` (replies, host → child). Each message is one line of
JSON. The host runs every call in the task that started the child, so tool-call
budgets, call tracking and the pre-write check apply exactly as they do to a native
call.

Used two ways:

- by the ``execute_python`` subprocess worker (``cuga_worker.py``), whose tool
  functions are thin async stubs over :func:`acall`;
- by scripts that ``run_command`` runs::

      from cuga_tools import call
      songs = call("spotify_show_song_library", access_token=token, page_index=0)

  or from bash: ``cuga-tool spotify_show_song_library '{"page_index": 0}'``.

This file is also imported by the host for :func:`encode` / :func:`decode`, so the
two sides share one wire format. Keep it free of third-party imports.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import sys
import threading
from typing import Any, Dict, List, Optional

ENV_IN = "CUGA_TOOL_RPC_IN"
ENV_OUT = "CUGA_TOOL_RPC_OUT"

_TAG = "__cuga__"


class ToolCallError(Exception):
    """A tool call failed; the message says why."""


class ToolCallBudgetExceeded(RuntimeError):
    """The host refused the call: a tool-call budget is spent."""


# Tracebacks name these like built-ins ("ToolCallError: ..."), not "cuga_tools.ToolCallError".
ToolCallError.__module__ = ToolCallBudgetExceeded.__module__ = "builtins"


class FoundTools(str):
    """``tool_search`` result: prints as the signatures, ``.names`` lists the tools found."""

    names: List[str]

    def __new__(cls, text: str, names: Optional[List[str]] = None) -> "FoundTools":
        obj = super().__new__(cls, text)
        obj.names = list(names or [])
        return obj


# ── wire format ──────────────────────────────────────────────────────────────


def encode(value: Any, _depth: int = 0) -> Any:
    """``value`` as plain JSON data; types JSON lacks travel as tagged dicts.

    Anything else is sent as its ``str`` — a tool result is data, never code.
    """
    if _depth > 200:
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, FoundTools):
        return {_TAG: "found_tools", "text": str(value), "names": list(value.names)}
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return {str(k): encode(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [encode(v, _depth + 1) for v in value]
    if isinstance(value, tuple):
        return {_TAG: "tuple", "v": [encode(v, _depth + 1) for v in value]}
    if isinstance(value, (set, frozenset)):
        kind = "frozenset" if isinstance(value, frozenset) else "set"
        try:
            items = sorted(value)
        except TypeError:
            items = list(value)
        return {_TAG: kind, "v": [encode(v, _depth + 1) for v in items]}
    if isinstance(value, _dt.datetime):
        return {_TAG: "datetime", "v": value.isoformat()}
    if isinstance(value, _dt.date):
        return {_TAG: "date", "v": value.isoformat()}
    if isinstance(value, _dt.time):
        return {_TAG: "time", "v": value.isoformat()}
    if isinstance(value, _dt.timedelta):
        return {_TAG: "timedelta", "v": value.total_seconds()}
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    # Tool-search results from the host (``ToolSearchResult``) and anything with a
    # ``model_dump`` (pydantic) are reduced to data before falling back to ``str``.
    names = getattr(value, "discovered", None)
    text = getattr(value, "text", None)
    if isinstance(text, str) and isinstance(names, list):
        return {_TAG: "found_tools", "text": text, "names": [str(n) for n in names]}
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return encode(dump(), _depth + 1)
        except Exception:
            pass
    return str(value)


def decode(value: Any) -> Any:
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    tag = value.get(_TAG)
    if tag is None:
        return {k: decode(v) for k, v in value.items()}
    v = value.get("v")
    if tag == "tuple":
        return tuple(decode(x) for x in v)
    if tag == "set":
        return {decode(x) for x in v}
    if tag == "frozenset":
        return frozenset(decode(x) for x in v)
    if tag == "datetime":
        return _dt.datetime.fromisoformat(v)
    if tag == "date":
        return _dt.date.fromisoformat(v)
    if tag == "time":
        return _dt.time.fromisoformat(v)
    if tag == "timedelta":
        return _dt.timedelta(seconds=v)
    if tag == "found_tools":
        return FoundTools(value.get("text") or "", value.get("names") or [])
    return {k: decode(x) for k, x in value.items()}


def dumps(message: Dict[str, Any]) -> bytes:
    return (json.dumps(message, ensure_ascii=False, default=str) + "\n").encode("utf-8")


# ── client ───────────────────────────────────────────────────────────────────


class _Client:
    """One connection per process. Thread-safe: concurrent calls are matched by id."""

    def __init__(self, fd_out: int, fd_in: int) -> None:
        self._out = os.fdopen(fd_out, "wb", buffering=0, closefd=False)
        self._in = os.fdopen(fd_in, "rb", closefd=False)
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._next_id = 0
        self._waiting: Dict[int, List[Any]] = {}  # id -> [event, reply]
        self._reader: Optional[threading.Thread] = None
        self._closed: Optional[str] = None

    def _ensure_reader(self) -> None:
        with self._state_lock:
            if self._reader is None:
                self._reader = threading.Thread(target=self._read_loop, name="cuga-tool-rpc", daemon=True)
                self._reader.start()

    def _read_loop(self) -> None:
        while True:
            line = self._in.readline()
            if not line:
                break
            try:
                reply = json.loads(line)
            except ValueError:
                continue
            with self._state_lock:
                slot = self._waiting.pop(reply.get("id"), None)
            if slot is not None:
                slot[1] = reply
                slot[0].set()
        with self._state_lock:
            self._closed = "the host closed the tool channel"
            waiting, self._waiting = self._waiting, {}
        for slot in waiting.values():
            slot[0].set()

    def send(self, message: Dict[str, Any]) -> None:
        data = dumps(message)
        with self._write_lock:
            self._out.write(data)

    def request(self, message: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_reader()
        event = threading.Event()
        with self._state_lock:
            if self._closed:
                raise ToolCallError(self._closed)
            self._next_id += 1
            call_id = self._next_id
            slot: List[Any] = [event, None]
            self._waiting[call_id] = slot
        self.send({**message, "id": call_id})
        event.wait()
        if slot[1] is None:
            raise ToolCallError(self._closed or "no reply from the host")
        return slot[1]


_client: Optional[_Client] = None
_client_lock = threading.Lock()


def available() -> bool:
    """True when this process was started with a tool channel."""
    return bool(os.environ.get(ENV_OUT)) and bool(os.environ.get(ENV_IN))


def client() -> _Client:
    global _client
    with _client_lock:
        if _client is None:
            if not available():
                raise ToolCallError(
                    "No tool channel: tools can only be called from a process the agent started "
                    "(run_command or execute_python)."
                )
            _client = _Client(int(os.environ[ENV_OUT]), int(os.environ[ENV_IN]))
        return _client


def _raise_for(reply: Dict[str, Any]) -> Any:
    if reply.get("ok"):
        return decode(reply.get("result"))
    message = str(reply.get("error") or "tool call failed")
    if reply.get("kind") == "ToolCallBudgetExceeded":
        raise ToolCallBudgetExceeded(message)
    raise ToolCallError(message)


def call(name: str, *args: Any, **kwargs: Any) -> Any:
    """Call tool ``name`` and return its result (blocking)."""
    reply = client().request(
        {"op": "call", "tool": name, "args": encode(list(args)), "kwargs": encode(kwargs)}
    )
    return _raise_for(reply)


async def acall(name: str, *args: Any, **kwargs: Any) -> Any:
    """Call tool ``name`` from async code; concurrent calls are allowed."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: call(name, *args, **kwargs))


def list_tools() -> List[str]:
    """Names of the tools this process can call."""
    reply = client().request({"op": "list"})
    return list(_raise_for(reply) or [])


def search(query: str, app_name: Optional[str] = None, max_results: Optional[int] = None) -> FoundTools:
    """Find tools by keywords (or ``select:<name>``); prints as their signatures."""
    kwargs: Dict[str, Any] = {"query": query}
    if app_name:
        kwargs["app_name"] = app_name
    if max_results:
        kwargs["max_results"] = max_results
    result = call("tool_search", **kwargs)
    return result if isinstance(result, FoundTools) else FoundTools(str(result))


def __getattr__(name: str) -> Any:
    """``cuga_tools.<tool_name>(**kwargs)`` calls that tool."""
    if name.startswith("_"):
        raise AttributeError(name)

    def tool(*args: Any, **kwargs: Any) -> Any:
        return call(name, *args, **kwargs)

    tool.__name__ = name
    return tool


# ── command line: cuga-tool NAME ['{"arg": value}'] | --list | --search QUERY ──


def _main(argv: List[str]) -> int:
    usage = (
        "usage: cuga-tool TOOL_NAME ['{\"arg\": value, ...}']\n"
        "       cuga-tool --list\n"
        "       cuga-tool --search 'keywords'"
    )
    if not argv or argv[0] in ("-h", "--help"):
        print(usage)
        return 0 if argv else 2
    try:
        if argv[0] == "--list":
            print("\n".join(list_tools()))
            return 0
        if argv[0] == "--search":
            print(search(" ".join(argv[1:])))
            return 0
        name = argv[0]
        raw = argv[1] if len(argv) > 1 else (sys.stdin.read() if not sys.stdin.isatty() else "")
        kwargs = json.loads(raw) if raw.strip() else {}
        if not isinstance(kwargs, dict):
            print("cuga-tool: arguments must be a JSON object", file=sys.stderr)
            return 2
        result = call(name, **kwargs)
    except (ToolCallError, ToolCallBudgetExceeded) as exc:
        print(f"cuga-tool: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"cuga-tool: arguments are not valid JSON: {exc}", file=sys.stderr)
        return 2
    print(result if isinstance(result, str) else json.dumps(encode(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
