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
async def test_structured_tools_do_nothing_when_feature_is_disabled():
    with (
        patch.object(EvolveIntegration, "is_enabled", return_value=False),
        patch.object(EvolveIntegration, "_call_tool", new=AsyncMock()) as call_tool,
    ):
        result = await EvolveIntegration.get_entity("entity-a")

    assert result is None
    call_tool.assert_not_awaited()


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
    }
    call_tool.assert_awaited_once_with(
        "get_guidelines_with_attribution",
        {
            "task": "draft a reply",
            "namespace_id": "namespace-a",
            "session_id": "thread-a",
        },
    )
