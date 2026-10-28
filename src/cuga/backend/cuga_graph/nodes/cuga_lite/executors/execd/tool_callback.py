"""Tool calls from code running in the execd sandbox, executed back in CUGA.

The agent's code runs in the sandbox, but its tools do not and cannot: a tool is
a callable in this process — a registry or MCP call, an SDK function, the turn's
todo list, a spawned subagent's future, ToolGuard and the call budget wrapped
around all of them. So each tool in the sandbox is a stub that POSTs its call
to :func:`handle_call` here, which runs the very callable the local executor
would have run and returns its result in the response.

Nothing is reconstructed. Before a block is sent its namespace is registered
under a fresh block id; the stub names a callable, and the call is looked up in
that namespace and nowhere else. When the block ends the entry is dropped, so a
call that outlives its block — a task the code left running in the kernel —
is refused rather than run against a finished turn.

Each block carries an HMAC token over its id, signed with a key that exists only
in this process. The sandbox is shared by every thread on the pod, so code in
one thread must not be able to reach another thread's tools by guessing an id.
CUGA runs as one pod, so the process that sent the block is the process that
answers the call; a second replica would need the callback to name its pod.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import dataclasses
import datetime as _dt
import decimal
import enum
import hashlib
import hmac
import inspect
import json
import secrets
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from loguru import logger

from cuga.config import settings

_signing_key = secrets.token_bytes(32)

# Results larger than this are refused with a clear error rather than shipped;
# a tool returning tens of MB into generated code is a bug, not a payload.
MAX_RESULT_BYTES = 8 * 1024 * 1024


@dataclasses.dataclass
class _Block:
    callables: dict[str, Any]
    thread_id: Optional[str]
    expires: float
    # The block's context, captured when it was sent. The HTTP handler runs in
    # another task, and the tool must see the block's call budget, tracker,
    # knowledge credentials and spawn depth — all contextvars. The counters in
    # them are mutable boxes, so charges made here are the block's charges.
    context: contextvars.Context
    # A control signal a tool raised (a BaseException such as the supervisor's
    # ACPPermissionPause). Locally it unwinds the block to the graph; here the
    # block is in another process, so it is kept and re-raised once the block
    # ends — see ``raise_pending_signal``.
    signal: Optional[BaseException] = None


class BlockHandle:
    """What the executor holds for a registered block."""

    def __init__(self, block_id: str, token: str, block: "_Block"):
        self.block_id = block_id
        self.token = token
        self._block = block

    def raise_pending_signal(self) -> None:
        """Re-raise in CUGA the control signal a tool raised during the block, if any."""
        if self._block.signal is not None:
            raise self._block.signal


_blocks: dict[str, _Block] = {}


def has_blocks() -> bool:
    """Whether any code block is running in the sandbox, so a call can be answered at all."""
    return bool(_blocks)


def callback_url() -> str:
    """Base URL the sandbox uses to reach this server: ``advanced_features.sandbox_callback_url``.

    No fallback. ``server_ports.function_call_host`` names the tool registry
    (port 8001), not this server, so a callback sent there never reaches the
    route that answers it. Empty when not configured.
    """
    return str(getattr(settings.advanced_features, "sandbox_callback_url", "") or "").strip().rstrip("/")


def _sign(block_id: str) -> str:
    return hmac.new(_signing_key, block_id.encode("ascii"), hashlib.sha256).hexdigest()


def flatten_callables(namespace: dict[str, Any]) -> dict[str, Any]:
    """Every callable the block can reach, keyed by the name the stub calls it by.

    Top-level callables keep their name. A dict of callables (the function-calling
    ``_fc_tools`` lookup) is entered as ``"<dict>[<key>]"`` so the stub for that
    dict resolves each key the same way.
    """
    found: dict[str, Any] = {}
    for name, value in namespace.items():
        if callable(value) and not inspect.isclass(value):
            found[name] = value
        elif isinstance(value, dict) and value and all(callable(v) for v in value.values()):
            for key, fn in value.items():
                found[f"{name}[{key}]"] = fn
    return found


@asynccontextmanager
async def registered_block(
    namespace: dict[str, Any], thread_id: Optional[str], timeout: float
) -> AsyncIterator[BlockHandle]:
    """Make the block's callables answerable for as long as the block runs.

    Yields the handle carrying ``block_id`` and ``token`` for the stubs. The
    entry also expires on its own, past the block's wall-clock limit, in case
    the caller is cancelled in a way that skips ``finally``.
    """
    block_id = uuid.uuid4().hex
    block = _Block(
        callables=flatten_callables(namespace),
        thread_id=thread_id,
        expires=time.monotonic() + timeout + 60.0,
        context=contextvars.copy_context(),
    )
    _blocks[block_id] = block
    try:
        yield BlockHandle(block_id, _sign(block_id), block)
    finally:
        _blocks.pop(block_id, None)


class CallRefused(Exception):
    """The call cannot be run: bad token, finished block, unknown callable."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


