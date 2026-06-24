# CugaSupervisor (Multi-Agent)

[← Back to README](../README.md)

Orchestrate multiple agents with a single supervisor: delegate tasks to specialized sub-agents, mix local agents with remote A2A agents, and pass data between them.

**Documentation**: [CugaSupervisor](https://docs.cuga.dev/docs/sdk/cuga_supervisor)

**Try the supervisor demo:** run the multi-agent demo (CRM + email sub-agents) with:

```bash
cuga start demo_supervisor
```

## Quick Start

```python
from cuga import CugaAgent, CugaSupervisor
from langchain_core.tools import tool
import asyncio

@tool
def get_customers(limit: int = 10) -> str:
    """Fetch top customers from CRM with name, email, and revenue. Returns a formatted string."""
    customers = [
        "Alice (alice@example.com, $250,000)",
        "Bob (bob@example.com, $180,000)",
        "Carol (carol@example.com, $120,000)",
        "Dave (dave@example.com, $95,000)",
        "Eve (eve@example.com, $88,000)",
    ]
    top = customers[: min(limit, len(customers))]
    return "Top customers by revenue: " + "; ".join(f"{i+1}. {c}" for i, c in enumerate(top))

@tool
def send_email(to: str, body: str) -> str:
    """Send an email. Returns confirmation."""
    return f"Email sent successfully to {to}"

async def main():
    crm_agent = CugaAgent(tools=[get_customers])
    crm_agent.description = "CRM and customer data"

    email_agent = CugaAgent(tools=[send_email])
    email_agent.description = "Sending emails and notifications"

    supervisor = CugaSupervisor(agents={
        "crm": crm_agent,
        "email": email_agent,
    })

    result = await supervisor.invoke("Get our top 5 customers by revenue, then send the top customer a thank-you email")
    print(result.answer)

asyncio.run(main())
```

To add a remote agent via A2A, pass an external config in `agents`: `"analytics": {"type": "external", "description": "...", "config": {"a2a_protocol": {"endpoint": "http://localhost:9999", "transport": "http"}}}`.

## Supervisor features

- **Delegation**: Supervisor hands work to sub-agents and can pass variables between them when needed.
- **Internal + external**: Combine local `CugaAgent` instances with external agents via **A2A**, task-only or variables in metadata if enabled.
- **Variable passing**: Use `variables=["var_name"]` to pass previous agent outputs or context to the next agent (for internal agents, or A2A when `pass_variables_a2a` is enabled in settings).
- **Agent cards**: For A2A agents, capabilities and description are taken from the agent card and shown in the supervisor prompt.

You can also load agents from YAML with `CugaSupervisor.from_yaml("path/to/config.yaml")`. Enable the supervisor in `settings.toml` under `[supervisor]` when using the server.
