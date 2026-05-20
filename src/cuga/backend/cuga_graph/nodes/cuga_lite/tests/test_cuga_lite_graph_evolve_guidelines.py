from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
    CugaLiteState,
    create_cuga_lite_graph,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import (
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
            "cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph.apply_context_summarization",
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
    mock_get_guidelines.assert_awaited_once_with("fetch all users")

    captured_messages = model.calls[0]["messages"]
    assert captured_messages[0]["role"] == "system"
    assert "## Evolve Guidelines" in captured_messages[0]["content"]
    assert "Check pagination before assuming the first page is complete." in captured_messages[0]["content"]
