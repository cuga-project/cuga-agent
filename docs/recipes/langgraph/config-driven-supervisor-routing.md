# Recipe: Config-Driven Supervisor Routing in LangGraph

Build the supervisor's routing prompt from a YAML agent registry at startup, so adding or removing a sub-agent only requires a config change — no code edits needed.

---

## When to use this

- You have multiple sub-agents and the list changes as the system grows
- You want routing rules and agent descriptions to live outside of code
- You are already using CUGA's supervisor YAML format and want to extend the routing prompt from it

---

## How it works

```
startup:
  load agents from YAML → build routing prompt string → attach to supervisor node

each turn:
  supervisor receives user message + routing prompt → picks agent → dispatches
```

The routing prompt is built once at startup from the YAML registry. The supervisor node is stateless — it just reads the pre-built prompt.

---

## Example

### 1. Define your agent registry (YAML)

```yaml
# config/agents.yaml
agents:
  - name: search_agent
    description: Searches for available options based on user criteria.
    url: http://localhost:8001

  - name: booking_agent
    description: Handles reservations and confirms bookings.
    url: http://localhost:8002

  - name: support_agent
    description: Answers questions about existing bookings and policies.
    url: http://localhost:8003
```

### 2. Build the routing prompt at startup

```python
import yaml

def build_routing_prompt(config_path: str) -> str:
    with open(config_path) as f:
        config = yaml.safe_load(f)

    lines = ["Route the user request to the most suitable agent.", ""]
    for agent in config["agents"]:
        lines.append(f"- **{agent['name']}**: {agent['description']}")
    lines.append("")
    lines.append("Reply with only the agent name.")
    return "\n".join(lines)


ROUTING_PROMPT = build_routing_prompt("config/agents.yaml")
```

### 3. Use the prompt in the supervisor node

```python
from langchain_core.messages import SystemMessage, HumanMessage

def supervisor_node(state: dict) -> dict:
    user_message = state["messages"][-1].content
    response = llm.invoke([
        SystemMessage(content=ROUTING_PROMPT),
        HumanMessage(content=user_message),
    ])
    chosen_agent = response.content.strip()
    return {"next_agent": chosen_agent}
```

To add a new agent, update `agents.yaml` and restart — no code changes required.

---

## Relation to CUGA's supervisor YAML

CUGA already supports a supervisor YAML format for defining external A2A agents (see `docs/examples/a2a_two_cuga/`). This recipe applies the same config-driven principle to the routing prompt itself, keeping agent descriptions as data rather than hardcoded strings.

---

## Tips

- Keep agent descriptions short and distinct — the LLM routes based on these descriptions, so overlap causes misrouting.
- Add an `unknown` fallback agent in the registry to handle requests that don't match any agent.
- If you need deterministic routing for known patterns, combine with an `IntentGuard` that redirects before the supervisor runs (see [pre-graph-intent-intercept.md](pre-graph-intent-intercept.md)).

---

## Related

- [Pre-Graph Intent Intercept](pre-graph-intent-intercept.md)
- [CUGA A2A supervisor example](../../examples/a2a_two_cuga/)
