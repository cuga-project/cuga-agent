import asyncio
import threading
import weakref


class PerLoopLock:
    """An ``asyncio.Lock`` per running event loop.

    An ``asyncio.Lock`` binds to the loop of the first caller that has to wait, so one lock
    shared by callers on different loops (for example worker threads that each call
    ``asyncio.run``) would never wake a waiter on another loop.
    """

    def __init__(self) -> None:
        self._locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
            weakref.WeakKeyDictionary()
        )
        self._guard = threading.Lock()

    def get(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with self._guard:
            lock = self._locks.get(loop)
            if lock is None:
                lock = self._locks[loop] = asyncio.Lock()
            return lock
