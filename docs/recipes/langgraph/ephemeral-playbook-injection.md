# Recipe: Ephemeral Playbook Injection in LangGraph

Use `Playbook` policies to inject step-by-step instructions into a single LLM call — without permanently modifying your base system prompt.

---

## When to use this

- Your agent has multiple stages, and each stage needs different instructions
- You want the LLM to follow a specific procedure for this turn only
- Your base system prompt should stay constant across turns

---

## How it works

```
Each turn:
  base_prompt + playbook_content → SystemMessage → LLM call
  (base_prompt is never mutated)
```

Match the active `Playbook` for the current turn, append its `markdown_content` to the base prompt string, and pass it as the `SystemMessage` for that call only. Next turn starts fresh from the base prompt again.

---

## Gating by stage with StateTrigger

Use `StateTrigger` to activate a playbook only when the agent is in the right stage:

```yaml
# policies/playbooks.yaml
- type: playbook
  id: search_guide
  name: Flight Search Instructions
  description: Step-by-step guide for collecting search parameters.
  triggers:
    - type: state
      key: stage
      value: COLLECTING
      operator: equals
  markdown_content: |
    ## Your task for this turn
    1. Ask the user for origin, destination, and travel date if any are missing.
    2. Do not proceed to search until all three are confirmed.
    3. Summarise what you collected before calling the search tool.
  enabled: true
```

---

## Example: per-turn injection in a LangGraph node

```python
from langchain_core.messages import SystemMessage
from cuga.backend.cuga_graph.policy.models import Playbook
import yaml

# Load playbooks at startup
with open("policies/playbooks.yaml") as f:
    raw = yaml.safe_load(f)
playbooks = [Playbook(**p) for p in raw if p["type"] == "playbook"]


def find_matching_playbook(state: dict) -> Playbook | None:
    for pb in sorted(playbooks, key=lambda p: -p.priority):
        if pb.enabled and matches_triggers(pb.triggers, state):
            return pb
    return None


def agent_node(state: dict) -> dict:
    base_prompt = "You are a helpful assistant."

    # Build ephemeral prompt for this turn only
    playbook = find_matching_playbook(state)
    if playbook:
        system_content = f"{base_prompt}\n\n{playbook.markdown_content}"
    else:
        system_content = base_prompt

    messages = [SystemMessage(content=system_content)] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": state["messages"] + [response]}
```

---

## Tips

- `StateTrigger` is the right trigger for stage-based gating — it checks agent state directly without needing LLM inference.
- `NaturalLanguageTrigger` works when you want to match on user intent semantically rather than a fixed state value.
- Keep `markdown_content` short — it competes with your context budget on every call where it fires.
- Set `priority` on playbooks so that when multiple match, the most specific one wins.

---

## Related

- [Pre-Graph Intent Intercept](pre-graph-intent-intercept.md)
- [CUGA Policy Models](../../../src/cuga/backend/cuga_graph/policy/models.py)
