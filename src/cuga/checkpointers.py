"""Durable checkpointers for the SDK (issue #774, W15.3).

Pass one of these to ``CugaAgent(checkpointer=...)`` (or ``CugaSupervisor``) so
a conversation survives a restart: call the agent again with the same
``thread_id``, from this process or a new one.

- :func:`sqlite` opens an ``AsyncSqliteSaver`` on one local file.
- :func:`postgres` opens an ``AsyncPostgresSaver`` on a connection pool.

Both take explicit arguments and never read ``settings.storage``. Each returns
an opener that works in two ways:

As an async context manager (the connection or pool is closed on exit, also
when the block raises)::

    import cuga
    from cuga import CugaAgent

    async with cuga.checkpointers.sqlite("./agent_state.db") as cp:
        agent = CugaAgent(tools=..., checkpointer=cp.saver)
        await agent.invoke("Hi", thread_id="t-1")

Awaited, when the saver outlives one block (the caller closes it)::

    cp = await cuga.checkpointers.postgres("postgresql://user:pw@host/db", max_size=10)
    try:
        agent = CugaAgent(tools=..., checkpointer=cp.saver)
        ...
    finally:
        await cp.aclose()

Arguments are checked when ``sqlite()`` / ``postgres()`` is called, so a bad
path, URL or pool size raises ``TypeError`` / ``ValueError`` before anything is
opened. Connection errors surface when the opener is entered or awaited.

Either way you get a :class:`~cuga.backend.storage.checkpoint.factory.CheckpointerHandle`:
``cp.saver`` is the saver to pass to the agent, ``await cp.aclose()`` closes it
(idempotent) and ``await cp.ping()`` is a health probe that never raises. An
opener is single-use: enter or await it once. To retry after a failed open,
call ``sqlite()`` / ``postgres()`` again for a new opener.

Serialization: by default these savers use CUGA's explicit msgpack allowlist
(``build_checkpoint_serde()``), which covers the types in CUGA's graph state.
Any other pydantic model, dataclass or Enum you store in a free-form field
(for example a tool result kept in ``variables_storage``) is **not** rebuilt on
load: it comes back as a plain value (a dict, or an Enum's raw value) and
langgraph only logs a ``Blocked deserialization of ...`` warning; nothing
raises. (Pydantic models and dataclasses passed via ``invoke(variables=...)``
are already plain dicts before they are saved, with any saver.) To keep your own
types, pass ``serde=`` with an extended allowlist, here for an Enum::

    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from cuga.backend.storage.checkpoint.serde import CUGA_ALLOWED_MSGPACK_TYPES

    serde = JsonPlusSerializer(
        allowed_msgpack_modules=CUGA_ALLOWED_MSGPACK_TYPES + (("my_app.models", "Color"),)
    )
    async with cuga.checkpointers.sqlite("./agent_state.db", serde=serde) as cp:
        ...

The SDK does not isolate threads: callers own their thread IDs, so use
unguessable IDs and namespace them per end user.

``import cuga`` does not load this module, and loading it imports nothing heavy:
``aiosqlite``, ``psycopg_pool`` and the savers are imported when an opener is
entered or awaited.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Generator
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    from langgraph.checkpoint.serde.base import SerializerProtocol

    from cuga.backend.storage.checkpoint.factory import CheckpointerHandle

__all__ = ["CheckpointerOpener", "postgres", "sqlite"]


class CheckpointerOpener:
    """Opens a checkpointer once, by ``async with`` or by ``await``.

    ``async with`` yields the :class:`CheckpointerHandle` and closes it on exit.
    ``await`` returns the open handle; the caller then owns it and must call
    ``await handle.aclose()``. Nothing is opened until one of the two happens,
    and a second use raises ``RuntimeError``; to retry after a failed open,
    create a new opener.
    """

    __slots__ = ("_open", "_used", "_handle")

    def __init__(self, open_: Callable[[], Awaitable[CheckpointerHandle]]) -> None:
        self._open = open_
        self._used = False
        self._handle: Optional[CheckpointerHandle] = None

    def _start(self) -> Awaitable[CheckpointerHandle]:
        if self._used:
            raise RuntimeError("A cuga.checkpointers opener can only be entered or awaited once")
        self._used = True
        return self._open()

    def __await__(self) -> Generator[Any, None, CheckpointerHandle]:
        return self._start().__await__()

    async def __aenter__(self) -> CheckpointerHandle:
        self._handle = await self._start()
        return self._handle

    async def __aexit__(self, *exc_info: Any) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            await handle.aclose()


def _check_path(path: Any) -> None:
    if isinstance(path, bytes) or not isinstance(path, (str, os.PathLike)):
        raise TypeError(
            f"cuga.checkpointers.sqlite(): path must be a str or os.PathLike, got {type(path).__name__}"
        )
    fspath = os.fspath(path)
    if not isinstance(fspath, str):
        raise TypeError("cuga.checkpointers.sqlite(): path must be a text path, not bytes")
    if not fspath.strip():
        raise ValueError("cuga.checkpointers.sqlite(): path must not be empty")


def _check_postgres_args(url: Any, min_size: Any, max_size: Any) -> None:
    fn = "cuga.checkpointers.postgres()"
    if not isinstance(url, str):
        raise TypeError(f"{fn}: url must be a str, got {type(url).__name__}")
    if not url.strip():
        raise ValueError(f"{fn}: url must not be empty")
    for name, value in (("min_size", min_size), ("max_size", max_size)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{fn}: {name} must be an int, got {type(value).__name__}")
        if value < 1:
            raise ValueError(f"{fn}: {name} must be >= 1, got {value}")
    if min_size > max_size:
        raise ValueError(f"{fn}: min_size ({min_size}) must be <= max_size ({max_size})")


def sqlite(
    path: Union[str, os.PathLike[str]], *, serde: Optional[SerializerProtocol] = None
) -> CheckpointerOpener:
    """Open an ``AsyncSqliteSaver`` on the SQLite file at ``path``.

    The parent directory is created, the file is made owner-only (0o600) where
    the file system allows it, WAL mode is enabled and the checkpoint tables are
    created. ``serde=None`` uses CUGA's allowlist serializer (see the module
    docstring).

    Raises ``TypeError`` / ``ValueError`` immediately for a path that is not a
    non-empty ``str`` or ``os.PathLike``.

    Usage::

        async with cuga.checkpointers.sqlite("./agent_state.db") as cp:
            agent = CugaAgent(tools=..., checkpointer=cp.saver)
            await agent.invoke("Hi", thread_id="t-1")
    """
    _check_path(path)

    async def _open() -> CheckpointerHandle:
        from cuga.backend.storage.checkpoint.factory import _create_sqlite

        return await _create_sqlite(path, serde=serde)

    return CheckpointerOpener(_open)


def postgres(
    url: str,
    *,
    min_size: int = 1,
    max_size: int = 5,
    serde: Optional[SerializerProtocol] = None,
) -> CheckpointerOpener:
    """Open an ``AsyncPostgresSaver`` on a ``psycopg_pool`` pool for ``url``.

    ``url`` is a ``postgresql://`` URL or a libpq conninfo string. The pool holds
    ``min_size`` to ``max_size`` connections (both at least 1). The checkpoint
    tables are created or migrated on open, so the database user needs DDL
    rights. ``serde=None`` uses CUGA's allowlist serializer (see the module
    docstring).

    Raises ``TypeError`` / ``ValueError`` immediately for an empty or non-str
    ``url``, or pool sizes that are not ints >= 1 with ``min_size <= max_size``.

    Usage::

        async with cuga.checkpointers.postgres("postgresql://user:pw@host/db") as cp:
            agent = CugaAgent(tools=..., checkpointer=cp.saver)
            await agent.invoke("Hi", thread_id="t-1")
    """
    _check_postgres_args(url, min_size, max_size)

    async def _open() -> CheckpointerHandle:
        from cuga.backend.storage.checkpoint.factory import _create_postgres

        return await _create_postgres(url, min_size=min_size, max_size=max_size, serde=serde)

    return CheckpointerOpener(_open)
