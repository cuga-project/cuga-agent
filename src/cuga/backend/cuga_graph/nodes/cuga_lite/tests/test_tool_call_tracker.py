import numpy as np
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from cuga.backend.cuga_graph.nodes.cuga_lite.tool_call_tracker import ToolCallTracker


def test_tool_call_tracker_normalizes_numpy_values_for_checkpointing():
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
