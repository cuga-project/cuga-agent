"""Error responses for HTTP handlers that do not include exception details.

Some handlers used to put the text of the exception into the response the caller
receives. One of them included the full stack trace. That text can contain file
paths on the server, configuration values, and details of the libraries in use,
and anyone able to make the request fail could read it. CodeQL reports this as
``py/stack-trace-exposure``.

The helpers here follow one rule:

    Nothing taken from the exception is ever put into the response.

That means no ``str(exc)``, no ``repr(exc)``, no ``exc.args``, and no message
built from any of them. The ``message`` argument is always a fixed piece of text
written by the caller.

This is a deliberate choice rather than a stylistic one. CodeQL offers a way to
declare a function safe in a configuration file, but that mechanism does not
apply to this particular rule, so declaring these helpers safe would have no
effect. The only approach that works is to genuinely not include the details.
The comment block in ``.github/codeql/codeql-config.yml`` explains which rules
the configuration file does affect.

The details are not lost. Each helper writes the full stack trace to the server
log alongside a short random reference code, and returns that same code to the
caller. If someone reports "I received reference a3f2c1d4e5b6", an operator can
search the log for that code and find the matching stack trace, without the
stack trace ever being sent over the network.

The helpers work with both loguru loggers and the ones from Python's standard
``logging`` module.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from loguru import logger as _default_logger

#: The text sent to the caller when none is supplied. It says nothing about
#: the cause on purpose.
GENERIC_MESSAGE = "Internal server error"


def log_error_ref(
    exc: BaseException,
    *,
    log: Any = None,
    context: str = "Request failed",
) -> str:
    """Write ``exc`` and its stack trace to the log, and return a reference code.

    The returned code is a random string. Nothing about it is taken from ``exc``,
    so it is safe to include in a response.
    """
    ref = uuid.uuid4().hex[:12]
    target = log if log is not None else _default_logger
    # The name of the exception class is safe to write to the log and makes the
    # entry easier to find later.
    message = f"[{ref}] {context} ({type(exc).__name__})"

    # Pass the exception itself to the logger rather than calling `.exception()`.
    # `.exception()` looks up whichever exception is currently being handled, so
    # it only works inside an `except` block. If one of these helpers is called
    # from somewhere else, such as a callback that runs after a task finishes,
    # `.exception()` would record no stack trace at all and the reference code
    # would point to a log entry with nothing useful in it.
    opt = getattr(target, "opt", None)
    if callable(opt):  # a loguru logger
        opt(exception=exc).error(message)
    else:  # a standard library logger, or a stand-in used by tests
        try:
            target.error(message, exc_info=exc)
        except TypeError:
            target.exception(message)
    return ref


def safe_error_payload(
    exc: BaseException,
    *,
    message: str = GENERIC_MESSAGE,
    log: Any = None,
    context: str | None = None,
) -> dict[str, Any]:
    """Build the body of an error response, for streams and custom formats.

    ``message`` must be fixed text. Never pass anything taken from ``exc``.
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
    """The result of ``safe_error_payload``, wrapped in a ``JSONResponse``.

    ``message`` must be fixed text. Never pass anything taken from ``exc``.
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
    """An ``HTTPException`` whose detail holds only the message and the reference.

    For handlers that raise an error instead of returning a response.
    ``message`` must be fixed text.
    """
    ref = log_error_ref(exc, log=log, context=context or message)
    return HTTPException(status_code=status, detail=f"{message} (ref {ref})")
