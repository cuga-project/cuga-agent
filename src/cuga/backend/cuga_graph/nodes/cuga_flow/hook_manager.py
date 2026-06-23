"""
Hook Manager - Runtime interception and graph reformation system.

This module provides the hook mechanism that allows FlowAgent to intercept
execution at specific points (edges) and potentially reform the graph structure
before continuing execution.

Hooks are evaluated at edge transitions and can:
- Inspect current state
- Make decisions about graph structure
- Add/remove nodes and edges
- Modify process variables
"""

from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent_state import FlowState


class HookType(Enum):
    """Types of hooks that can be registered."""

    PRE_EDGE = "pre_edge"


class HookAction(Enum):
    """
    Actions the FlowAgent (meta-agent) will apply based on hook reasoning.

    The hook itself only returns a HookResult — the FlowAgent reads the action
    and executes the corresponding structural intervention on the LangGraph.
    """

    CONTINUE = "continue"  # Proceed along the normal edge
    SKIP_NODE = "skip_node"  # Skip the immediate next node
    SKIP_TO = "skip_to"  # Jump to a named node via Command(goto=),
    # bypassing all intermediate nodes
    TERMINATE = "terminate"  # Hard-halt the process
    SWAP_NODES = "swap_nodes"  # Swap two nodes: redirect to node_b when
    # node_a was next, or node_a when node_b was next
    REMOVE_NODE = "remove_node"  # Remove a node from the execution path;
    # provide target node_id in remove_node field
    ADD_NODE = "add_node"  # Insert a node into the execution path before
    # the normal target; provide node_id and task_instruction in add_node field


@dataclass
class HookResult:
    """
    Decision returned by hook reasoning to the FlowAgent.

    The FlowAgent reads this and applies the corresponding structural action.

    Attributes:
        action:        Intervention type (see HookAction).
        message:       Human-readable explanation shown in the UI via tracker.
        skip_to_node:  Target node ID for SKIP_TO — FlowAgent issues
                       Command(goto=skip_to_node) to jump there directly.
        graph_modifications: Arbitrary detail dict recorded in the audit log
                       for REFORM_GRAPH (informational only at this stage).
        state_updates: Optional process-variable patches to apply before
                       the action takes effect.
        remove_node:   Node ID to skip over for REMOVE_NODE — engine sets
                       _skip_next_node if it matches the immediate next node,
                       or routes past it via Command(goto=) for non-adjacent nodes.
        add_node:      Dict with 'node_id' and optional 'task_instruction' for
                       ADD_NODE — engine inserts a dynamic task node before the
                       normal target and routes through it first.
    """

    action: HookAction
    message: Optional[str] = None
    skip_to_node: Optional[str] = None
    user_prompt: Optional[str] = None
    swap_nodes: Optional[tuple] = None  # (node_a, node_b) pair for SWAP_NODES
    graph_modifications: Optional[Dict[str, Any]] = None
    state_updates: Optional[Dict[str, Any]] = None
    remove_node: Optional[str] = None  # node ID to remove for REMOVE_NODE
    add_node: Optional[Dict[str, Any]] = None  # {node_id, task_instruction} for ADD_NODE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "message": self.message,
            "skip_to_node": self.skip_to_node,
            "user_prompt": self.user_prompt,
            "swap_nodes": list(self.swap_nodes) if self.swap_nodes else None,
            "graph_modifications": self.graph_modifications,
            "state_updates": self.state_updates,
            "remove_node": self.remove_node,
            "add_node": self.add_node,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HookResult":
        swap = d.get("swap_nodes")
        return cls(
            action=HookAction(d["action"]),
            message=d.get("message"),
            skip_to_node=d.get("skip_to_node"),
            user_prompt=d.get("user_prompt"),
            swap_nodes=tuple(swap) if swap else None,
            graph_modifications=d.get("graph_modifications"),
            state_updates=d.get("state_updates"),
            remove_node=d.get("remove_node"),
            add_node=d.get("add_node"),
        )


