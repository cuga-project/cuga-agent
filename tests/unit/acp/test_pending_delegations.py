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


@pytest.mark.parametrize(
    ("approved", "options", "expected"),
    [
        (True, (("persistent", "Always", "allow_always"), ("once", "Once", "allow_once")), "once"),
        (True, (("persistent", "Always", "allow_always"),), "persistent"),
        (True, (("deny", "No", "reject_once"),), None),
        (False, (("always", "Never", "reject_always"), ("once", "No", "reject_once")), "once"),
        (False, (("always", "Never", "reject_always"),), "always"),
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
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert await registry.safe_metadata("exited") is None
    assert cleaned == ["exited"]
    await registry.aclose()
    assert cleaned == ["exited"]
