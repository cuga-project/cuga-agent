"""Bounded in-memory ownership registry for paused outbound ACP delegations."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Any, Awaitable, Callable

from .permissions import SafePermissionRequest

Cleanup = Callable[[], Awaitable[None] | None]
Finalizer = Callable[[str], Awaitable[None] | None]

_IDENTITY_LIMIT = 128
_IDENTITY_DIGEST_LENGTH = 32
_REPLACEMENT = "�"


def safe_identity(value: object, *, field: str) -> str:
    """Return a bounded safe identity, preserving uniqueness across every lossy normalization."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a non-empty string")
    normalized = "".join(character if character.isprintable() else _REPLACEMENT for character in value)
    normalized = " ".join(normalized.split())
    if not normalized:
        raise ValueError(f"{field} must be a non-empty string")
    changed = normalized != value
    if not changed and len(normalized) <= _IDENTITY_LIMIT:
        return normalized
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:_IDENTITY_DIGEST_LENGTH]
    prefix_limit = _IDENTITY_LIMIT - len(digest) - 1
    return f"{normalized[:prefix_limit]}-{digest}"


def permission_response_decision(expected_pending_id: object, response: Any) -> bool | None:
    """Return the user's decision only when the client submitted it for this exact permission.

    Reads ``submitted_additional_data`` (the client's own payload) rather than
    ``additional_data``, which WaitForResponse restores from the current action.
    """
    submitted_tool = getattr(getattr(response, "submitted_additional_data", None), "tool", None)
    submitted_permission = submitted_tool.get("acp_permission") if isinstance(submitted_tool, dict) else None
    submitted_id = submitted_permission.get("pending_id") if isinstance(submitted_permission, dict) else None
    try:
        canonical = (
            isinstance(submitted_id, str) and safe_identity(submitted_id, field="pending_id") == submitted_id
        )
    except ValueError:
        return None
    if not canonical or submitted_id != expected_pending_id:
        return None
    confirmed = getattr(response, "confirmed", None)
    return confirmed if isinstance(confirmed, bool) else None


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


class PendingACPDelegationWinnerError(PendingACPDelegationError):
    """A registry claim winner failed after acquiring sole final-record authority."""


class PendingACPDelegationWinnerCancelled(BaseException):
    """Cancellation after canonical owned cleanup granted sole final-record authority."""

    def __init__(self, metadata: SafePendingDelegation) -> None:
        super().__init__("ACP pending delegation winner was cancelled during cleanup")
        self.metadata = metadata


class PendingACPDelegationRegistryFinalizedCancelled(BaseException):
    """Caller cancellation after the registry acquired final-record authority."""


