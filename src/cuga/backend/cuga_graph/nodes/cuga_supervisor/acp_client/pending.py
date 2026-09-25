"""Bounded in-memory ownership registry for paused outbound ACP delegations."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Any, Awaitable, Callable

from .permissions import SafePermissionRequest

Cleanup = Callable[[], Awaitable[None] | None]


class PendingACPDelegationError(RuntimeError):
    """Base safe registry failure."""


class PendingACPDelegationCapacityError(PendingACPDelegationError):
    """The bounded registry cannot accept another delegation."""


class PendingACPDelegationNotFoundError(PendingACPDelegationError):
    """The delegation is stale or was already removed."""


class PendingACPDelegationOwnershipError(PendingACPDelegationError):
    """The resume does not own the pending delegation."""


class PendingACPDelegationStateError(PendingACPDelegationError):
    """The delegation was already claimed or otherwise cannot transition."""


class PendingState(str, Enum):
    PENDING = "pending"
    RESUMING = "resuming"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SafePendingDelegation:
    pending_id: str
    thread_id: str
    agent_name: str
    permission: SafePermissionRequest
    state: str


@dataclass
class PendingACPDelegation:
    pending_id: str
    thread_id: str
    agent_name: str
    request: SafePermissionRequest
    owner: Any
    prompt_task: asyncio.Future[Any]
    permission_future: asyncio.Future[str | None]
    cleanup: Cleanup | None
    created_monotonic: float
    expires_monotonic: float
    state: PendingState = PendingState.PENDING
    cleaned: bool = False
    expiry_task: asyncio.Task[None] | None = None


class PendingACPDelegationRegistry:
    """Own live resources with bounded capacity, TTL, and atomic resume claims."""

    def __init__(self, *, capacity: int = 64, ttl_seconds: float = 300) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._capacity = capacity
        self._ttl_seconds = float(ttl_seconds)
        self._entries: dict[str, PendingACPDelegation] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    async def size(self) -> int:
        await self.expire()
        async with self._lock:
            return len(self._entries)

    async def insert(
        self,
        *,
        pending_id: str,
        thread_id: str,
        agent_name: str,
        request: SafePermissionRequest,
        owner: Any,
        prompt_task: asyncio.Future[Any],
        permission_future: asyncio.Future[str | None],
        cleanup: Cleanup | None = None,
    ) -> None:
        await self.expire()
        now = monotonic()
        entry = PendingACPDelegation(
            pending_id=pending_id,
            thread_id=thread_id,
            agent_name=agent_name,
            request=request,
            owner=owner,
            prompt_task=prompt_task,
            permission_future=permission_future,
            cleanup=cleanup,
            created_monotonic=now,
            expires_monotonic=now + self._ttl_seconds,
        )
        rejected = False
        async with self._lock:
            if self._closed or pending_id in self._entries or len(self._entries) >= self._capacity:
                rejected = True
            else:
                self._entries[pending_id] = entry
                entry.expiry_task = asyncio.create_task(self._expire_entry(entry))
                prompt_task.add_done_callback(
                    lambda _done, entry_id=pending_id: asyncio.create_task(self._prompt_finished(entry_id))
                )
        if rejected:
            await self._cleanup_entry(entry, PendingState.CANCELLED)
            raise PendingACPDelegationCapacityError("ACP pending delegation registry is unavailable")

    async def safe_metadata(self, pending_id: str) -> SafePendingDelegation | None:
        await self.expire()
        async with self._lock:
            entry = self._entries.get(pending_id)
            if entry is None:
                return None
            return SafePendingDelegation(
                pending_id=entry.pending_id,
                thread_id=entry.thread_id,
                agent_name=entry.agent_name,
                permission=entry.request,
                state=entry.state.value,
            )

    async def claim(self, pending_id: str, *, thread_id: str, agent_name: str) -> PendingACPDelegation:
        await self.expire()
        async with self._lock:
            entry = self._entries.get(pending_id)
            if entry is None:
                raise PendingACPDelegationNotFoundError("ACP pending delegation is stale")
            if entry.thread_id != thread_id or entry.agent_name != agent_name:
                raise PendingACPDelegationOwnershipError("ACP pending delegation ownership mismatch")
            if entry.state is not PendingState.PENDING:
                raise PendingACPDelegationStateError("ACP pending delegation was already resumed")
            entry.state = PendingState.RESUMING
            return entry

    async def complete(self, pending_id: str) -> None:
        entry = await self._remove(pending_id, PendingState.COMPLETED)
        if entry is not None:
            await self._cleanup_entry(entry, PendingState.COMPLETED, cancel_prompt=False)

    async def cancel(self, pending_id: str, *, reason: str) -> None:
        del reason
        entry = await self._remove(pending_id, PendingState.CANCELLED)
        if entry is not None:
            await self._cleanup_entry(entry, PendingState.CANCELLED)

    async def expire(self) -> int:
        now = monotonic()
        expired: list[PendingACPDelegation] = []
        async with self._lock:
            for pending_id, entry in list(self._entries.items()):
                if entry.expires_monotonic <= now:
                    self._entries.pop(pending_id, None)
                    entry.state = PendingState.EXPIRED
                    expired.append(entry)
        for entry in expired:
            await self._cleanup_entry(entry, PendingState.EXPIRED)
        return len(expired)

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed and not self._entries:
                return
            self._closed = True
            entries = list(self._entries.values())
            self._entries.clear()
            expiry_tasks = []
            for entry in entries:
                entry.state = PendingState.CANCELLED
                if entry.expiry_task is not None:
                    entry.expiry_task.cancel()
                    expiry_tasks.append(entry.expiry_task)
                    entry.expiry_task = None
        if expiry_tasks:
            await asyncio.gather(*expiry_tasks, return_exceptions=True)
        for entry in entries:
            await self._cleanup_entry(entry, PendingState.CANCELLED)

    async def _remove(self, pending_id: str, state: PendingState) -> PendingACPDelegation | None:
        async with self._lock:
            entry = self._entries.pop(pending_id, None)
            if entry is not None:
                entry.state = state
                if entry.expiry_task is not None and entry.expiry_task is not asyncio.current_task():
                    entry.expiry_task.cancel()
                entry.expiry_task = None
            return entry

    async def _expire_entry(self, expected: PendingACPDelegation) -> None:
        try:
            await asyncio.sleep(self._ttl_seconds)
            async with self._lock:
                entry = self._entries.get(expected.pending_id)
                if entry is not expected:
                    return
                self._entries.pop(expected.pending_id, None)
                entry.state = PendingState.EXPIRED
                entry.expiry_task = None
            await self._cleanup_entry(entry, PendingState.EXPIRED)
        except asyncio.CancelledError:
            return

    async def _prompt_finished(self, pending_id: str) -> None:
        entry = await self._remove(pending_id, PendingState.COMPLETED)
        if entry is not None:
            await self._cleanup_entry(entry, PendingState.COMPLETED, cancel_prompt=False)

    async def _cleanup_entry(
        self,
        entry: PendingACPDelegation,
        state: PendingState,
        *,
        cancel_prompt: bool = True,
    ) -> None:
        async with self._lock:
            if entry.cleaned:
                return
            entry.cleaned = True
            entry.state = state
        if not entry.permission_future.done():
            entry.permission_future.set_result(None)
        if cancel_prompt and not entry.prompt_task.done():
            entry.prompt_task.cancel()
            if entry.prompt_task is not asyncio.current_task():
                try:
                    await entry.prompt_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        if entry.cleanup is not None:
            result = entry.cleanup()
            if inspect.isawaitable(result):
                await result
