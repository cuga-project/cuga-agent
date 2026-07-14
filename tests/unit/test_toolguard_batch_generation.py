import json

import pytest

from cuga.backend.cuga_graph.policy.models import AlwaysTrigger, Playbook, ToolGuide
from cuga.backend.cuga_graph.policy.utils import extract_policies_data_from_json
from cuga.backend.server import tool_guard_generation
from cuga.backend.server.tool_guard_generation import generate_tool_guards_for_policies


def _write_json(tmp_path, payload):
    path = tmp_path / "policies.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.unit
def test_extract_policies_data_from_frontend_export(tmp_path):
    path = _write_json(
        tmp_path,
        {
            "enablePolicies": False,
            "policies": [
                {"id": "guide_one", "policy_type": "tool_guide", "name": "Guide One"},
                {"id": "approval_one", "policy_type": "tool_approval", "name": "Approval One"},
            ],
        },
    )

    result = extract_policies_data_from_json(str(path))

    assert result == {
        "enabled": False,
        "policies": [
            {"id": "guide_one", "policy_type": "tool_guide", "name": "Guide One"},
            {"id": "approval_one", "policy_type": "tool_approval", "name": "Approval One"},
        ],
        "policy_ids": ["guide_one", "approval_one"],
        "errors": [],
    }


@pytest.mark.unit
def test_extract_policies_data_from_array(tmp_path):
    path = _write_json(
        tmp_path,
        [
            {"id": "guide_one", "policy_type": "tool_guide", "name": "Guide One"},
            {"id": "guide_two", "policy_type": "tool_guide", "name": "Guide Two"},
        ],
    )

    result = extract_policies_data_from_json(str(path))

    assert result["enabled"] is True
    assert result["policies"] == [
        {"id": "guide_one", "policy_type": "tool_guide", "name": "Guide One"},
        {"id": "guide_two", "policy_type": "tool_guide", "name": "Guide Two"},
    ]
    assert result["policy_ids"] == ["guide_one", "guide_two"]
    assert result["errors"] == []


@pytest.mark.unit
def test_extract_policies_data_from_single_object(tmp_path):
    path = _write_json(
        tmp_path,
        {"id": "single_guide", "policy_type": "tool_guide", "name": "Single Guide"},
    )

    result = extract_policies_data_from_json(str(path))

    assert result["enabled"] is True
    assert result["policies"] == [{"id": "single_guide", "policy_type": "tool_guide", "name": "Single Guide"}]
    assert result["policy_ids"] == ["single_guide"]
    assert result["errors"] == []


@pytest.mark.unit
def test_extract_policies_data_reports_missing_policy_id(tmp_path):
    path = _write_json(
        tmp_path,
        [
            {"id": "valid_guide", "policy_type": "tool_guide", "name": "Valid Guide"},
            {"policy_type": "tool_guide", "name": "Missing ID"},
        ],
    )

    result = extract_policies_data_from_json(str(path))

    assert result["policy_ids"] == ["valid_guide"]
    assert result["errors"] == ["Policy at index 1 is missing required field 'id'"]


# ---------------------------------------------------------------------------
# Batch generation helper tests
# ---------------------------------------------------------------------------


class _FakeStorage:
    def __init__(self, policies):
        self._policies = {policy.id: policy for policy in policies}

    async def get_policy(self, policy_id):
        return self._policies.get(policy_id)


