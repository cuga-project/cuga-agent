import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import tool

from cuga.backend.cuga_graph.policy import folder_loader
from cuga.backend.cuga_graph.policy.filesystem_sync import PolicyFilesystemSync
from cuga.backend.cuga_graph.policy.models import (
    IntentGuard,
    IntentGuardResponse,
    KeywordTrigger,
)
from cuga.sdk import CugaAgent

pytestmark = pytest.mark.unit


class _FakeStorage:
    def __init__(self):
        self._policies = {}
        self.embedding_generations = 0

    async def add_policy(self, policy, embedding=None):
        if embedding is None:
            self.embedding_generations += 1
        self._policies[policy.id] = policy

    async def list_policies(self, enabled_only=True):
        return list(self._policies.values())

    async def delete_policy(self, id):
        self._policies.pop(id, None)

    async def get_policy(self, policy_id):
        return self._policies.get(policy_id)

    async def update_policy(self, policy):
        self._policies[policy.id] = policy


class _FakePolicyConfigurable:
    instances: list = []

    def __init__(self, *a, **k):
        self.storage = _FakeStorage()
        type(self).instances.append(self)

    async def initialize(self, *a, **k):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def _reset_instances():
    _FakePolicyConfigurable.instances.clear()
    yield
    _FakePolicyConfigurable.instances.clear()


@pytest.fixture(autouse=True)
def fake_policy_configurable():
    with patch(
        "cuga.backend.cuga_graph.policy.configurable.PolicyConfigurable",
        _FakePolicyConfigurable,
    ):
        yield


@pytest.fixture
def cuga_folder(tmp_path):
    folder = tmp_path / ".cuga"
    folder.mkdir()
    sync = PolicyFilesystemSync(cuga_folder=str(folder))
    seed1 = IntentGuard(
        id="seed_1",
        name="Seed Policy 1",
        description="Seed policy 1",
        triggers=[KeywordTrigger(value=["block1"])],
        response=IntentGuardResponse(response_type="natural_language", content="blocked"),
    )
    seed2 = IntentGuard(
        id="seed_2",
        name="Seed Policy 2",
        description="Seed policy 2",
        triggers=[KeywordTrigger(value=["block2"])],
        response=IntentGuardResponse(response_type="natural_language", content="blocked"),
    )
    sync.save_policy_to_file(seed1)
    sync.save_policy_to_file(seed2)
    return str(folder)


@pytest.fixture
def load_calls(monkeypatch):
    counter = {"count": 0}
    real_fn = folder_loader.load_policies_from_folder

    async def counting_wrapper(*args, **kwargs):
        counter["count"] += 1
        return await real_fn(*args, **kwargs)

    monkeypatch.setattr(folder_loader, "load_policies_from_folder", counting_wrapper)
    yield counter


@tool
def noop_tool(x: str) -> str:
    """No-op tool for testing"""
    return x


