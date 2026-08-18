"""Generic HTTP error responses that carry no exception detail.

Handlers used to hand the caller their own exception text — ``str(e)``, and in
one case ``traceback.format_exc()`` — which leaks absolute paths, config values,
and library internals to anyone who can provoke a 500 (CodeQL
``py/stack-trace-exposure``).

The rule these helpers enforce is narrow and worth stating plainly:

    **Nothing derived from the exception ever reaches the response.**

Not ``str(exc)``, not ``repr(exc)``, not ``exc.args``, not a message built from
any of them. ``message`` is always a literal supplied by the caller. That is what
makes the taint flow stop here rather than depending on CodeQL configuration —
``py/stack-trace-exposure`` has no model-driven sanitizer hook (its
``Sanitizer`` class is abstract with no models-as-data subclass), so a
``barrierModel`` entry claiming these functions are safe would silently do
nothing. The only thing that works is not leaking.

The detail is not lost, just moved: the full traceback goes to the server log
under a short random reference, and the same reference goes to the caller. A
user reporting "I got ref a3f2c1d4e5b6" lets an operator find the exact
traceback without the traceback ever crossing the wire.

Accepts loguru and stdlib loggers alike — both expose ``.exception()``, which
reads the active exception, so call these from inside an ``except`` block.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from loguru import logger as _default_logger

#: Default caller-facing text. Deliberately says nothing about what went wrong.
GENERIC_MESSAGE = "Internal server error"


def log_error_ref(
    exc: BaseException,
    *,
    log: Any = None,
    context: str = "Request failed",
) -> str:
    """Log ``exc`` with a full traceback and return only a short reference.

    The return value is a random hex string — it is not derived from ``exc``, so
    it is safe to put in a response body.
    """
    ref = uuid.uuid4().hex[:12]
    # The exception class name is safe to log and makes triage faster; the
    # traceback itself comes from .exception() reading the active exception.
    (log if log is not None else _default_logger).exception(f"[{ref}] {context} ({type(exc).__name__})")
    return ref


def safe_error_payload(
    exc: BaseException,
    *,
    message: str = GENERIC_MESSAGE,
    log: Any = None,
    context: str | None = None,
) -> dict[str, Any]:
    """Build an error body for streams and custom envelopes.

    ``message`` must be a literal. Never pass anything derived from ``exc``.
    """
    ref = log_error_ref(exc, log=log, context=context or message)
    return {"status": "error", "message": message, "ref": ref}


def safe_error_response(
    exc: BaseException,
    *,
    status: int = 500,
    message: str = GENERIC_MESSAGE,
    log: Any = None,
    context: str | None = None,
) -> JSONResponse:
    """``safe_error_payload`` wrapped in a ``JSONResponse``.

    ``message`` must be a literal. Never pass anything derived from ``exc``.
    """
    return JSONResponse(
        safe_error_payload(exc, message=message, log=log, context=context),
        status_code=status,
    )


def safe_http_exception(
    exc: BaseException,
    *,
    status: int = 500,
    message: str = GENERIC_MESSAGE,
    log: Any = None,
    context: str | None = None,
) -> HTTPException:
    """An ``HTTPException`` whose ``detail`` names only the message and reference.

    For handlers that raise rather than return. ``message`` must be a literal.
    """
    ref = log_error_ref(exc, log=log, context=context or message)
    return HTTPException(status_code=status, detail=f"{message} (ref {ref})")
