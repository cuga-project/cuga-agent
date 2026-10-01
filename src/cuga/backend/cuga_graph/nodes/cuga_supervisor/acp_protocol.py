"""Thin public orchestration facade for outbound ACP delegation."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Mapping
from uuid import uuid4

from .acp_client.callbacks import ACPClientCallbacks, LifecycleRegistrar, PermissionHandler
from .acp_client.pending import (
    FinalRecordOwner,
    PendingACPDelegation,
    PendingACPDelegationError,
    PendingACPDelegationRegistry,
    PendingACPDelegationRegistryFinalizedCancelled,
    PendingACPDelegationWinnerError,
    PendingState,
    safe_identity,
)
from .acp_client.permissions import SafePermissionRequest, select_permission_option
from .acp_client.config import ACPProcessConfig
from .acp_client.process import ACPFactoryContractError, ACPStartupTimeoutError, open_acp_process_session
from .acp_client import result as normalized

_REPAUSED = object()


class ACPPermissionPause(BaseException):
    """Typed control signal carrying only checkpoint-safe permission metadata."""

    def __init__(self, pending_id: str, agent_name: str, request: SafePermissionRequest) -> None:
        super().__init__("ACP delegation is waiting for permission")
        self.pending_id = pending_id
        self.agent_name = agent_name
        self.request = request


class ACPPermissionWinnerCancelled(BaseException):
    """Cancellation after a claim winner retained sole graph-record authority."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("ACP permission winner was cancelled during terminalization")
        self.result = result


class ACPPermissionRegistryFinalized(BaseException):
    """The registry owns and completed terminal recording for this claim."""


