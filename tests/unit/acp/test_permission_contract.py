"""End-to-end permission contracts across a real outbound ACP process."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[3]
FAKE_AGENT = ROOT / "tests" / "fixtures" / "acp" / "fake_agent.py"


@pytest.fixture(autouse=True)
def _workspace_root(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem import paths

    monkeypatch.setattr(paths, "local_base_dir", lambda: ROOT)


def _config(scenario: str = "permission"):
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.config import ACPProcessConfig

    return ACPProcessConfig(
        command=sys.executable,
        args=(str(FAKE_AGENT), "--scenario", scenario),
        cwd=ROOT,
        startup_timeout=2,
        prompt_timeout=5,
        shutdown_grace_period=0.5,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approved", "expected"),
    [(True, "permission-allowed"), (False, "permission-denied")],
)
async def test_real_permission_pause_resumes_same_process_once(approved: bool, expected: str) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationError,
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import (
        ACPPermissionPause,
        ACPPermissionRuntimeBridge,
        delegate_task_via_acp,
        resume_acp_delegation,
    )

    registry = PendingACPDelegationRegistry(capacity=2, ttl_seconds=10)
    try:
        bridge = ACPPermissionRuntimeBridge(
            registry=registry,
            thread_id="contract-thread",
            agent_name="fixture-agent",
            interactive=True,
        )

        with pytest.raises(ACPPermissionPause) as raised:
            await delegate_task_via_acp(
                config=_config(),
                task="perform fixture operation",
                permission_bridge=bridge,
            )
        pause = raised.value
        assert pause.request.tool_call_id == "fixture-operation"
        assert [option.kind for option in pause.request.options] == ["allow_once", "reject_once"]
        assert await registry.size() == 1

        result = await resume_acp_delegation(
            registry=registry,
            pending_id=pause.pending_id,
            thread_id="contract-thread",
            agent_name="fixture-agent",
            approved=approved,
        )

        assert result == {"result": expected, "status": "success", "variables": {}}
        assert await registry.size() == 0
        with pytest.raises(PendingACPDelegationError):
            await resume_acp_delegation(
                registry=registry,
                pending_id=pause.pending_id,
                thread_id="contract-thread",
                agent_name="fixture-agent",
                approved=approved,
            )
    finally:
        await registry.aclose()


@pytest.mark.asyncio
async def test_real_sequential_permissions_each_pause_and_resume_same_process() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import (
        ACPPermissionPause,
        ACPPermissionRuntimeBridge,
        delegate_task_via_acp,
        resume_acp_delegation,
    )

    registry = PendingACPDelegationRegistry(capacity=2, ttl_seconds=10)
    try:
        bridge = ACPPermissionRuntimeBridge(
            registry=registry,
            thread_id="contract-thread",
            agent_name="fixture-agent",
            interactive=True,
        )
        with pytest.raises(ACPPermissionPause) as first:
            await delegate_task_via_acp(
                config=_config("permission-twice"),
                task="perform fixture operations",
                permission_bridge=bridge,
            )
        assert first.value.request.tool_call_id == "fixture-operation-1"

        with pytest.raises(ACPPermissionPause) as second:
            await resume_acp_delegation(
                registry=registry,
                pending_id=first.value.pending_id,
                thread_id="contract-thread",
                agent_name="fixture-agent",
                approved=True,
            )
        assert second.value.request.tool_call_id == "fixture-operation-2"
        assert second.value.pending_id != first.value.pending_id
        assert await registry.size() == 1

        result = await resume_acp_delegation(
            registry=registry,
            pending_id=second.value.pending_id,
            thread_id="contract-thread",
            agent_name="fixture-agent",
            approved=False,
        )
        assert result == {"result": "first=allowed;second=denied", "status": "success", "variables": {}}
        assert await registry.size() == 0
    finally:
        await registry.aclose()


@pytest.mark.asyncio
async def test_headless_permission_request_fails_closed_and_reaps_process() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import delegate_task_via_acp

    result = await delegate_task_via_acp(config=_config(), task="perform fixture operation")

    assert result == {
        "result": "ACP agent requires permission to continue.",
        "status": "failed",
        "variables": {},
    }
