"""Temporary early SDK diagnostic; remove after live validation."""

import time
import traceback

import pytest

from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter

from cuga.sdk_core.tests.test_supervisor_policies import (
    TestSupervisorPolicyE2E as _LiveSupervisorPolicies,
    clean_policy_storage,  # noqa: F401 - imported autouse fixture
)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_onboarding_live_diagnostic(capsys, monkeypatch):
    invoke_model = SupervisorGraphAdapter.ainvoke_model

    def report(message):
        with capsys.disabled():
            print(f"[SDK-live-diagnostic] {message}", flush=True)

    async def trace_model(adapter, bound, messages, invoke_config):
        started = time.monotonic()
        report("supervisor model request started")
        try:
            response = await invoke_model(adapter, bound, messages, invoke_config)
            report(f"supervisor model response: {response.content!r}")
            return response
        finally:
            report(f"supervisor model request ended after {time.monotonic() - started:.1f}s")

    monkeypatch.setattr(SupervisorGraphAdapter, "ainvoke_model", trace_model)
    report("starting onboarding")
    try:
        await _LiveSupervisorPolicies().test_e2e_playbook_orchestrates_sub_agents()
    except Exception:
        with capsys.disabled():
            traceback.print_exc()
        raise
    report("onboarding passed")
