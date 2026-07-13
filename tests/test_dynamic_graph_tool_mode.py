from cuga.backend.cuga_graph.graph import DynamicAgentGraph


def test_dynamic_graph_default_tool_mode_is_internal():
    graph = DynamicAgentGraph(None)

    assert graph.tool_mode == "internal"
    assert graph.external_tools == []
    assert graph.tooling_profile is None