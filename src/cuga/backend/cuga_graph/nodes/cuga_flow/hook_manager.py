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

    PRE_EDGE = "pre_edge"  # Before traversing an edge
    POST_NODE = "post_node"  # After executing a node
    PRE_GATEWAY = "pre_gateway"  # Before gateway decision
    POST_GATEWAY = "post_gateway"  # After gateway decision


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
    REQUEST_USER_INPUT = "request_user_input"  # Surface a prompt to the user and
    # soft-halt; caller can resume
    TERMINATE = "terminate"  # Hard-halt the process
    SWAP_NODES = "swap_nodes"  # Swap two nodes: redirect to node_b when
    # node_a was next, or node_a when node_b was next


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
        user_prompt:   Question text for REQUEST_USER_INPUT — FlowAgent
                       surfaces this to the user and soft-halts.
        graph_modifications: Arbitrary detail dict recorded in the audit log
                       for REFORM_GRAPH (informational only at this stage).
        state_updates: Optional process-variable patches to apply before
                       the action takes effect.
    """

    action: HookAction
    message: Optional[str] = None
    skip_to_node: Optional[str] = None
    user_prompt: Optional[str] = None
    swap_nodes: Optional[tuple] = None  # (node_a, node_b) pair for SWAP_NODES
    graph_modifications: Optional[Dict[str, Any]] = None
    state_updates: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "message": self.message,
            "skip_to_node": self.skip_to_node,
            "user_prompt": self.user_prompt,
            "swap_nodes": list(self.swap_nodes) if self.swap_nodes else None,
            "graph_modifications": self.graph_modifications,
            "state_updates": self.state_updates,
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
        )


@dataclass
class GraphChangeSpec:
    """Spec describing structural changes a hook wants to make to the graph."""

    add_nodes: Dict[str, Callable] = field(default_factory=dict)
    remove_nodes: List[str] = field(default_factory=list)
    add_edges: List[tuple] = field(default_factory=list)
    remove_edges: List[tuple] = field(default_factory=list)
    modify_edges: Dict[str, str] = field(default_factory=dict)


@dataclass
class Hook:
    """
    A hook that intercepts execution at a specific point.

    Attributes:
        id: Unique hook identifier
        hook_type: Type of hook (pre_edge, post_node, etc.)
        location: Where the hook is attached (edge_id, node_id, etc.)
        condition: Optional condition for hook activation
        handler: Function that evaluates the hook
        priority: Execution priority (higher = earlier)
        enabled: Whether the hook is active
    """

    id: str
    hook_type: HookType
    location: str  # edge_id, node_id, or gateway_id
    handler: Callable[[FlowState], HookResult]
    condition: Optional[Callable[[FlowState], bool]] = None
    priority: int = 0
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Optional markdown policy. When set the FlowAgent's hook node calls an LLM
    # with the policy + current process state to produce the HookResult, replacing
    # the handler entirely with policy-driven reasoning.
    policy: Optional[str] = None


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
        self.hooks_by_location: Dict[str, List[Hook]] = {}
        self.hooks_by_type: Dict[HookType, List[Hook]] = {hook_type: [] for hook_type in HookType}

    def register_hook(self, hook: Hook) -> None:
        """
        Register a hook for execution.

        Args:
            hook: Hook to register
        """
        logger.info(f"Registering hook: {hook.id} at {hook.location} ({hook.hook_type.value})")

        # Store by ID
        self.hooks[hook.id] = hook

        # Index by location
        if hook.location not in self.hooks_by_location:
            self.hooks_by_location[hook.location] = []
        self.hooks_by_location[hook.location].append(hook)

        # Index by type
        self.hooks_by_type[hook.hook_type].append(hook)

        # Sort by priority (descending)
        self.hooks_by_location[hook.location].sort(key=lambda h: h.priority, reverse=True)
        self.hooks_by_type[hook.hook_type].sort(key=lambda h: h.priority, reverse=True)

    def unregister_hook(self, hook_id: str) -> None:
        """
        Unregister a hook.

        Args:
            hook_id: ID of hook to remove
        """
        if hook_id not in self.hooks:
            logger.warning(f"Hook not found: {hook_id}")
            return

        hook = self.hooks[hook_id]
        logger.info(f"Unregistering hook: {hook_id}")

        # Remove from all indexes
        del self.hooks[hook_id]
        self.hooks_by_location[hook.location].remove(hook)
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

    def get_hooks_at_location(self, location: str) -> List[Hook]:
        """Get all hooks at a specific location."""
        return self.hooks_by_location.get(location, [])

    def get_hooks_by_type(self, hook_type: HookType) -> List[Hook]:
        """Get all hooks of a specific type."""
        return self.hooks_by_type.get(hook_type, [])


def create_simple_hook(
    hook_id: str,
    location: str,
    hook_type: HookType,
    handler: Callable[[FlowState], HookResult],
    condition: Optional[Callable[[FlowState], bool]] = None,
    priority: int = 0,
) -> Hook:
    """
    Convenience function to create a simple hook.

    Args:
        hook_id: Unique identifier
        location: Where to attach the hook
        hook_type: Type of hook
        handler: Function to evaluate the hook
        condition: Optional activation condition
        priority: Execution priority

    Returns:
        Configured Hook instance
    """
    return Hook(
        id=hook_id,
        hook_type=hook_type,
        location=location,
        handler=handler,
        condition=condition,
        priority=priority,
    )


# Made with Bob
