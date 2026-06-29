from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
    CugaLiteState,
    create_cuga_lite_graph,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import (
    AppDefinition,
    ToolProviderInterface,
)


class _EmptyToolProvider(ToolProviderInterface):
    async def initialize(self):
        return None

    async def get_apps(self):
        return [AppDefinition(name="test_app", description="Test app", type="api")]

    async def get_tools(self, app_name: str):
        return []

    async def get_all_tools(self):
        return []


class _CapturingModel:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, messages, config=None):
        self.calls.append({"messages": messages, "config": config})
        return AIMessage(content="done")


@pytest.mark.asyncio
async def test_cuga_lite_evolve_guidelines_are_injected_independently_of_legacy_memory():
    model = _CapturingModel()
    graph = create_cuga_lite_graph(
        model=model,
        tool_provider=_EmptyToolProvider(),
        apps_list=[],
    ).compile()

    state = CugaLiteState(
        chat_messages=[HumanMessage(content="fetch all users")],
        sub_task="fetch all users",
    )

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph.settings.policy.enabled",
            False,
        ),
        patch(
            "cuga.backend.cuga_graph.utils.context_management_utils.apply_context_summarization",
            new=AsyncMock(side_effect=lambda messages, *args, **kwargs: messages),
        ),
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.get_guidelines",
            new=AsyncMock(return_value="1. Check pagination before assuming the first page is complete."),
        ) as mock_get_guidelines,
    ):
        result = await graph.ainvoke(state, config={"configurable": {}})

    assert result["final_answer"] == "done"
    mock_get_guidelines.assert_awaited_once_with(
        "fetch all users",
        user_id=None,
        namespace_id=None,
        session_id=None,
    )

    captured_messages = model.calls[0]["messages"]
    assert captured_messages[0]["role"] == "system"
    assert "## Evolve Guidelines" in captured_messages[0]["content"]
    assert "Check pagination before assuming the first page is complete." in captured_messages[0]["content"]


@pytest.mark.asyncio
async def test_multi_user_params_flow_end_to_end():
    """Verify user_id, namespace_id, and session_id are passed to get_guidelines during graph execution.

    This test verifies that multi-user parameters from state are correctly extracted
    and passed to EvolveIntegration.get_guidelines() during the graph execution flow.
    """
    model = _CapturingModel()
    graph = create_cuga_lite_graph(
        model=model,
        tool_provider=_EmptyToolProvider(),
        apps_list=[],
    ).compile()

    state = CugaLiteState(
        chat_messages=[HumanMessage(content="fetch all users")],
        sub_task="fetch all users",
        user_id="user-123",
        thread_id="thread-456",
        service_scope={"tenant_id": "tenant-789", "instance_id": "inst-1"},
    )

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph.settings.policy.enabled",
            False,
        ),
        patch(
            "cuga.backend.cuga_graph.utils.context_management_utils.apply_context_summarization",
            new=AsyncMock(side_effect=lambda messages, *args, **kwargs: messages),
        ),
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.get_guidelines",
            new=AsyncMock(return_value="1. Use pagination for large datasets."),
        ) as mock_get_guidelines,
    ):
        result = await graph.ainvoke(state, config={"configurable": {}})

    # Verify the graph completed successfully
    assert result["final_answer"] == "done"

    # Verify get_guidelines was called with multi-user parameters
    mock_get_guidelines.assert_awaited_once_with(
        "fetch all users",
        user_id="user-123",
        namespace_id="tenant-789",
        session_id="thread-456",
    )


@pytest.mark.asyncio
async def test_multi_user_params_flow_with_none_values():
    """Verify that None values for multi-user parameters are handled correctly in get_guidelines.

    This test verifies that when state has no user_id, thread_id, or service_scope set,
    the get_guidelines call receives None values for all multi-user parameters.
    """
    model = _CapturingModel()
    graph = create_cuga_lite_graph(
        model=model,
        tool_provider=_EmptyToolProvider(),
        apps_list=[],
    ).compile()

    state = CugaLiteState(
        chat_messages=[HumanMessage(content="test task")],
        sub_task="test task",
        # No user_id, thread_id, or service_scope explicitly set
    )

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph.settings.policy.enabled",
            False,
        ),
        patch(
            "cuga.backend.cuga_graph.utils.context_management_utils.apply_context_summarization",
            new=AsyncMock(side_effect=lambda messages, *args, **kwargs: messages),
        ),
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.get_guidelines",
            new=AsyncMock(return_value="Test guideline"),
        ) as mock_get_guidelines,
    ):
        result = await graph.ainvoke(state, config={"configurable": {}})

    # Verify the graph completed successfully
    assert result["final_answer"] == "done"

    # Verify get_guidelines was called with None values for all multi-user parameters
    mock_get_guidelines.assert_awaited_once_with(
        "test task",
        user_id=None,
        namespace_id=None,
        session_id=None,
    )