_HEX = frozenset("0123456789abcdef")


def _is_hex(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and set(value) <= _HEX


def _block_for(block_id: str, token: str) -> _Block:
    # Ids are uuid4().hex and tokens a SHA-256 hexdigest; anything else is not ours.
    if not _is_hex(block_id, 32) or not _is_hex(token, 64):
        raise CallRefused(400, "block and token are required")
    if not hmac.compare_digest(_sign(block_id), token):
        raise CallRefused(403, "invalid block token")
    block = _blocks.get(block_id)
    if block is None or block.expires < time.monotonic():
        _blocks.pop(block_id, None)
        raise CallRefused(410, "the code block this call belongs to has finished")
    if block.signal is not None:
        # The block is already unwinding toward a pause; nothing after it runs.
        raise CallRefused(409, "the code block is stopping")
    return block


def to_jsonable(value: Any) -> Any:
    """Convert a tool result into something JSON can carry to the sandbox.

    Values JSON has a form for are kept; datetimes become ISO strings, Decimal a
    number, bytes base64, sets lists, Pydantic models and dataclasses their
    fields. Anything else arrives as its ``str()`` — the same text the agent
    would see if it printed the value locally.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, enum.Enum):
        return to_jsonable(value.value)
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return to_jsonable(value.model_dump())
        except Exception:
            pass
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(dataclasses.asdict(value))
    return str(value)


async def handle_call(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Run one call from the sandbox. Returns ``(http_status, response_body)``.

    A tool that raises is a result, not a transport failure: it comes back as
    ``{"ok": false, "error": {...}}`` with status 200 so the stub can re-raise
    it in the agent's code, where ``try/except`` handles it as it would locally.
    """
    try:
        block = _block_for(body.get("block"), body.get("token"))
    except CallRefused as exc:
        logger.warning(f"[execd:callback] refused: {exc}")
        return exc.status, {"ok": False, "error": {"type": "CallRefused", "message": str(exc)}}

    name = body.get("name")
    fn = block.callables.get(name) if isinstance(name, str) else None
    if fn is None:
        return 404, {
            "ok": False,
            "error": {"type": "NameError", "message": f"no tool named {name!r} in this block"},
        }

    args = body.get("args") or []
    kwargs = body.get("kwargs") or {}
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        return 400, {
            "ok": False,
            "error": {"type": "TypeError", "message": "args must be a list, kwargs an object"},
        }

    async def _invoke() -> Any:
        result = fn(*args, **kwargs)
        while inspect.isawaitable(result):
            result = await result
        return result

    t0 = time.monotonic()
    try:
        if sys.version_info >= (3, 11):
            result = await asyncio.create_task(_invoke(), context=block.context)
        else:  # pragma: no cover - 3.10 has no context= on create_task
            result = await block.context.run(asyncio.ensure_future, _invoke())
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.info(
            f"[execd:callback] {name} raised {type(exc).__name__} thread={block.thread_id} "
            f"duration_ms={int((time.monotonic() - t0) * 1000)}"
        )
        return 200, {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
    except BaseException as exc:
        # Not an error the agent's code may catch: a signal to the graph. The
        # sandbox gets a stop marker that ends the block; the signal itself is
        # re-raised in CUGA after the block, as it would have unwound locally.
        block.signal = exc
        logger.info(
            f"[execd:callback] {name} raised control signal {type(exc).__name__} thread={block.thread_id}"
        )
        return 200, {"ok": False, "stop": True, "error": {"type": type(exc).__name__, "message": str(exc)}}

    payload = {"ok": True, "result": to_jsonable(result)}
    encoded_len = len(json.dumps(payload, ensure_ascii=False))
    if encoded_len > MAX_RESULT_BYTES:
        return 200, {
            "ok": False,
            "error": {
                "type": "ValueError",
                "message": f"result of {name} is {encoded_len} bytes, over the {MAX_RESULT_BYTES}-byte limit",
            },
        }
    logger.info(
        f"[execd:callback] {name} ok thread={block.thread_id} "
        f"duration_ms={int((time.monotonic() - t0) * 1000)} result_bytes={encoded_len}"
    )
    return 200, payload


def stub_code(namespace: dict[str, Any], *, url: str, block_id: str, token: str, timeout: float) -> str:
    """Python for the sandbox defining a stub for every callable in the namespace.

    Async tools get async stubs, plain functions plain ones, so ``await tool()``
    and ``helper(x)`` read the same as locally. A dict of callables becomes a
    dict of stubs. The HTTP client is the standard library's: nothing has to be
    installed in the sandbox image.
    """
    lines = [
        "import json as _cuga_cb_json, urllib.request as _cuga_cb_req, urllib.error as _cuga_cb_err",
        "import asyncio as _cuga_cb_aio",
        f"_CUGA_CB_URL = {url + '/sandbox/tools/call'!r}",
        f"_CUGA_CB_BLOCK = {block_id!r}",
        f"_CUGA_CB_TOKEN = {token!r}",
        f"_CUGA_CB_TIMEOUT = {float(timeout)!r}",
        "",
        "class _CugaStop(BaseException):",
        "    \"\"\"CUGA paused the turn; not catchable by `except Exception` in agent code.\"\"\"",
        "",
        "class _CugaToolError(Exception):",
        "    \"\"\"A tool raised in CUGA; ``type`` names the original exception class.\"\"\"",
        "    def __init__(self, type_, message):",
        "        super().__init__(message)",
        "        self.type = type_",
        "",
        "def _cuga_cb_call(name, args, kwargs):",
        "    body = _cuga_cb_json.dumps({'block': _CUGA_CB_BLOCK, 'token': _CUGA_CB_TOKEN,",
        "                                'name': name, 'args': list(args), 'kwargs': kwargs}, default=str).encode()",
        "    req = _cuga_cb_req.Request(_CUGA_CB_URL, data=body, method='POST',",
        "                               headers={'Content-Type': 'application/json'})",
        "    try:",
        "        with _cuga_cb_req.urlopen(req, timeout=_CUGA_CB_TIMEOUT) as resp:",
        "            reply = _cuga_cb_json.loads(resp.read().decode())",
        "    except _cuga_cb_err.HTTPError as e:",
        "        try:",
        "            reply = _cuga_cb_json.loads(e.read().decode())",
        "        except Exception:",
        "            raise _CugaToolError('CallbackError', f'{name}: HTTP {e.code} from CUGA') from None",
        "    except _cuga_cb_err.URLError as e:",
        "        raise _CugaToolError('CallbackError', f'{name}: CUGA unreachable at {_CUGA_CB_URL} ({e.reason})') from None",
        "    if reply.get('ok'):",
        "        return reply.get('result')",
        "    if reply.get('stop'):",
        "        raise _CugaStop((reply.get('error') or {}).get('message', 'stopped by CUGA'))",
        "    err = reply.get('error') or {}",
        "    raise _CugaToolError(err.get('type', 'Error'), err.get('message', 'tool call failed'))",
        "",
        "async def _cuga_cb_acall(name, args, kwargs):",
        "    return await _cuga_cb_aio.get_running_loop().run_in_executor(None, _cuga_cb_call, name, args, kwargs)",
        "",
    ]

    counter = 0
    for name, value in namespace.items():
        if not name.isidentifier():
            continue
        if inspect.ismodule(value):
            # e.g. ``_internal_re`` in skills mode: the module, not a call into CUGA.
            lines.append(f"import {value.__name__} as {name}")
        elif callable(value) and not inspect.isclass(value):
            lines.append(_stub_def(name, name, value))
        elif isinstance(value, dict) and value and all(callable(v) for v in value.values()):
            entries = []
            for key, fn in value.items():
                counter += 1
                stub_name = f"_cuga_cb_entry_{counter}"
                lines.append(_stub_def(stub_name, f"{name}[{key}]", fn))
                entries.append(f"{key!r}: {stub_name}")
            lines.append(f"{name} = {{{', '.join(entries)}}}")
    return "\n".join(lines) + "\n"


def _stub_def(py_name: str, call_name: str, fn: Any) -> str:
    # A repr literal, so quotes and backslashes in a tool's description cannot break the stub.
    doc = repr((inspect.getdoc(fn) or "").split("\n", 1)[0])
    if inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(getattr(fn, "__wrapped__", None)):
        return (
            f"async def {py_name}(*args, **kwargs):\n"
            f"    {doc}\n"
            f"    return await _cuga_cb_acall({call_name!r}, args, kwargs)\n"
        )
    return (
        f"def {py_name}(*args, **kwargs):\n    {doc}\n    return _cuga_cb_call({call_name!r}, args, kwargs)\n"
    )


__all__ = [
    "CallRefused",
    "MAX_RESULT_BYTES",
    "callback_url",
    "flatten_callables",
    "handle_call",
    "has_blocks",
    "registered_block",
    "stub_code",
    "to_jsonable",
]
