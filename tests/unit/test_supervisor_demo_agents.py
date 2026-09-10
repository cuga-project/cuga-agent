"""Per-agent supervisor overrides must not inject the YAML-path demo stubs."""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.graph import DynamicAgentGraph

pytestmark = pytest.mark.unit


def _graph(supervisor_agents):
    g = DynamicAgentGraph.__new__(DynamicAgentGraph)
    g.supervisor_agents = supervisor_agents
    return g


def test_per_agent_empty_override_skips_demo_agents():
    assert _graph({})._should_inject_demo_supervisor_agents({}) is False


def test_per_agent_resolved_agents_skip_demo():
    assert _graph({"crm": object()})._should_inject_demo_supervisor_agents({"crm": object()}) is False


def test_yaml_path_still_gets_demo_agents_when_empty():
    assert _graph(None)._should_inject_demo_supervisor_agents({}) is True


def test_yaml_path_skips_demo_when_config_loaded():
    assert _graph(None)._should_inject_demo_supervisor_agents({"crm": object()}) is False