class ACPPermissionRuntimeBridge:
    """Coordinate a live ACP prompt with one graph pause and one exact resume."""

    def __init__(
        self,
        *,
        registry: PendingACPDelegationRegistry,
        thread_id: str,
        agent_name: str,
        interactive: bool,
        finalizer: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> None:
        self.registry = registry
        try:
            self.thread_id = safe_identity(thread_id, field="thread_id")
            self.agent_name = safe_identity(agent_name, field="agent_name")
        except ValueError:
            self.thread_id = ""
            self.agent_name = ""
            interactive = False
        self.interactive = interactive
        self._finalizer = finalizer
        self._lifecycle_id: str | None = None
        self._owner: Any = None
        self._prompt_task: asyncio.Task[Any] | None = None
        self._pause_ready = asyncio.Event()
        self._pause: ACPPermissionPause | None = None
        self._permission_active = False
        self._was_parked = False

    @property
    def was_parked(self) -> bool:
        return self._was_parked

    async def register_lifecycle(self, lifecycle_id: str, owner: Any) -> None:
        self._lifecycle_id = lifecycle_id
        self._owner = owner

    async def permission_handler(self, request: Any) -> str | None:
        if not self.interactive or self._permission_active or self._pause is not None:
            return None
        if self._prompt_task is None or self._owner is None or request.lifecycle_id != self._lifecycle_id:
            return None
        self._permission_active = True
        pending_id = uuid4().hex
        safe_request = SafePermissionRequest.create(
            lifecycle_id=request.lifecycle_id,
            session_id=request.session_id,
            tool_call_id=request.tool_call_id,
            title=request.title,
            description=getattr(request, "description", "") or request.title,
            kind=request.kind,
            locations=tuple(getattr(request, "locations", ())),
            options=tuple(request.options),
            ttl_seconds=self.registry.ttl_seconds,
        )
        permission_future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        try:
            await self.registry.insert(
                pending_id=pending_id,
                thread_id=self.thread_id,
                agent_name=self.agent_name,
                request=safe_request,
                owner=self._owner,
                prompt_task=self._prompt_task,
                permission_future=permission_future,
                cleanup=self._cancel_prompt,
                finalizer=self._finalizer,
                bridge=self,
            )
        except PendingACPDelegationError:
            return None
        self._pause = ACPPermissionPause(pending_id, self.agent_name, safe_request)
        self._pause_ready.set()
        try:
            return await permission_future
        finally:
            self._permission_active = False

    async def run(self, prompt: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        self._prompt_task = asyncio.create_task(prompt)
        pause_waiter = asyncio.create_task(self._pause_ready.wait())
        try:
            done, _ = await asyncio.wait(
                {self._prompt_task, pause_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            if self._prompt_task in done:
                pause_waiter.cancel()
                return await self._prompt_task
            if self._pause is None:
                raise RuntimeError("ACP permission pause was not initialized")
            self._was_parked = True
            raise self._pause
        except asyncio.CancelledError:
            await self._cancel_prompt()
            raise
        finally:
            if not pause_waiter.done():
                pause_waiter.cancel()

    async def resume(self, *, pending_id: str, approved: bool | None) -> dict[str, Any]:
        """Answer one pending permission; raises a fresh ``ACPPermissionPause`` if the prompt asks again."""
        entry = await self.registry.claim(pending_id, thread_id=self.thread_id, agent_name=self.agent_name)
        # Ownership was checked by the claim above; the prompt's own bridge holds the pause state.
        owner = entry.bridge if isinstance(entry.bridge, ACPPermissionRuntimeBridge) else self
        return await owner._resume_claimed(entry, approved=approved)

    async def _resume_claimed(self, entry: PendingACPDelegation, *, approved: bool | None) -> dict[str, Any]:
        failed_result = {
            "result": "ACP pending delegation is stale or could not be resumed.",
            "status": "failed",
            "variables": {},
        }
        try:
            selected = select_permission_option(entry.request.options, approved=approved)
            if selected is None:
                try:
                    registry_finalized = await self.registry.settle_claim(
                        entry,
                        PendingState.CANCELLED,
                        cancel_prompt=True,
                    )
                except asyncio.CancelledError:
                    raise ACPPermissionWinnerCancelled(failed_result) from None
                except Exception as exc:
                    raise PendingACPDelegationWinnerError(
                        "ACP permission rejection failed during cleanup"
                    ) from exc
                if registry_finalized:
                    raise ACPPermissionRegistryFinalized from None
                raise PendingACPDelegationWinnerError("ACP permission response is ambiguous or unavailable")
            # Re-arm before answering so the prompt's next operation can pause again.
            self._pause = None
            self._pause_ready = asyncio.Event()
            if not entry.permission_future.done():
                entry.permission_future.set_result(selected)
            try:
                result = await self._await_prompt_or_pause(entry.prompt_task)
            except asyncio.CancelledError:
                try:
                    registry_finalized = await self.registry.settle_claim(
                        entry,
                        PendingState.CANCELLED,
                        cancel_prompt=True,
                    )
                except PendingACPDelegationRegistryFinalizedCancelled:
                    raise
                except BaseException:
                    registry_finalized = False
                if registry_finalized:
                    raise ACPPermissionRegistryFinalized from None
                raise ACPPermissionWinnerCancelled(failed_result) from None
            except Exception as exc:
                try:
                    registry_finalized = await self.registry.settle_claim(
                        entry,
                        PendingState.COMPLETED,
                        cancel_prompt=False,
                    )
                except asyncio.CancelledError:
                    raise ACPPermissionWinnerCancelled(failed_result) from None
                except Exception:
                    raise PendingACPDelegationWinnerError(
                        "ACP resumed delegation failed during cleanup"
                    ) from exc
                if registry_finalized:
                    raise ACPPermissionRegistryFinalized from None
                raise PendingACPDelegationWinnerError("ACP resumed delegation failed") from exc
            if result is _REPAUSED:
                pause = self._pause
                if pause is not None and await self.registry.hand_off_claim(entry):
                    self._was_parked = True
                    raise pause
                # The registry reclaimed this entry (expiry/shutdown) and is tearing the prompt
                # down; drop the newer pause so it cannot be approved or finalized twice.
                if pause is not None:
                    await self.registry.cancel(pause.pending_id, reason="superseded claim was reclaimed")
                if entry.final_record_owner is FinalRecordOwner.REGISTRY:
                    raise ACPPermissionRegistryFinalized from None
                raise PendingACPDelegationWinnerError("ACP permission pause could not be handed off")
            try:
                registry_finalized = await self.registry.settle_claim(
                    entry,
                    PendingState.COMPLETED,
                    cancel_prompt=False,
                )
            except PendingACPDelegationRegistryFinalizedCancelled:
                raise
            except asyncio.CancelledError:
                raise ACPPermissionWinnerCancelled(result) from None
            except Exception as exc:
                raise PendingACPDelegationWinnerError("ACP resumed delegation failed during cleanup") from exc
            if registry_finalized:
                raise ACPPermissionRegistryFinalized from None
            return result
        except PendingACPDelegationWinnerError:
            raise

    async def _await_prompt_or_pause(self, prompt_task: asyncio.Future[Any]) -> Any:
        """Return the prompt result, or ``_REPAUSED`` once the prompt requests another permission."""
        pause_waiter = asyncio.create_task(self._pause_ready.wait())
        try:
            done, _ = await asyncio.wait({prompt_task, pause_waiter}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            prompt_task.cancel()
            raise
        finally:
            if not pause_waiter.done():
                pause_waiter.cancel()
        if prompt_task in done:
            return await prompt_task
        return _REPAUSED

    async def _cancel_prompt(self) -> None:
        task = self._prompt_task
        if task is not None and not task.done():
            task.cancel()


async def resume_acp_delegation(
    *,
    registry: PendingACPDelegationRegistry,
    pending_id: str,
    thread_id: str,
    agent_name: str,
    approved: bool | None,
) -> dict[str, Any]:
    bridge = ACPPermissionRuntimeBridge(
        registry=registry,
        thread_id=thread_id,
        agent_name=agent_name,
        interactive=True,
    )
    return await bridge.resume(pending_id=pending_id, approved=approved)


async def delegate_task_via_acp(
    *,
    config: ACPProcessConfig,
    task: str,
    permission_handler: PermissionHandler | None = None,
    lifecycle_registrar: LifecycleRegistrar | None = None,
    permission_bridge: ACPPermissionRuntimeBridge | None = None,
) -> dict[str, Any]:
    """Run exactly one prompt against one spawned ACP agent subprocess."""

    delegation = _delegate_task_via_acp(
        config=config,
        task=task,
        permission_handler=(
            permission_bridge.permission_handler if permission_bridge else permission_handler
        ),
        lifecycle_registrar=(
            permission_bridge.register_lifecycle if permission_bridge else lifecycle_registrar
        ),
    )
    return await permission_bridge.run(delegation) if permission_bridge else await delegation


async def _delegate_task_via_acp(
    *,
    config: ACPProcessConfig,
    task: str,
    permission_handler: PermissionHandler | None = None,
    lifecycle_registrar: LifecycleRegistrar | None = None,
    process_factory: Callable[..., Any] | None = None,
    connection_factory: Callable[..., Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Dependency-injected implementation used by focused lifecycle tests."""

    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a non-empty string")

    callbacks = ACPClientCallbacks(
        permission_handler=permission_handler,
        lifecycle_id=uuid4().hex,
        lifecycle_registrar=lifecycle_registrar,
    )
    try:
        async with open_acp_process_session(
            config,
            callbacks,
            process_factory=process_factory,
            connection_factory=connection_factory,
            environ=environ,
        ) as session:
            await callbacks.register_lifecycle(session)
            from acp.schema import TextContentBlock

            try:
                await asyncio.wait_for(
                    session.connection.prompt(
                        session.session_id,
                        [TextContentBlock(type="text", text=task)],
                    ),
                    timeout=config.prompt_timeout,
                )
            except (ConnectionError, EOFError, ValueError) as exc:
                if session.process.returncode is not None:
                    raise ChildProcessError("ACP subprocess exited during prompt") from exc
                raise

            if callbacks.permission_required:
                return normalized.permission_required()
            return normalized.success(callbacks.text)
    except asyncio.CancelledError:
        raise
    except (ACPFactoryContractError, ACPStartupTimeoutError):
        return normalized.startup_failure()
    except (TimeoutError, asyncio.TimeoutError):
        return normalized.timeout()
    except ChildProcessError:
        return normalized.subprocess_exit()
    except (ConnectionError, EOFError, ValueError):
        return normalized.protocol_failure()
    except OSError:
        return normalized.startup_failure()
    except Exception:
        return normalized.protocol_failure()
