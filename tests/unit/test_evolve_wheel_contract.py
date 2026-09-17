"""Optional Evolve extra contract tests: real MCP handlers and filesystem storage, no LLM."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from cuga.backend.evolve.integration import EvolveIntegration

pytestmark = pytest.mark.unit


@pytest.fixture
def evolve(tmp_path, monkeypatch):
    pytest.importorskip("altk_evolve")
    from altk_evolve.config.evolve import EvolveConfig
    from altk_evolve.config.filesystem import FilesystemSettings
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.frontend.mcp import mcp_server as server
    from altk_evolve.config.guidelines import guidelines_settings

    client = EvolveClient(
        config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path)))
    )
    monkeypatch.setattr(server, "get_client", lambda: client)
    monkeypatch.setattr(server, "_initialized_namespaces", set())
    monkeypatch.setattr(server.evolve_config, "namespace_id", "legacy")
    monkeypatch.setattr(server, "extract_facts_from_messages", lambda messages: [messages[0]["content"]])
    monkeypatch.setattr(guidelines_settings, "guidelines_mode", "standard")
    monkeypatch.setattr(
        server,
        "generate_guidelines",
        lambda messages: [
            SimpleNamespace(
                task_description="task",
                guidelines=[
                    SimpleNamespace(
                        content="Use a concise answer",
                        category="style",
                        rationale="clarity",
                        trigger="reply",
                        implementation_steps=[],
                    )
                ],
            )
        ],
    )
    from altk_evolve.llm.conflict_resolution import conflict_resolution
    from altk_evolve.schema.conflict_resolution import EntityUpdate

    monkeypatch.setattr(
        conflict_resolution,
        "resolve_conflicts",
        lambda old, new: [
            EntityUpdate(id=e.id, type=e.type, content=e.content, metadata=e.metadata, event="ADD")
            for e in new
        ],
    )
    monkeypatch.setattr(EvolveIntegration, "is_enabled", lambda: True)

    async def call(tool, args):
        result = getattr(server, tool)(**args)
        return json.loads(result) if isinstance(result, str) else result

    monkeypatch.setattr(EvolveIntegration, "_call_tool", call)
    return client, server


@pytest.mark.asyncio
async def test_fact_adapter_isolates_instances_and_users(evolve):
    client, _ = evolve
    for namespace, user in [("instance-a", "alice"), ("instance-b", "alice"), ("instance-a", "bob")]:
        text = f"private {namespace} {user}"
        await EvolveIntegration.store_user_facts(
            user, text, namespace_id=namespace, metadata={"user_id": "impostor", "agent_id": "agent-a"}
        )
    for namespace, user in [("instance-a", "alice"), ("instance-b", "alice"), ("instance-a", "bob")]:
        result = await EvolveIntegration.retrieve_user_facts(
            user, "private", namespace_id=namespace, agent_id="agent-a"
        )
        assert result["matched_count"] == 1
        assert result["categories"]["misc"][0]["content"] == f"private {namespace} {user}"
    result = await EvolveIntegration.retrieve_user_facts("unknown", "private", namespace_id="instance-a")
    assert result["matched_count"] == 0
    assert not client.namespace_exists("legacy")


@pytest.mark.asyncio
async def test_trajectory_save_inventory_and_access_share_agent_scope(evolve, monkeypatch):
    from cuga.config import settings

    client, server = evolve
    monkeypatch.setattr(settings.evolve, "save_on_success", True)
    await EvolveIntegration.save_trajectory(
        [HumanMessage(content="Please answer briefly")],
        "task",
        True,
        user_id="alice",
        namespace_id="instance-a",
        session_id="thread",
        agent_id="agent-a",
    )
    inventory = await EvolveIntegration.list_entities(
        user_id="alice", agent_id="agent-a", namespace_id="instance-a"
    )
    assert {item["type"] for item in inventory["items"]} == {"trajectory", "guideline"}
    ids = [item["id"] for item in inventory["items"]]
    access = await EvolveIntegration.record_access(
        ids, user_id="alice", agent_id="agent-a", namespace_id="instance-a"
    )
    assert set(access["updated_ids"]) == set(ids)
    assert not access["denied_ids"]
    denied = await EvolveIntegration.record_access(
        ids, user_id="alice", agent_id="agent-b", namespace_id="instance-a"
    )
    assert set(denied["denied_ids"]) == set(ids)
    # Legacy untagged memories remain unassigned, never implicitly owned by an agent.
    server.save_trajectory(
        '[{"role":"user","content":"legacy"}]',
        task_id="legacy-task",
        user_id="alice",
        namespace_id="instance-a",
    )
    all_items = await EvolveIntegration.list_entities(namespace_id="instance-a")
    untagged = [item["id"] for item in all_items["items"] if not item["metadata"].get("agent_id")]
    assert untagged
    scoped = await EvolveIntegration.list_entities(
        user_id="alice", agent_id="agent-a", namespace_id="instance-a"
    )
    assert not set(untagged).intersection(item["id"] for item in scoped["items"])
    denied = await EvolveIntegration.record_access(
        untagged, user_id="alice", agent_id="agent-a", namespace_id="instance-a"
    )
    assert set(denied["denied_ids"]) == set(untagged)
