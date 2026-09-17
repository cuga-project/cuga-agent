"""Temporary early SDK diagnostic; remove after live validation."""

import pytest

from cuga.sdk_core.tests.test_supervisor_policies import (
    TestSupervisorPolicyE2E as _LiveSupervisorPolicies,
    clean_policy_storage,  # noqa: F401 - imported autouse fixture
)


@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", range(3))
async def test_onboarding_live_diagnostic(attempt, capsys):
    with capsys.disabled():
        print(f"[SDK-live-diagnostic] attempt {attempt}: starting onboarding", flush=True)
    await _LiveSupervisorPolicies().test_e2e_playbook_orchestrates_sub_agents()
    with capsys.disabled():
        print(f"[SDK-live-diagnostic] attempt {attempt}: onboarding passed", flush=True)
