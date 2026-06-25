"""Tests for producer-side numpy normalization (issue #229)."""

import numpy as np
import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from unittest.mock import AsyncMock, MagicMock, patch

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker
from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager


class TestVariablesManagerNumpySanitization:
    def setup_method(self):
        VariablesManager().reset()

    def teardown_method(self):
        VariablesManager().reset()

    def test_add_variable_converts_numpy_scalar(self):
        manager = VariablesManager()
        manager.add_variable(np.float64(0.75), name="score")

        stored = manager.get_variable("score")
        assert stored == 0.75
        assert isinstance(stored, float)

    def test_add_variable_converts_nested_numpy(self):
        manager = VariablesManager()
        manager.add_variable(
            {"values": np.array([1, 2, 3]), "enabled": np.bool_(True)},
            name="nested",
        )

        stored = manager.get_variable("nested")
        assert stored == {"values": [1, 2, 3], "enabled": True}

    def test_state_variables_manager_sanitizes_on_ingest(self):
        state = AgentState(input="test", url="http://example.com")
        state.variables_manager.add_variable(np.float64(0.98), name="confidence")

        stored = state.variables_manager.get_variable("confidence")
        assert stored == 0.98
        assert isinstance(stored, float)

        serializer = JsonPlusSerializer()
        serializer.dumps_typed(state.variables_storage)


class TestToolCallTrackerNumpySanitization:
    def test_record_call_normalizes_numpy_for_checkpointing(self):
        ToolCallTracker.start_tracking(enabled=True)

        ToolCallTracker.record_call(
            tool_name="find_tools",
            arguments={
                "score": np.float64(0.75),
                "nested": {"values": np.array([1, 2, 3]), "enabled": np.bool_(True)},
            },
            result={
                "output": {
                    "value": "# Found 2 Matching Tool(s)",
                    "confidence": np.float64(0.98),
                }
            },
            app_name="crm",
            operation_id="find_tools",
        )

        calls = ToolCallTracker.stop_tracking()

        assert len(calls) == 1
        assert calls[0]["arguments"]["score"] == 0.75
        assert isinstance(calls[0]["arguments"]["score"], float)
        assert calls[0]["arguments"]["nested"]["values"] == [1, 2, 3]
        assert calls[0]["arguments"]["nested"]["enabled"] is True
        assert calls[0]["result"]["output"]["confidence"] == 0.98
        assert isinstance(calls[0]["result"]["output"]["confidence"], float)

        serializer = JsonPlusSerializer()
        serializer.dumps_typed(calls)


@pytest.mark.asyncio
async def test_find_tools_sanitizes_tool_schemas():
    mock_tool = MagicMock()
    mock_tool.name = "crm_get_contacts"
    mock_tool.description = "Get contacts"
    mock_tool.args_schema = MagicMock()
    mock_tool.args_schema.schema.return_value = {
        "type": "object",
        "properties": {"limit": {"type": "number", "default": np.float64(10.0)}},
    }
    mock_tool.func = MagicMock()
    mock_tool.func._response_schemas = {
        "success": {"score": np.float64(0.95), "items": np.array([1, 2])},
    }
    mock_tool.func._param_constraints = {}

    api_detail = MagicMock()
    api_detail.name = "crm_get_contacts"
    api_detail.reasoning = "Relevant for contact lookup"

    shortlister_response = MagicMock()
    shortlister_response.result = [api_detail]

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils.PromptUtils.get_tool_docs",
            return_value=("params", "response"),
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.shared.base_agent.BaseAgent.get_chain",
        ) as mock_chain,
        patch(
            "cuga.backend.llm.models.LLMManager",
        ),
    ):
        mock_chain.return_value.ainvoke = AsyncMock(return_value=shortlister_response)

        result = await PromptUtils.find_tools(
            query="check contacts",
            all_tools=[mock_tool],
            all_apps=[],
        )

    assert "crm_get_contacts" in result
    assert "Found 1 Matching Tool" in result

    serializer = JsonPlusSerializer()
    serializer.dumps_typed(result)
