"""
CUGA FLO - Flow-based Agentic Process Orchestration

This module implements the TDF Model (Task-Decision-Flow) for BPMN-based
agentic applications in CUGA.

Agent Types:
- FlowAgent: Meta-agent that orchestrates BPMN process execution
- DecisionAgent: LLM-powered gateway routing agent
- TaskAgent: Task execution agent (wrapper around CugaAgent)

Key Components:
- BPMN Parser: Converts BPMN XML to LangGraph StateGraph
- Hook System: Runtime interception and graph reformation
- Flow State: Extended state management for process execution
"""

from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent_state import (
    FlowState,
    GraphModification,
)
from cuga.backend.cuga_graph.nodes.cuga_flow.hook_manager import (
    Hook,
    HookType,
    HookAction,
    HookResult,
    HookManager,
    create_simple_hook,
)
from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import (
    BPMNParser,
    BPMNProcess,
    BPMNElement,
    BPMNFlow,
)
from cuga.backend.cuga_graph.nodes.cuga_flow.decision_agent import DecisionAgent
from cuga.backend.cuga_graph.nodes.cuga_flow.task_agent import TaskAgent, TaskAgentFactory
from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent import FlowAgent
from cuga.backend.cuga_graph.nodes.cuga_flow.flow_config import FlowConfig, load_flow_from_yaml
from cuga.backend.cuga_graph.nodes.cuga_flow.process_registry import (
    ProcessRegistry,
    ProcessDefinition,
)
from cuga.backend.cuga_graph.nodes.cuga_flow.workflow_engine import (
    WorkflowEngine,
    ControlPointFlowKnowledge,
)
from cuga.backend.cuga_graph.nodes.cuga_flow.langgraph_engine import LangGraphWorkflowEngine
from cuga.backend.server.cuga_flo_mcp import MCPFlowBridge

__all__ = [
    # State
    "FlowState",
    "GraphModification",
    # Hooks
    "Hook",
    "HookType",
    "HookAction",
    "HookResult",
    "HookManager",
    "create_simple_hook",
    # BPMN Parser
    "BPMNParser",
    "BPMNProcess",
    "BPMNElement",
    "BPMNFlow",
    # Agents
    "DecisionAgent",
    "TaskAgent",
    "TaskAgentFactory",
    "FlowAgent",
    # Configuration
    "FlowConfig",
    "load_flow_from_yaml",
    # Registry & Engine (new)
    "ProcessRegistry",
    "ProcessDefinition",
    "WorkflowEngine",
    "ControlPointFlowKnowledge",
    "MCPFlowBridge",
    "LangGraphWorkflowEngine",
]

# Made with Bob
