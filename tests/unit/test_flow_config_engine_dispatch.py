"""Engine dispatch in FlowConfig.to_flow_agent().

The one branch that decides which WorkflowEngine backs a process. Before this test
the `else` silently fell through to LangGraph, so a typo'd engine type ran the wrong
engine while appearing to work.
"""

from unittest.mock import MagicMock, patch

import pytest

from cuga.backend.cuga_graph.nodes.cuga_flow.flow_config import FlowConfig


def _config(engine_type: str) -> FlowConfig:
    """A FlowConfig carrying only the workflow_engine block dispatch reads."""
    cfg = FlowConfig.__new__(FlowConfig)
    cfg.config = {"workflow_engine": {"type": engine_type, "url": "http://localhost:8080"}}
    cfg.config_path = f"/tmp/{engine_type}_config.yaml"
    return cfg


def _dispatch(engine_type: str) -> MagicMock:
    """Run to_flow_agent() with everything but the engine branch stubbed; return the bridge."""
    bridge = MagicMock()
    with (
        patch("cuga.backend.server.cuga_flo_mcp.bridge.MCPFlowBridge", return_value=bridge),
        patch("cuga.backend.cuga_graph.nodes.cuga_flow.process_registry.ProcessRegistry"),
        patch("cuga.backend.cuga_graph.nodes.cuga_flow.flow_config.FlowAgent"),
    ):
        _config(engine_type).to_flow_agent()
    return bridge


def test_kogito_builds_kogito_proxy():
    with patch("cuga.backend.server.kogito.kogito_proxy.KogitoProxy") as proxy_cls:
        bridge = _dispatch("kogito")

    proxy_cls.assert_called_once()
    bridge.register_kogito_engine.assert_called_once()
    # Kogito must not be wired through the Flowable path — that one gates the
    # dead _realize_hook_action REST redirect.
    bridge.register_flowable_engine.assert_not_called()


def test_flowable_builds_flowable_proxy():
    with patch("cuga.backend.server.flowable.flowable_proxy.FlowableProxy") as proxy_cls:
        bridge = _dispatch("flowable")

    proxy_cls.assert_called_once()
    bridge.register_flowable_engine.assert_called_once()
    bridge.register_kogito_engine.assert_not_called()


def test_langgraph_builds_langgraph_engine():
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_flow.langgraph_engine.LangGraphWorkflowEngine"
    ) as engine_cls:
        bridge = _dispatch("langgraph")

    engine_cls.assert_called_once_with(bridge=bridge)
    bridge.register_flowable_engine.assert_not_called()
    bridge.register_kogito_engine.assert_not_called()


def test_unknown_engine_raises_rather_than_defaulting_to_langgraph():
    with pytest.raises(ValueError, match="kogitoo"):
        _dispatch("kogitoo")
