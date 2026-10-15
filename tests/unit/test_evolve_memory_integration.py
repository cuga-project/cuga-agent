import json
from unittest.mock import AsyncMock, patch

import pytest

from cuga.backend.evolve.integration import EvolveIntegration

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_list_entities_serializes_filters_and_scope():
    with (
        patch.object(EvolveIntegration, "is_enabled", return_value=True),
        patch.object(
            EvolveIntegration,
            "_call_tool",
            new=AsyncMock(return_value={"items": [], "total": 0}),
        ) as call_tool,
    ):
        result = await EvolveIntegration.list_entities(
            entity_types=["fact"],
            user_id="user-a",
            agent_id="agent-a",
            metadata_filters={"category": "preference"},
            limit=20,
            namespace_id="namespace-a",
        )

    assert result == {"items": [], "total": 0}
    call_tool.assert_awaited_once_with(
        "list_entities",
        {
            "limit": 20,
            "include_content": False,
            "entity_types": ["fact"],
            "user_id": "user-a",
            "agent_id": "agent-a",
            "metadata_filters": json.dumps({"category": "preference"}),
            "namespace_id": "namespace-a",
        },
    )


@pytest.mark.asyncio
async def test_management_tools_remain_available_when_operator_default_is_disabled():
    with (
        patch.object(EvolveIntegration, "is_enabled", return_value=False),
        patch.object(
            EvolveIntegration, "_call_tool", new=AsyncMock(return_value={"id": "entity-a"})
        ) as call_tool,
    ):
        result = await EvolveIntegration.get_entity("entity-a")

    assert result == {"id": "entity-a"}
    call_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_attributed_guidelines_normalize_identifiers():
    with (
        patch.object(EvolveIntegration, "is_enabled", return_value=True),
        patch.object(
            EvolveIntegration,
            "_call_tool",
            new=AsyncMock(return_value={"text": "Use the account name", "entity_ids": ["g-1"]}),
        ) as call_tool,
    ):
        result = await EvolveIntegration.get_guidelines_with_attribution(
            "draft a reply",
            user_id="default_user",
            namespace_id="namespace-a",
            session_id="thread-a",
        )

    assert result == {
        "text": "Use the account name",
        "entity_ids": ["g-1"],
        "namespace_id": None,
        "entity_revisions": {},
    }
    call_tool.assert_awaited_once_with(
        "get_guidelines_with_attribution",
        {
            "task": "draft a reply",
            "namespace_id": "namespace-a",
            "session_id": "thread-a",
        },
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize('supported', [True, False])
async def test_fact_conflict_resolution_requires_upstream_capability(monkeypatch, supported):
    from unittest.mock import AsyncMock
    from cuga.backend.evolve.integration import EvolveIntegration

    status = {'memory_capabilities': {'scoped_conflict_resolution': True}} if supported else {}
    monkeypatch.setattr(EvolveIntegration, 'get_compliance_status', AsyncMock(return_value=status))
    call = AsyncMock(return_value={'stored_count': 1})
    monkeypatch.setattr(EvolveIntegration, '_call_tool', call)
    await EvolveIntegration.store_user_facts('alice', 'preference', namespace_id='instance-a')
    assert call.call_args.args[1]['enable_conflict_resolution'] is supported
