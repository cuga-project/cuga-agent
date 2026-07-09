# Recipe: Deterministic Tool-Failure Reflection in LangGraph

Handle the case where all tool calls in a turn fail — without invoking the LLM again for replanning.

---

## When to use this

- You want a fast, guaranteed fallback when tools fail (no LLM latency, no token cost)
- Tool failures are expected and recoverable (e.g. no results found, invalid input)
- You want to keep CUGA's LLM-based reflection for strategic replanning and use this separately for routine failures

---

## How it differs from CUGA's reflection node

CUGA's built-in reflection uses an LLM to critique the agent's strategy and decide next steps. That is powerful for complex multi-step reasoning but costs a full LLM call.

A deterministic short-circuit inspects `ToolMessage` results directly in Python. If every tool call in the turn failed, it emits a plain fallback message immediately — no LLM involved.

Use them together: deterministic short-circuit for routine failures, LLM reflection for strategic replanning when needed.

---

## Example

```python
from langgraph.graph import StateGraph, END
from langchain_core.messages import AIMessage, ToolMessage
import json


def all_tools_failed(messages: list) -> bool:
    """Return True if every ToolMessage in the last turn signals failure."""
    tool_results = [m for m in messages if isinstance(m, ToolMessage)]
    if not tool_results:
        return False
    return all(_is_failure(m) for m in tool_results)


def _is_failure(msg: ToolMessage) -> bool:
    try:
        data = json.loads(msg.content)
        return not data.get("success", True)
    except (ValueError, TypeError):
        return False


def reflection_node(state: dict) -> dict:
    if all_tools_failed(state["messages"]):
        fallback = AIMessage(
            content="I wasn't able to complete that step. Could you check the details and try again?"
        )
        return {"messages": state["messages"] + [fallback]}
    # Not a full failure — let the agent continue normally
    return state


# Wire into the graph between tools and agent
builder = StateGraph(dict)
builder.add_node("agent", agent_node)
builder.add_node("tools", tool_node)
builder.add_node("reflection", reflection_node)

builder.add_edge("tools", "reflection")
builder.add_conditional_edges(
    "reflection",
    lambda s: END if isinstance(s["messages"][-1], AIMessage) else "agent",
)
```

---

## Tips

- Keep the failure check simple — inspect the `success` field (or equivalent) in your tool's return payload.
- If only *some* tools failed and others succeeded, let the agent continue; only short-circuit when *all* failed.
- You can customise the fallback message per failure type by inspecting the tool name on the `ToolMessage`.
- This node adds no latency when tools succeed — it just passes `state` through unchanged.

---

## Related

- [Pre-Graph Intent Intercept](pre-graph-intent-intercept.md)
- [CUGA plan_controller_agent](../../../src/cuga/backend/cuga_graph/nodes/task_decomposition_planning/)
