"""Bounded, single-consumer ACP permission registry contracts."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.unit


async def _wait_forever() -> None:
    await asyncio.Event().wait()


def _request(*, options=None, title="Delete records", description="Remove selected records"):
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.permissions import (
        PermissionOptionDTO,
        SafePermissionRequest,
    )

    return SafePermissionRequest.create(
        lifecycle_id="a" * 32,
        session_id="session-safe",
        tool_call_id="tool-safe",
        title=title,
        description=description,
        kind="delete",
        locations=("workspace/report.txt",),
        options=tuple(
            options
            or (
                PermissionOptionDTO("allow-once", "Allow once", "allow_once"),
                PermissionOptionDTO("reject-once", "Reject once", "reject_once"),
            )
        ),
        ttl_seconds=30,
    )


class _Owner:
    def __init__(self) -> None:
        self.connection = object()
        self.process = object()
        self.session_id = "runtime-session"


@pytest.mark.asyncio
async def test_insert_and_safe_metadata_lookup_exposes_no_runtime_objects_or_secrets() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )

    registry = PendingACPDelegationRegistry(capacity=2, ttl_seconds=30)
    decision = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(_wait_forever())
    owner = _Owner()
    request = _request(title="Deploy service", description="Run safe summary")

    await registry.insert(
        pending_id="pending-1",
        thread_id="thread-1",
        agent_name="coder",
        request=request,
        owner=owner,
        prompt_task=task,
        permission_future=decision,
    )

    metadata = await registry.safe_metadata("pending-1")
    assert metadata is not None
    encoded = repr(asdict(metadata))
    assert metadata.pending_id == "pending-1"
    assert metadata.agent_name == "coder"
    assert metadata.permission.title == "Deploy service"
    assert "runtime-session" not in encoded
    assert "connection" not in encoded
    assert "process" not in encoded
    assert "future" not in encoded
    await registry.cancel("pending-1", reason="test cleanup")


@pytest.mark.asyncio
async def test_capacity_rejects_new_entry_and_cleans_it_exactly_once() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationCapacityError,
        PendingACPDelegationRegistry,
    )

    cleaned = []
    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=30)

    async def add(pending_id: str):
        future = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(_wait_forever())
        await registry.insert(
            pending_id=pending_id,
            thread_id="thread",
            agent_name="coder",
            request=_request(),
            owner=_Owner(),
            prompt_task=task,
            permission_future=future,
            cleanup=lambda: cleaned.append(pending_id),
        )

    await add("first")
    with pytest.raises(PendingACPDelegationCapacityError):
        await add("second")
    assert cleaned == ["second"]
    await registry.cancel("first", reason="test cleanup")
    assert cleaned == ["second", "first"]


@pytest.mark.asyncio
async def test_claim_checks_owner_and_is_atomic_single_consumer() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationOwnershipError,
        PendingACPDelegationRegistry,
        PendingACPDelegationStateError,
    )

    registry = PendingACPDelegationRegistry(capacity=2, ttl_seconds=30)
    future = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(_wait_forever())
    await registry.insert(
        pending_id="pending",
        thread_id="thread",
        agent_name="coder",
        request=_request(),
        owner=_Owner(),
        prompt_task=task,
        permission_future=future,
    )

    with pytest.raises(PendingACPDelegationOwnershipError):
        await registry.claim("pending", thread_id="other", agent_name="coder")

    outcomes = await asyncio.gather(
        registry.claim("pending", thread_id="thread", agent_name="coder"),
        registry.claim("pending", thread_id="thread", agent_name="coder"),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
    assert sum(isinstance(item, PendingACPDelegationStateError) for item in outcomes) == 1
    await registry.cancel("pending", reason="test cleanup")


@pytest.mark.asyncio
async def test_malformed_duplicate_cannot_cancel_claimed_resume_winner() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
        PendingACPDelegationStateError,
    )

    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=30)
    future = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(_wait_forever())
    await registry.insert(
        pending_id="pending",
        thread_id="thread",
        agent_name="coder",
        request=_request(),
        owner=_Owner(),
        prompt_task=task,
        permission_future=future,
    )
    await registry.claim("pending", thread_id="thread", agent_name="coder")

    with pytest.raises(PendingACPDelegationStateError):
        await registry.cancel_owned(
            "pending",
            thread_id="thread",
            agent_name="coder",
            reason="malformed duplicate",
        )

    assert not task.done()
    metadata = await registry.safe_metadata("pending")
    assert metadata is not None
    assert metadata.state == "resuming"
    await registry.cancel("pending", reason="test cleanup")


@pytest.mark.parametrize(
    ("approved", "options", "expected"),
    [
        (True, (("persistent", "Always", "allow_always"), ("once", "Once", "allow_once")), "once"),
        (True, (("persistent", "Always", "allow_always"),), None),
        (True, (("deny", "No", "reject_once"),), None),
        (False, (("always", "Never", "reject_always"), ("once", "No", "reject_once")), "once"),
        (False, (("always", "Never", "reject_always"),), None),
        (
            False,
            (("always-1", "Never A", "reject_always"), ("always-2", "Never B", "reject_always")),
            None,
        ),
        (None, (("once", "Once", "allow_once"),), None),
    ],
)
def test_permission_option_selection_is_exact_and_fail_closed(approved, options, expected) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.permissions import (
        PermissionOptionDTO,
        select_permission_option,
    )

    offered = tuple(PermissionOptionDTO(*option) for option in options)
    assert select_permission_option(offered, approved=approved) == expected


def test_safe_request_is_immutable_bounded_and_serializable() -> None:
    from dataclasses import FrozenInstanceError

    request = _request(title="x" * 2000, description="secret\x00" + "y" * 3000)
    dumped = asdict(request)
    assert len(request.title) <= 512
    assert len(request.description) <= 1024
    assert "\x00" not in repr(dumped)
    assert isinstance(request.created_at, str)
    assert datetime.fromisoformat(request.created_at).tzinfo == timezone.utc
    with pytest.raises(FrozenInstanceError):
        request.title = "changed"


@pytest.mark.asyncio
async def test_ttl_expiry_cancels_prompt_and_cleans_once() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )

    cleaned = 0

    def cleanup() -> None:
        nonlocal cleaned
        cleaned += 1

    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=0.01)
    future = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(_wait_forever())
    await registry.insert(
        pending_id="expiring",
        thread_id="thread",
        agent_name="coder",
        request=_request(),
        owner=_Owner(),
        prompt_task=task,
        permission_future=future,
        cleanup=cleanup,
    )
    await asyncio.sleep(0.03)
    assert task.cancelled()
    assert cleaned == 1
    assert await registry.safe_metadata("expiring") is None
    assert await registry.expire() == 0
    assert cleaned == 1


@pytest.mark.asyncio
async def test_ttl_expiry_cancels_a_claimed_but_stalled_resume() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )

    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=0.01)
    future = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(_wait_forever())
    await registry.insert(
        pending_id="stalled",
        thread_id="thread",
        agent_name="coder",
        request=_request(),
        owner=_Owner(),
        prompt_task=task,
        permission_future=future,
    )
    await registry.claim("stalled", thread_id="thread", agent_name="coder")

    await asyncio.sleep(0.03)

    assert task.cancelled()
    assert await registry.safe_metadata("stalled") is None


@pytest.mark.asyncio
async def test_complete_and_shutdown_cleanup_are_exactly_once() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )

    cleaned = []
    registry = PendingACPDelegationRegistry(capacity=3, ttl_seconds=30)
    for pending_id in ("completed", "shutdown"):
        future = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(_wait_forever())
        await registry.insert(
            pending_id=pending_id,
            thread_id="thread",
            agent_name="coder",
            request=_request(),
            owner=_Owner(),
            prompt_task=task,
            permission_future=future,
            cleanup=lambda pending_id=pending_id: cleaned.append(pending_id),
        )

    claim = await registry.claim("completed", thread_id="thread", agent_name="coder")
    claim.permission_future.set_result("allow-once")
    claim.prompt_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await claim.prompt_task
    await registry.complete("completed")
    await registry.complete("completed")
    await registry.aclose()
    await registry.aclose()
    assert sorted(cleaned) == ["completed", "shutdown"]
    assert await registry.size() == 0


@pytest.mark.asyncio
async def test_process_exit_removes_entry_and_runs_cleanup_once() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )

    cleaned = []
    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=30)
    future = asyncio.get_running_loop().create_future()

    async def prompt_exits() -> None:
        await asyncio.sleep(0)

    task = asyncio.create_task(prompt_exits())
    await registry.insert(
        pending_id="exited",
        thread_id="thread",
        agent_name="coder",
        request=_request(),
        owner=_Owner(),
        prompt_task=task,
        permission_future=future,
        cleanup=lambda: cleaned.append("exited"),
    )
    await task
    for _ in range(5):
        await asyncio.sleep(0)
        if cleaned:
            break
    assert await registry.safe_metadata("exited") is None
    assert cleaned == ["exited"]
    await registry.aclose()
    assert cleaned == ["exited"]


@pytest.mark.asyncio
async def test_registry_sanitizes_bounded_identities_and_rejects_missing_owner() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )

    registry = PendingACPDelegationRegistry(capacity=2, ttl_seconds=30)

    async def insert(*, pending_id: str, thread_id: str, agent_name: str):
        future = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(_wait_forever())
        await registry.insert(
            pending_id=pending_id,
            thread_id=thread_id,
            agent_name=agent_name,
            request=_request(),
            owner=_Owner(),
            prompt_task=task,
            permission_future=future,
        )
        return task

    missing_task = asyncio.create_task(_wait_forever())
    missing_future = asyncio.get_running_loop().create_future()
    with pytest.raises(ValueError, match="thread_id"):
        await registry.insert(
            pending_id="pending-missing",
            thread_id="",
            agent_name="coder",
            request=_request(),
            owner=_Owner(),
            prompt_task=missing_task,
            permission_future=missing_future,
        )
    assert missing_task.cancelled()

    task = await insert(
        pending_id="pending-safe",
        thread_id="  thread\x00" + "x" * 300,
        agent_name="  coder\x00" + "y" * 300,
    )
    metadata = await registry.safe_metadata("pending-safe")
    assert metadata is not None
    assert 0 < len(metadata.thread_id) <= 128
    assert 0 < len(metadata.agent_name) <= 128
    assert "\x00" not in metadata.thread_id
    assert "\x00" not in metadata.agent_name
    await registry.cancel_owned(
        "pending-safe",
        thread_id=metadata.thread_id,
        agent_name=metadata.agent_name,
        reason="test cleanup",
    )
    assert task.cancelled()


@pytest.mark.asyncio
async def test_short_sanitized_identities_remain_collision_resistant() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import safe_identity

    values = ("agent one", "agent  one", "agent\tone", "agent\x00one", "agent\x01one")
    identities = {safe_identity(value, field="agent_name") for value in values}

    assert len(identities) == len(values)
    assert all(0 < len(identity) <= 128 for identity in identities)
    assert all(identity.isprintable() for identity in identities)


@pytest.mark.asyncio
async def test_cancel_owned_requires_exact_agent_and_missing_agent_cannot_cancel_peer() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationOwnershipError,
        PendingACPDelegationRegistry,
    )

    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=30)
    future = asyncio.get_running_loop().create_future()
    prompt = asyncio.create_task(_wait_forever())
    await registry.insert(
        pending_id="pending",
        thread_id="shared-thread",
        agent_name="agent-a",
        request=_request(),
        owner=_Owner(),
        prompt_task=prompt,
        permission_future=future,
    )

    for agent_name in (None, "agent-b"):
        with pytest.raises((ValueError, PendingACPDelegationOwnershipError)):
            await registry.cancel_owned(
                "pending",
                thread_id="shared-thread",
                agent_name=agent_name,
                reason="tampered resume",
            )
        assert not prompt.done()
        assert await registry.safe_metadata("pending") is not None

    await registry.cancel("pending", reason="test cleanup")


@pytest.mark.asyncio
async def test_cancellation_during_cleanup_still_finishes_exactly_once() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )

    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()
    cleaned = 0

    async def cleanup() -> None:
        nonlocal cleaned
        cleanup_started.set()
        await finish_cleanup.wait()
        cleaned += 1

    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=30)
    future = asyncio.get_running_loop().create_future()
    prompt = asyncio.create_task(_wait_forever())
    await registry.insert(
        pending_id="cleanup-cancel",
        thread_id="thread",
        agent_name="coder",
        request=_request(),
        owner=_Owner(),
        prompt_task=prompt,
        permission_future=future,
        cleanup=cleanup,
    )
    cancellation = asyncio.create_task(registry.cancel("cleanup-cancel", reason="test"))
    await cleanup_started.wait()
    cancellation.cancel()
    finish_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await cancellation
    await registry.aclose()

    assert cleaned == 1
    assert await registry.size() == 0


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_suppress_finalization_or_other_shutdown_cleanup() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )

    finalized = []
    cleaned = []
    registry = PendingACPDelegationRegistry(capacity=2, ttl_seconds=30)
    for pending_id in ("broken", "healthy"):
        future = asyncio.get_running_loop().create_future()
        prompt = asyncio.create_task(_wait_forever())

        async def cleanup(current=pending_id) -> None:
            cleaned.append(current)
            if current == "broken":
                raise RuntimeError("cleanup failed")

        await registry.insert(
            pending_id=pending_id,
            thread_id="thread",
            agent_name="coder",
            request=_request(),
            owner=_Owner(),
            prompt_task=prompt,
            permission_future=future,
            cleanup=cleanup,
            finalizer=lambda outcome, current=pending_id: finalized.append((current, outcome)),
        )

    await registry.aclose()

    assert sorted(cleaned) == ["broken", "healthy"]
    assert sorted(item[0] for item in finalized) == ["broken", "healthy"]
    assert await registry.size() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["expiry", "process_exit", "shutdown"])
async def test_terminal_registry_cleanup_finalizes_exactly_once(terminal: str) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )

    finalized = []
    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=0.01)
    future = asyncio.get_running_loop().create_future()
    prompt = asyncio.create_task(_wait_forever())
    await registry.insert(
        pending_id="terminal",
        thread_id="thread",
        agent_name="coder",
        request=_request(),
        owner=_Owner(),
        prompt_task=prompt,
        permission_future=future,
        finalizer=finalized.append,
    )

    if terminal == "expiry":
        await asyncio.sleep(0.03)
    elif terminal == "process_exit":
        prompt.cancel()
        with pytest.raises(asyncio.CancelledError):
            await prompt
        for _ in range(5):
            await asyncio.sleep(0)
            if finalized:
                break
    else:
        await registry.aclose()

    await registry.aclose()
    assert len(finalized) == 1


@pytest.mark.asyncio
async def test_manual_expiry_cancels_and_drains_redundant_expiry_task(monkeypatch) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client import pending

    now = 100.0
    monkeypatch.setattr(pending, "monotonic", lambda: now)
    registry = pending.PendingACPDelegationRegistry(capacity=1, ttl_seconds=30)
    future = asyncio.get_running_loop().create_future()
    prompt = asyncio.create_task(_wait_forever())
    await registry.insert(
        pending_id="manual-expiry",
        thread_id="thread",
        agent_name="coder",
        request=_request(),
        owner=_Owner(),
        prompt_task=prompt,
        permission_future=future,
    )
    scheduled = registry._entries["manual-expiry"].expiry_task
    assert scheduled is not None

    now = 131.0
    assert await registry.expire() == 1
    assert scheduled.done()
    assert scheduled.cancelled()
    assert prompt.cancelled()
    assert await registry.expire() == 0
    await registry.aclose()