class _FakePolicySystem:
    def __init__(self, policies):
        self.storage = _FakeStorage(policies)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_batch_generates_only_requested_eligible_policy_ids(monkeypatch):
    eligible = ToolGuide(
        id="imported_guide",
        name="Imported Guide",
        description="Generate this guide",
        triggers=[AlwaysTrigger()],
        target_tools=["send_email"],
        guide_content="Only send approved emails.",
        enabled=True,
    )
    unrelated = ToolGuide(
        id="unrelated_guide",
        name="Unrelated Guide",
        description="Do not generate this guide",
        triggers=[AlwaysTrigger()],
        target_tools=["delete_record"],
        guide_content="Do not delete records.",
        enabled=True,
    )
    policy_system = _FakePolicySystem([eligible, unrelated])
    called_policy_ids = []

    async def fake_generate_tool_guards_for_policy(*, policy_system, policy_id, generation_agent):
        called_policy_ids.append(policy_id)
        return {
            "status": "ok",
            "policy_id": policy_id,
            "results": [{"tool": "send_email", "status": "ok"}],
        }

    monkeypatch.setattr(
        tool_guard_generation,
        "generate_tool_guards_for_policy",
        fake_generate_tool_guards_for_policy,
    )

    result = await generate_tool_guards_for_policies(
        policy_system=policy_system,
        policy_ids=["imported_guide"],
        generation_agent=object(),
    )

    assert called_policy_ids == ["imported_guide"]
    assert result["status"] == "ok"
    assert result["generated"] == {
        "imported_guide": {
            "status": "ok",
            "policy_id": "imported_guide",
            "results": [{"tool": "send_email", "status": "ok"}],
        }
    }
    assert result["skipped"] == []
    assert result["errors"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_batch_skips_ineligible_imported_policies(monkeypatch):
    disabled = ToolGuide(
        id="disabled_guide",
        name="Disabled Guide",
        description="Disabled",
        triggers=[AlwaysTrigger()],
        target_tools=["send_email"],
        guide_content="Disabled guide.",
        enabled=False,
    )
    wildcard = ToolGuide(
        id="wildcard_guide",
        name="Wildcard Guide",
        description="Wildcard",
        triggers=[AlwaysTrigger()],
        target_tools=["*"],
        guide_content="Wildcard guide.",
        enabled=True,
    )
    playbook = Playbook(
        id="playbook_one",
        name="Playbook One",
        description="Not a tool guide",
        triggers=[AlwaysTrigger()],
        markdown_content="# Steps",
        enabled=True,
    )
    policy_system = _FakePolicySystem([disabled, wildcard, playbook])

    async def fake_generate_tool_guards_for_policy(*, policy_system, policy_id, generation_agent):
        raise AssertionError("ineligible policies must not generate")

    monkeypatch.setattr(
        tool_guard_generation,
        "generate_tool_guards_for_policy",
        fake_generate_tool_guards_for_policy,
    )

    result = await generate_tool_guards_for_policies(
        policy_system=policy_system,
        policy_ids=["disabled_guide", "wildcard_guide", "playbook_one", "missing_policy"],
        generation_agent=object(),
    )

    assert result["status"] == "partial"
    assert result["generated"] == {}
    assert {(s["policy_id"], s["reason"]) for s in result["skipped"]} == {
        ("disabled_guide", "disabled"),
        ("wildcard_guide", "no_concrete_target_tools"),
        ("playbook_one", "not_tool_guide"),
        ("missing_policy", "missing"),
    }
    assert result["errors"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_batch_continues_after_policy_generation_failure(monkeypatch):
    failing = ToolGuide(
        id="failing_guide",
        name="Failing Guide",
        description="Fails",
        triggers=[AlwaysTrigger()],
        target_tools=["send_email"],
        guide_content="Failing guide.",
        enabled=True,
    )
    succeeding = ToolGuide(
        id="succeeding_guide",
        name="Succeeding Guide",
        description="Succeeds",
        triggers=[AlwaysTrigger()],
        target_tools=["read_data"],
        guide_content="Succeeding guide.",
        enabled=True,
    )
    policy_system = _FakePolicySystem([failing, succeeding])

    async def fake_generate_tool_guards_for_policy(*, policy_system, policy_id, generation_agent):
        if policy_id == "failing_guide":
            raise RuntimeError("synthetic generation failure")
        return {
            "status": "ok",
            "policy_id": policy_id,
            "results": [{"tool": "read_data", "status": "ok"}],
        }

    monkeypatch.setattr(
        tool_guard_generation,
        "generate_tool_guards_for_policy",
        fake_generate_tool_guards_for_policy,
    )

    result = await generate_tool_guards_for_policies(
        policy_system=policy_system,
        policy_ids=["failing_guide", "succeeding_guide"],
        generation_agent=object(),
    )

    assert result["status"] == "partial"
    assert result["generated"]["failing_guide"]["status"] == "error"
    assert result["generated"]["failing_guide"]["message"] == "synthetic generation failure"
    assert result["generated"]["succeeding_guide"] == {
        "status": "ok",
        "policy_id": "succeeding_guide",
        "results": [{"tool": "read_data", "status": "ok"}],
    }
    assert result["skipped"] == []
    assert result["errors"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_batch_status_is_partial_when_generation_succeeds_with_skips(monkeypatch):
    eligible = ToolGuide(
        id="eligible_guide",
        name="Eligible Guide",
        description="Generate this guide",
        triggers=[AlwaysTrigger()],
        target_tools=["send_email"],
        guide_content="Eligible guide.",
        enabled=True,
    )
    disabled = ToolGuide(
        id="disabled_guide",
        name="Disabled Guide",
        description="Skip this guide",
        triggers=[AlwaysTrigger()],
        target_tools=["send_email"],
        guide_content="Disabled guide.",
        enabled=False,
    )
    policy_system = _FakePolicySystem([eligible, disabled])

    async def fake_generate_tool_guards_for_policy(*, policy_system, policy_id, generation_agent):
        return {
            "status": "ok",
            "policy_id": policy_id,
            "results": [{"tool": "send_email", "status": "ok"}],
        }

    monkeypatch.setattr(
        tool_guard_generation,
        "generate_tool_guards_for_policy",
        fake_generate_tool_guards_for_policy,
    )

    result = await generate_tool_guards_for_policies(
        policy_system=policy_system,
        policy_ids=["eligible_guide", "disabled_guide"],
        generation_agent=object(),
    )

    assert result["status"] == "partial"
    assert result["generated"]["eligible_guide"]["status"] == "ok"
    assert result["skipped"] == [{"policy_id": "disabled_guide", "reason": "disabled"}]