@dataclass
class Hook:
    """
    A hook that intercepts execution at a specific point.

    Attributes:
        id: Unique hook identifier
        hook_type: Type of hook (pre_edge)
        location: Edge ID the hook is attached to
        condition: Optional condition for hook activation
        handler: Function that evaluates the hook
        enabled: Whether the hook is active
    """

    id: str
    hook_type: HookType
    location: str  # edge_id — exactly one hook per edge
    handler: Callable[[FlowState], HookResult]
    condition: Optional[Callable[[FlowState], bool]] = None
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Optional markdown policy. When set the FlowAgent's hook node calls an LLM
    # with the policy + current process state to produce the HookResult, replacing
    # the handler entirely with policy-driven reasoning.
    policy: Optional[str] = None
    policy_path: Optional[str] = None


class HookManager:
    """
    Manages hooks and their evaluation during process execution.

    The HookManager is responsible for:
    - Registering and organizing hooks
    - Evaluating hooks at appropriate execution points
    - Applying graph modifications when hooks request them
    - Tracking hook evaluation history
    """

    def __init__(self):
        self.hooks: Dict[str, Hook] = {}
        self.hooks_by_location: Dict[str, Hook] = {}
        self.hooks_by_type: Dict[HookType, List[Hook]] = {hook_type: [] for hook_type in HookType}

    def register_hook(self, hook: Hook) -> None:
        """Register a hook for execution. Each edge may have at most one hook."""
        if hook.location in self.hooks_by_location:
            existing = self.hooks_by_location[hook.location]
            raise ValueError(
                f"Edge '{hook.location}' already has hook '{existing.id}'. "
                f"Only one hook is permitted per edge."
            )
        logger.info(f"Registering hook: {hook.id} at {hook.location} ({hook.hook_type.value})")
        self.hooks[hook.id] = hook
        self.hooks_by_location[hook.location] = hook
        self.hooks_by_type[hook.hook_type].append(hook)

    def unregister_hook(self, hook_id: str) -> None:
        """Unregister a hook."""
        if hook_id not in self.hooks:
            logger.warning(f"Hook not found: {hook_id}")
            return
        hook = self.hooks[hook_id]
        logger.info(f"Unregistering hook: {hook_id}")
        del self.hooks[hook_id]
        del self.hooks_by_location[hook.location]
        self.hooks_by_type[hook.hook_type].remove(hook)

    def enable_hook(self, hook_id: str) -> None:
        """Enable a hook."""
        if hook_id in self.hooks:
            self.hooks[hook_id].enabled = True
            logger.info(f"Enabled hook: {hook_id}")

    def disable_hook(self, hook_id: str) -> None:
        """Disable a hook."""
        if hook_id in self.hooks:
            self.hooks[hook_id].enabled = False
            logger.info(f"Disabled hook: {hook_id}")

    def get_hook_at_location(self, location: str) -> Optional[Hook]:
        """Return the hook on the given edge, or None if no hook is registered there."""
        return self.hooks_by_location.get(location)

    def get_hooks_by_type(self, hook_type: HookType) -> List[Hook]:
        """Get all hooks of a specific type."""
        return self.hooks_by_type.get(hook_type, [])


def create_simple_hook(
    hook_id: str,
    location: str,
    hook_type: HookType,
    handler: Callable[[FlowState], HookResult],
    condition: Optional[Callable[[FlowState], bool]] = None,
) -> Hook:
    """
    Convenience function to create a hook on a BPMN sequence flow edge.

    Args:
        hook_id: Unique identifier
        location: Edge ID to attach the hook to (one hook per edge)
        hook_type: Type of hook
        handler: Function to evaluate the hook
        condition: Optional activation condition

    Returns:
        Configured Hook instance
    """
    return Hook(
        id=hook_id,
        hook_type=hook_type,
        location=location,
        handler=handler,
        condition=condition,
    )


# Made with Bob