def _make_agent(cuga_folder_path, **kwargs):
    # Pass model=MagicMock() to avoid the LLM manager initialization, which
    # can fail when other tests have polluted settings with provider credentials.
    return CugaAgent(
        tools=[noop_tool],
        model=MagicMock(),
        cuga_folder=cuga_folder_path,
        auto_load_policies=True,
        filesystem_sync=True,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_sequential_adds_auto_load_folder_only_once(cuga_folder, load_calls):
    agent = _make_agent(cuga_folder)
    for i in range(4):
        await agent.policies.add_intent_guard(
            policy_id=f"guard_{i}",
            name=f"Guard {i}",
            keywords=[f"kw{i}"],
            response="blocked",
        )
    assert load_calls["count"] == 1
    assert len(agent._policy_system.storage._policies) == 6
    assert agent._policy_system.storage.embedding_generations == 6


@pytest.mark.asyncio
async def test_concurrent_ensure_initializes_and_auto_loads_once(cuga_folder, load_calls):
    agent = _make_agent(cuga_folder)
    results = await asyncio.gather(*[agent.policies._ensure_policy_system() for _ in range(5)])
    assert len(_FakePolicyConfigurable.instances) == 1
    assert all(r is results[0] for r in results)
    assert load_calls["count"] == 1
    assert agent._policy_system.storage.embedding_generations == 2


@pytest.mark.asyncio
async def test_explicit_reload_and_sync_bypass_latch(cuga_folder, load_calls):
    agent = _make_agent(cuga_folder)
    await agent.policies.add_intent_guard(
        policy_id="guard_1",
        name="Guard 1",
        keywords=["kw1"],
        response="blocked",
    )
    assert load_calls["count"] == 1

    await agent.policies.load_from_folder(cuga_folder)
    assert load_calls["count"] == 2

    await agent.policies.sync_from_filesystem()
    assert load_calls["count"] == 3


@pytest.mark.asyncio
async def test_failed_auto_load_is_retried_on_next_call(cuga_folder, monkeypatch):
    # Use a standalone counter so double-patching via the load_calls fixture is avoided.
    from cuga.backend.cuga_graph.policy import folder_loader as _fl

    real_fn = _fl.load_policies_from_folder
    call_counter = {"count": 0}
    first = True

    async def failing_wrapper(*args, **kwargs):
        nonlocal first
        call_counter["count"] += 1
        if first:
            first = False
            raise RuntimeError("load failed")
        return await real_fn(*args, **kwargs)

    monkeypatch.setattr(_fl, "load_policies_from_folder", failing_wrapper)

    agent = _make_agent(cuga_folder)
    with pytest.raises(RuntimeError):
        await agent.policies._ensure_policy_system()

    # Latch not set (load failed) → second call retries and succeeds → sets latch
    await agent.policies._ensure_policy_system()
    # Latch is now set → third call skips the load entirely
    await agent.policies._ensure_policy_system()

    assert call_counter["count"] == 2


@pytest.mark.asyncio
async def test_storage_error_is_retried_on_next_call(cuga_folder, load_calls):
    """Storage errors (e.g. embedding outage) are transient: the loader catches them
    and returns them in storage_errors. The latch must stay unset so the next call
    retries. Patches _FakeStorage.add_policy at the class level before the agent is
    created, so the raising behaviour applies from the very first store call."""
    first_store = True
    original_class_add = _FakeStorage.add_policy

    async def raising_add(self_storage, policy, embedding=None):
        nonlocal first_store
        if first_store:
            first_store = False
            raise RuntimeError("Embedding service unavailable")
        await original_class_add(self_storage, policy, embedding=embedding)

    _FakeStorage.add_policy = raising_add
    try:
        agent = _make_agent(cuga_folder)

        # First call: storage raises for the first policy → storage_errors non-empty →
        # latch must NOT be set
        await agent.policies._ensure_policy_system()
        assert load_calls["count"] == 1
        assert agent.policies._auto_load_done is False, "latch must stay unset after storage error"

        # Second call: all policies store cleanly → latch IS set
        await agent.policies._ensure_policy_system()
        assert load_calls["count"] == 2
        assert agent.policies._auto_load_done is True

        # Third call: latch set → loader skipped
        await agent.policies._ensure_policy_system()
        assert load_calls["count"] == 2

        # Seeds present in storage after the clean second load
        assert "seed_1" in agent._policy_system.storage._policies
        assert "seed_2" in agent._policy_system.storage._policies
    finally:
        _FakeStorage.add_policy = original_class_add


@pytest.mark.asyncio
async def test_reconciliation_error_is_retried_on_next_call(cuga_folder, load_calls, monkeypatch):
    real_get_ids = PolicyFilesystemSync.get_filesystem_policy_ids
    sync_calls = 0

    def fail_once(self):
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 1:
            raise OSError("Filesystem temporarily unavailable")
        return real_get_ids(self)

    monkeypatch.setattr(PolicyFilesystemSync, "get_filesystem_policy_ids", fail_once)
    agent = _make_agent(cuga_folder)

    await agent.policies._ensure_policy_system()
    assert sync_calls == 1
    assert agent.policies._auto_load_done is False

    await agent.policies._ensure_policy_system()
    assert sync_calls == 2
    assert agent.policies._auto_load_done is True
    assert set(agent._policy_system.storage._policies) == {"seed_1", "seed_2"}

    await agent.policies._ensure_policy_system()
    assert sync_calls == 2
    assert load_calls["count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("read_error", [BlockingIOError, PermissionError])
async def test_file_read_error_is_retried_on_next_call(cuga_folder, load_calls, monkeypatch, read_error):
    real_open = open
    read_attempts = 0

    def fail_once(file_path, *args, **kwargs):
        nonlocal read_attempts
        if os.path.basename(file_path) == "seed_1.md":
            read_attempts += 1
            if read_attempts == 1:
                raise read_error("File temporarily unavailable")
        return real_open(file_path, *args, **kwargs)

    monkeypatch.setattr(folder_loader, "open", fail_once, raising=False)
    agent = _make_agent(cuga_folder)

    await agent.policies._ensure_policy_system()
    assert agent.policies._auto_load_done is False
    assert set(agent._policy_system.storage._policies) == {"seed_2"}

    await agent.policies._ensure_policy_system()
    assert agent.policies._auto_load_done is True
    assert set(agent._policy_system.storage._policies) == {"seed_1", "seed_2"}

    await agent.policies._ensure_policy_system()
    assert read_attempts == 2
    assert load_calls["count"] == 2


@pytest.mark.asyncio
async def test_permanent_file_error_latches_and_does_not_retry(cuga_folder, load_calls):
    """Parse errors (bad frontmatter, unknown type) are permanent. The latch IS set
    after the first load so the reload loop from #804 does not come back."""
    import os

    # Write a broken policy file with an unknown type
    broken_path = os.path.join(cuga_folder, "playbooks", "broken.md")
    os.makedirs(os.path.dirname(broken_path), exist_ok=True)
    with open(broken_path, "w") as f:
        f.write("---\nname: Broken Policy\ntype: not_a_type\n---\n\nContent.\n")

    agent = _make_agent(cuga_folder)

    # First call: broken file causes a parse error but load_from_folder does not raise.
    # No storage_errors → latch IS set despite the file error.
    await agent.policies._ensure_policy_system()
    assert load_calls["count"] == 1
    assert agent.policies._auto_load_done is True, "latch must be set even with a bad file"

    # Second and third calls: latch held → loader is not called again
    await agent.policies._ensure_policy_system()
    await agent.policies._ensure_policy_system()
    assert load_calls["count"] == 1

    # The two valid seeds are still in storage
    assert "seed_1" in agent._policy_system.storage._policies
    assert "seed_2" in agent._policy_system.storage._policies


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["load_from_json", "generate_tool_guards_from_json"])
async def test_json_import_persists_to_filesystem_and_survives_restart(
    cuga_folder, tmp_path, monkeypatch, method
):
    """With the auto-load latch, no later call re-syncs storage to .cuga, so JSON imports
    must write their files directly; otherwise the next start drops them from storage."""
    import json

    from cuga.backend.server import tool_guard_generation

    async def fake_generate(*, policy_system, policy_ids, generation_agent):
        return {"status": "ok", "generated": {}, "skipped": [], "errors": []}

    monkeypatch.setattr(tool_guard_generation, "generate_tool_guards_for_policies", fake_generate)
    monkeypatch.setattr(tool_guard_generation, "build_tool_guard_generation_agent", lambda **_: None)

    json_path = tmp_path / "policies.json"
    json_path.write_text(
        json.dumps(
            {
                "policies": [
                    {
                        "id": "imported_guide",
                        "policy_type": "tool_guide",
                        "name": "Imported Guide",
                        "description": "Imported guide",
                        "triggers": [{"type": "always"}],
                        "target_tools": ["noop_tool"],
                        "guide_content": "Imported content.",
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    agent = _make_agent(cuga_folder)
    await getattr(agent.policies, method)(str(json_path))
    assert "imported_guide" in agent._policy_system.storage._policies
    assert os.path.exists(os.path.join(cuga_folder, "tool_guides", "imported_guide.md"))

    restarted = _make_agent(cuga_folder)
    await restarted.policies._ensure_policy_system()
    assert "imported_guide" in restarted._policy_system.storage._policies


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_generated_guards_survive_reload_and_restart(cuga_folder, tmp_path, monkeypatch, partial):
    import json

    from cuga.sdk import PoliciesManager

    async def generate_examples(self, policy_id, target_tool):
        return ["forbidden input"], ["allowed input"]

    async def generate_code(self, policy_id, target_tool):
        if partial and policy_id == "second_guide":
            raise RuntimeError("Generation temporarily unavailable")
        return "def check(input):\n    return True"

    # Exercise the real batch workflow and update_tool_guard; only generation is mocked.
    monkeypatch.setattr(PoliciesManager, "generate_tool_guard_examples", generate_examples)
    monkeypatch.setattr(PoliciesManager, "generate_tool_guard_code", generate_code)
    json_path = tmp_path / "guides.json"
    json_path.write_text(
        json.dumps(
            {
                "policies": [
                    {
                        "id": policy_id,
                        "policy_type": "tool_guide",
                        "name": policy_id,
                        "description": "Test guide",
                        "triggers": [{"type": "always"}],
                        "target_tools": ["noop_tool"],
                        "guide_content": "Only allow approved input.",
                        "enabled": True,
                    }
                    for policy_id in ["first_guide", "second_guide"]
                ]
            }
        ),
        encoding="utf-8",
    )

    agent = _make_agent(cuga_folder)
    result = await agent.policies.generate_tool_guards_from_json(str(json_path))
    assert result["status"] == ("partial" if partial else "ok")
    expected = {
        policy_id: (await agent._policy_system.storage.get_policy(policy_id)).tool_guards
        for policy_id in ["first_guide", "second_guide"]
    }
    assert expected["first_guide"]["noop_tool"].policy_code == "def check(input):\n    return True"
    for guards in expected.values():
        assert guards["noop_tool"].violating_examples == ["forbidden input"]
        assert guards["noop_tool"].compliance_examples == ["allowed input"]

    await agent.policies.load_from_folder(cuga_folder)
    restarted = _make_agent(cuga_folder)
    await restarted.policies._ensure_policy_system()
    for current in [agent, restarted]:
        for policy_id, guards in expected.items():
            reloaded = await current._policy_system.storage.get_policy(policy_id)
            assert reloaded.tool_guards == guards


@pytest.mark.asyncio
async def test_missing_folder_is_not_latched(tmp_path, load_calls):
    missing_folder = str(tmp_path / "does_not_exist")
    agent = _make_agent(missing_folder)

    await agent.policies._ensure_policy_system()
    assert load_calls["count"] == 0

    os.makedirs(os.path.join(missing_folder, "intent_guards"), exist_ok=True)
    sync = PolicyFilesystemSync(cuga_folder=missing_folder)
    seed = IntentGuard(
        id="seed_new",
        name="New Seed",
        description="New seed policy",
        triggers=[KeywordTrigger(value=["block_new"])],
        response=IntentGuardResponse(response_type="natural_language", content="blocked"),
    )
    sync.save_policy_to_file(seed)

    await agent.policies._ensure_policy_system()
    assert load_calls["count"] == 1
    assert "seed_new" in agent._policy_system.storage._policies


@pytest.mark.asyncio
async def test_auto_load_disabled_never_loads(cuga_folder, load_calls):
    agent = CugaAgent(
        tools=[noop_tool],
        model=MagicMock(),
        cuga_folder=cuga_folder,
        auto_load_policies=False,
        filesystem_sync=False,
    )
    for i in range(3):
        await agent.policies.add_intent_guard(
            policy_id=f"guard_{i}",
            name=f"Guard {i}",
            keywords=[f"kw{i}"],
            response="blocked",
        )
    assert load_calls["count"] == 0


def _intent_guard(policy_id):
    return IntentGuard(
        id=policy_id,
        name=f"Guard {policy_id}",
        description=f"Guard {policy_id}",
        triggers=[KeywordTrigger(value=["kw"])],
        response=IntentGuardResponse(response_type="natural_language", content="blocked"),
    )


@pytest.mark.parametrize("bad_id", ["../escaped", "../../escaped", "nested/escaped"])
def test_save_policy_rejects_ids_outside_subfolder(tmp_path, bad_id):
    """Policy IDs come from caller-supplied JSON; they must not write outside .cuga/<subfolder>."""
    folder = tmp_path / ".cuga"
    sync = PolicyFilesystemSync(cuga_folder=str(folder))

    with pytest.raises(ValueError, match="resolves outside"):
        sync.save_policy_to_file(_intent_guard(bad_id))

    written = [p for p in tmp_path.rglob("*.md")]
    assert written == []


def test_delete_policy_file_matches_exact_id_only(tmp_path):
    folder = tmp_path / ".cuga"
    PolicyFilesystemSync(cuga_folder=str(folder)).save_policy_to_file(_intent_guard("foo_bar"))

    # Fresh instance -> no cached path, so the lookup falls back to scanning the folder.
    assert PolicyFilesystemSync(cuga_folder=str(folder)).delete_policy_file("foo") is False
    assert (folder / "intent_guards" / "foo_bar.md").exists()


def _write_json(path, policy_ids):
    import json

    path.write_text(
        json.dumps(
            {
                "policies": [
                    {
                        "id": pid,
                        "policy_type": "intent_guard",
                        "name": f"Guard {pid}",
                        "description": f"Guard {pid}",
                        "triggers": [{"type": "keyword", "value": ["kw"]}],
                        "response": {"response_type": "natural_language", "content": "blocked"},
                        "enabled": True,
                    }
                    for pid in policy_ids
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_load_from_json_clear_existing_prunes_stale_files_across_restart(cuga_folder, tmp_path):
    """A replacement import must not be undone by the next start's folder auto-load."""
    json_path = tmp_path / "replace.json"
    # "seed_1" is kept, "seed_2" is dropped, "seed_10" shares a prefix with "seed_1".
    _write_json(json_path, ["seed_1", "seed_10"])

    agent = _make_agent(cuga_folder)
    result = await agent.policies.load_from_json(str(json_path), clear_existing=True)
    assert result["errors"] == []

    guards_dir = os.path.join(cuga_folder, "intent_guards")
    assert sorted(os.listdir(guards_dir)) == ["seed_1.md", "seed_10.md"]

    restarted = _make_agent(cuga_folder)
    await restarted.policies._ensure_policy_system()
    assert sorted(restarted._policy_system.storage._policies) == ["seed_1", "seed_10"]


@pytest.mark.asyncio
async def test_load_from_json_without_clear_keeps_existing_files(cuga_folder, tmp_path):
    json_path = tmp_path / "add.json"
    _write_json(json_path, ["extra"])

    agent = _make_agent(cuga_folder)
    await agent.policies.load_from_json(str(json_path))

    guards_dir = os.path.join(cuga_folder, "intent_guards")
    assert sorted(os.listdir(guards_dir)) == ["extra.md", "seed_1.md", "seed_2.md"]


@pytest.mark.asyncio
async def test_server_style_apply_does_not_prune_by_default(cuga_folder):
    """Server callers (shared draft/live .cuga) keep the old behaviour unless they opt in."""
    from cuga.backend.cuga_graph.policy.utils import apply_policies_data_to_storage

    storage = _FakeStorage()
    sync = PolicyFilesystemSync(cuga_folder=cuga_folder)
    await apply_policies_data_to_storage(storage, [], clear_existing=True, filesystem_sync=sync)

    guards_dir = os.path.join(cuga_folder, "intent_guards")
    assert sorted(os.listdir(guards_dir)) == ["seed_1.md", "seed_2.md"]