class PendingState(str, Enum):
    PENDING = "pending"
    RESUMING = "resuming"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class FinalRecordOwner(str, Enum):
    REGISTRY = "registry"
    CLAIMANT = "claimant"


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
    finalizer: Finalizer | None
    created_monotonic: float
    expires_monotonic: float
    state: PendingState = PendingState.PENDING
    final_record_owner: FinalRecordOwner = FinalRecordOwner.REGISTRY
    cleaned: bool = False
    expiry_task: asyncio.Task[None] | None = None
    cleanup_task: asyncio.Task[None] | None = None
    finalized: bool = False
    # The runtime bridge that owns the live prompt, so a resume can await further pauses.
    bridge: Any = None


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
        finalizer: Finalizer | None = None,
        bridge: Any = None,
    ) -> None:
        await self.expire()
        try:
            pending_id = safe_identity(pending_id, field="pending_id")
            thread_id = safe_identity(thread_id, field="thread_id")
            agent_name = safe_identity(agent_name, field="agent_name")
        except ValueError:
            now = monotonic()
            rejected_entry = PendingACPDelegation(
                pending_id="invalid",
                thread_id="invalid",
                agent_name="invalid",
                request=request,
                owner=owner,
                prompt_task=prompt_task,
                permission_future=permission_future,
                cleanup=cleanup,
                finalizer=finalizer,
                created_monotonic=now,
                expires_monotonic=now,
            )
            await self._cleanup_entry(rejected_entry, PendingState.CANCELLED)
            raise
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
            finalizer=finalizer,
            created_monotonic=now,
            expires_monotonic=now + self._ttl_seconds,
            bridge=bridge,
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
        pending_id = safe_identity(pending_id, field="pending_id")
        thread_id = safe_identity(thread_id, field="thread_id")
        agent_name = safe_identity(agent_name, field="agent_name")
        async with self._lock:
            entry = self._entries.get(pending_id)
            if entry is None:
                raise PendingACPDelegationNotFoundError("ACP pending delegation is stale")
            if entry.thread_id != thread_id or entry.agent_name != agent_name:
                raise PendingACPDelegationOwnershipError("ACP pending delegation ownership mismatch")
            if entry.state is not PendingState.PENDING:
                raise PendingACPDelegationStateError("ACP pending delegation was already resumed")
            entry.state = PendingState.RESUMING
            entry.final_record_owner = FinalRecordOwner.CLAIMANT
            return entry

    async def complete(self, pending_id: str) -> None:
        await self._remove_and_cleanup(
            pending_id,
            PendingState.COMPLETED,
            cancel_prompt=False,
        )

    async def cancel(self, pending_id: str, *, reason: str, finalize: bool = False) -> None:
        await self._remove_and_cleanup(
            pending_id,
            PendingState.CANCELLED,
            finalize=finalize,
            outcome=reason,
        )

    async def cancel_owned(
        self,
        pending_id: str,
        *,
        thread_id: str,
        agent_name: str,
        reason: str,
    ) -> SafePendingDelegation:
        """Atomically grant one exact full-owner cancellation and return its record authority."""
        del reason
        pending_id = safe_identity(pending_id, field="pending_id")
        thread_id = safe_identity(thread_id, field="thread_id")
        expected_agent = safe_identity(agent_name, field="agent_name")
        async with self._lock:
            entry = self._entries.get(pending_id)
            if entry is None:
                raise PendingACPDelegationNotFoundError("ACP pending delegation is stale")
            if entry.thread_id != thread_id or entry.agent_name != expected_agent:
                raise PendingACPDelegationOwnershipError("ACP pending delegation ownership mismatch")
            if entry.state is not PendingState.PENDING:
                raise PendingACPDelegationStateError("ACP pending delegation was already resumed")
            self._entries.pop(pending_id)
            entry.state = PendingState.CANCELLED
            if entry.expiry_task is not None and entry.expiry_task is not asyncio.current_task():
                entry.expiry_task.cancel()
            entry.expiry_task = None
            cleanup_task = self._start_cleanup_locked(entry, PendingState.CANCELLED)
            metadata = SafePendingDelegation(
                pending_id=entry.pending_id,
                thread_id=entry.thread_id,
                agent_name=entry.agent_name,
                permission=entry.request,
                state=entry.state.value,
            )
        try:
            await self._await_cleanup(cleanup_task)
        except asyncio.CancelledError:
            raise PendingACPDelegationWinnerCancelled(metadata) from None
        return metadata

    async def settle_claim(
        self,
        entry: PendingACPDelegation,
        state: PendingState,
        *,
        cancel_prompt: bool,
    ) -> bool:
        """Settle the exact claim and report whether a registry finalizer won authority."""
        cleanup_task: asyncio.Task[None] | None = None
        async with self._lock:
            current = self._entries.get(entry.pending_id)
            if current is entry:
                self._entries.pop(entry.pending_id)
                if entry.expiry_task is not None and entry.expiry_task is not asyncio.current_task():
                    entry.expiry_task.cancel()
                entry.expiry_task = None
                cleanup_task = self._start_cleanup_locked(
                    entry,
                    state,
                    cancel_prompt=cancel_prompt,
                )
            elif entry.final_record_owner is FinalRecordOwner.REGISTRY:
                cleanup_task = entry.cleanup_task
            else:
                return False
        if cleanup_task is not None:
            try:
                await self._await_cleanup(cleanup_task)
            except asyncio.CancelledError:
                async with self._lock:
                    if entry.final_record_owner is FinalRecordOwner.REGISTRY and entry.finalized:
                        raise PendingACPDelegationRegistryFinalizedCancelled from None
                    entry.final_record_owner = FinalRecordOwner.CLAIMANT
                raise
            except BaseException:
                async with self._lock:
                    if entry.final_record_owner is FinalRecordOwner.REGISTRY and not entry.finalized:
                        entry.final_record_owner = FinalRecordOwner.CLAIMANT
                    claimant_owns = entry.final_record_owner is FinalRecordOwner.CLAIMANT
                if claimant_owns:
                    raise
        return entry.final_record_owner is FinalRecordOwner.REGISTRY

    async def hand_off_claim(self, entry: PendingACPDelegation) -> bool:
        """Retire a claimed entry whose live prompt now waits on a newer pending entry.

        The prompt, process, and finalizer belong to the newer entry, so this skips
        cleanup entirely. Returns False when the registry already took the entry
        (expiry or shutdown), in which case its cleanup owns the prompt.
        """
        async with self._lock:
            if self._entries.get(entry.pending_id) is not entry or entry.cleanup_task is not None:
                return False
            self._entries.pop(entry.pending_id)
            if entry.expiry_task is not None and entry.expiry_task is not asyncio.current_task():
                entry.expiry_task.cancel()
            entry.expiry_task = None
            entry.state = PendingState.COMPLETED
            entry.cleaned = True
            return True

    async def expire(self) -> int:
        now = monotonic()
        cleanup_tasks = []
        async with self._lock:
            for pending_id, entry in list(self._entries.items()):
                if entry.expires_monotonic <= now:
                    self._entries.pop(pending_id, None)
                    if entry.expiry_task is not None and entry.expiry_task is not asyncio.current_task():
                        entry.expiry_task.cancel()
                    entry.expiry_task = None
                    cleanup_tasks.append(
                        self._start_cleanup_locked(
                            entry,
                            PendingState.EXPIRED,
                            finalize=True,
                            outcome="expired",
                        )
                    )
        if cleanup_tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in cleanup_tasks), return_exceptions=True)
        return len(cleanup_tasks)

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed and not self._entries:
                return
            self._closed = True
            entries = list(self._entries.values())
            self._entries.clear()
            cleanup_tasks = []
            for entry in entries:
                if entry.expiry_task is not None:
                    entry.expiry_task.cancel()
                    entry.expiry_task = None
                cleanup_tasks.append(
                    self._start_cleanup_locked(
                        entry,
                        PendingState.CANCELLED,
                        finalize=True,
                        outcome="shutdown",
                    )
                )
        if cleanup_tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in cleanup_tasks), return_exceptions=True)

    async def _remove_and_cleanup(
        self,
        pending_id: str,
        state: PendingState,
        *,
        cancel_prompt: bool = True,
        finalize: bool = False,
        outcome: str = "failed",
    ) -> None:
        async with self._lock:
            entry = self._entries.pop(pending_id, None)
            if entry is None:
                return
            if entry.expiry_task is not None and entry.expiry_task is not asyncio.current_task():
                entry.expiry_task.cancel()
            entry.expiry_task = None
            cleanup_task = self._start_cleanup_locked(
                entry,
                state,
                cancel_prompt=cancel_prompt,
                finalize=finalize,
                outcome=outcome,
            )
        await self._await_cleanup(cleanup_task)

    async def _expire_entry(self, expected: PendingACPDelegation) -> None:
        try:
            await asyncio.sleep(self._ttl_seconds)
            async with self._lock:
                entry = self._entries.get(expected.pending_id)
                if entry is not expected:
                    return
                self._entries.pop(expected.pending_id, None)
                entry.expiry_task = None
                cleanup_task = self._start_cleanup_locked(
                    entry,
                    PendingState.EXPIRED,
                    finalize=True,
                    outcome="expired",
                )
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            return

    async def _prompt_finished(self, pending_id: str) -> None:
        async with self._lock:
            entry = self._entries.get(pending_id)
            if entry is None or entry.state is PendingState.RESUMING:
                return
            self._entries.pop(pending_id)
            if entry.expiry_task is not None and entry.expiry_task is not asyncio.current_task():
                entry.expiry_task.cancel()
            entry.expiry_task = None
            cleanup_task = self._start_cleanup_locked(
                entry,
                PendingState.COMPLETED,
                cancel_prompt=False,
                finalize=True,
                outcome="process exited",
            )
        await asyncio.shield(cleanup_task)

    def _start_cleanup_locked(
        self,
        entry: PendingACPDelegation,
        state: PendingState,
        *,
        cancel_prompt: bool = True,
        finalize: bool = False,
        outcome: str = "failed",
    ) -> asyncio.Task[None]:
        if entry.cleanup_task is None:
            entry.state = state
            if finalize:
                entry.final_record_owner = FinalRecordOwner.REGISTRY
            entry.cleanup_task = asyncio.create_task(
                self._run_cleanup(
                    entry,
                    cancel_prompt=cancel_prompt,
                    finalize=finalize,
                    outcome=outcome,
                )
            )
        return entry.cleanup_task

    async def _await_cleanup(self, cleanup_task: asyncio.Task[None]) -> None:
        cancelled = False
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            try:
                cleanup_task.result()
            except BaseException:
                pass
            raise asyncio.CancelledError
        cleanup_task.result()

    async def _cleanup_entry(
        self,
        entry: PendingACPDelegation,
        state: PendingState,
        *,
        cancel_prompt: bool = True,
        finalize: bool = False,
        outcome: str = "failed",
    ) -> None:
        async with self._lock:
            if entry.cleaned:
                return
            cleanup_task = self._start_cleanup_locked(
                entry,
                state,
                cancel_prompt=cancel_prompt,
                finalize=finalize,
                outcome=outcome,
            )
        await self._await_cleanup(cleanup_task)

    async def _run_cleanup(
        self,
        entry: PendingACPDelegation,
        *,
        cancel_prompt: bool,
        finalize: bool,
        outcome: str,
    ) -> None:
        cleanup_error: BaseException | None = None
        try:
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
                try:
                    result = entry.cleanup()
                    if inspect.isawaitable(result):
                        await result
                except BaseException as exc:
                    cleanup_error = exc
            if finalize and entry.finalizer is not None and not entry.finalized:
                result = entry.finalizer(outcome)
                if inspect.isawaitable(result):
                    await result
                entry.finalized = True
            if cleanup_error is not None:
                raise cleanup_error
        finally:
            async with self._lock:
                entry.cleaned = True
