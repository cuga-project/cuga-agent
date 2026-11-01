"""Process-wide aiohttp session for calls to the registry.

Opening a new ``ClientSession`` per tool call also opens a new TCP connection per call. One shared
session keeps the connections alive between calls.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import aiohttp

_session: Optional["aiohttp.ClientSession"] = None
_session_loop: Optional[asyncio.AbstractEventLoop] = None


async def get_http_session() -> "aiohttp.ClientSession":
    """Return the shared session, creating it on first use.

    There is no ``await`` between the check and the assignment, so concurrent callers cannot each
    create a session. Keep it that way, or add a lock.
    """
    global _session, _session_loop
    loop = asyncio.get_running_loop()
    if _session is None or _session.closed or _session_loop is not loop:
        import aiohttp

        _session = aiohttp.ClientSession()
        _session_loop = loop
    return _session


async def close_http_session() -> None:
    """Close the shared session. Safe to call when none was opened, or twice."""
    global _session, _session_loop
    session, _session, _session_loop = _session, None, None
    if session is not None and not session.closed:
        await session.close()
