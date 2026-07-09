# Recipe: Pre-Graph Intent Intercept with IntentGuard

Use `IntentGuard` policies to intercept requests **before** your LangGraph graph runs.
This is the fastest path for blocking or redirecting known patterns — no graph overhead involved.

---

## When to use this

- You have intents that should always be blocked or redirected (off-topic requests, restricted queries)
- The match condition is deterministic (keyword or state-based) and doesn't need LLM reasoning
- You want blocked requests to return instantly, without consuming graph compute

---

## How it works

```
User request
     │
     ▼
IntentGuard check   ◄── matches? → return guard response immediately
     │ no match
     ▼
graph.invoke()
```

Define one or more `IntentGuard` policies. Each guard has a list of triggers and a response.
Before calling `graph.invoke()`, evaluate the guards. If one matches, return its response directly.

---

## Example

### 1. Define the guard (YAML or Python)

```yaml
# policies/guards.yaml
- type: intent_guard
  id: block_medical_advice
  name: Block medical advice requests
  description: Redirects requests for medical advice to a disclaimer.
  triggers:
    - type: keyword
      value: ["diagnose", "prescription", "medical advice", "symptoms"]
      operator: or
  response:
    response_type: natural_language
    content: "I can't provide medical advice. Please consult a qualified healthcare professional."
  enabled: true
```

### 2. Load the guard at startup

```python
from cuga.backend.cuga_graph.policy.models import IntentGuard
import yaml

with open("policies/guards.yaml") as f:
    raw = yaml.safe_load(f)

guards = [IntentGuard(**p) for p in raw if p["type"] == "intent_guard"]
```

### 3. Check in your request handler

```python
from cuga.backend.cuga_graph.policy.engine import PolicyEngine

engine = PolicyEngine()
for guard in guards:
    engine.add_policy(guard)

async def handle_request(user_message: str, state: dict) -> str:
    # Check guards before the graph
    match = await engine.evaluate(user_message, state)
    if match.matched and match.action:
        return match.action.content  # return guard response immediately

    # No guard matched — run the graph
    result = await graph.ainvoke({"messages": [("user", user_message)], **state})
    return result["messages"][-1].content
```

---

## Tips

- **Keyword triggers** are evaluated with regex — no LLM call needed, very fast.
- **NaturalLanguageTrigger** uses semantic similarity (embedding model required) — use it when keywords are too brittle.
- **StateTrigger** can gate on session state (e.g. block certain actions before a required step completes).
- Guards fire in `priority` order (higher = first). Set `priority` explicitly if order matters.
- Set `allow_override: false` on guards that must never be bypassed.

---

## Related

- [CUGA Policy Overview](../../issues/)
- [Ephemeral Playbook Injection](ephemeral-playbook-injection.md)
