# Travel Agent Example

A corporate travel planning example built with **CUGA's multi-agent supervisor**.

The supervisor orchestrates four specialised sub-agents to plan a business trip end-to-end:
search for flights → search for hotels → check policy compliance → send manager approval via Slack.

---

## What This Example Demonstrates

| Concept | Where |
|---------|-------|
| `CugaSupervisor` orchestrating multiple `CugaAgent` instances | `main.py`, `config/supervisor_travel_agent.yaml` |
| One focused tool per agent | `agents/`, `tools/` |
| Policy via CUGA Tool Guide (`add_tool_guide`) | `.cuga/tool_guides/policy_employee.md` |
| Real external APIs (SerpAPI, Slack) with graceful fallback | `tools/flights.py`, `tools/hotels.py`, `tools/slack.py` |
| Zero duplication between SDK and UI run modes | `import_from` in YAML + `CugaSupervisor.from_yaml()` |
| Multi-turn conversation (search → select → approve) | `main.py` |

---

## Architecture

```text
tools/flights.py          ← search_flights (SerpAPI Google Flights)
tools/hotels.py           ← search_hotels  (SerpAPI Google Hotels)
tools/compliance.py       ← analyze_travel_compliance (structures data for policy evaluation)
tools/slack.py            ← send_slack_approval (real Slack + simulated fallback)
        ↓ imported by
agents/flight_agent.py    ← CugaAgent(tools=[search_flights])
agents/hotel_agent.py     ← CugaAgent(tools=[search_hotels])
agents/compliance_agent.py← CugaAgent(tools=[analyze_travel_compliance], policy loaded via add_tool_guide)
agents/approval_agent.py  ← CugaAgent(tools=[send_slack_approval])
        ↓ used by
config/supervisor_travel_agent.yaml  ← single source of truth (agents + workflow instructions)
        ↓ loaded by
main.py                   ← SDK path  (CugaSupervisor.from_yaml, terminal)
cuga start travel_agent   ← UI path   (same YAML, browser chat UI)
```

### No Duplication

Agents are defined **once** in Python. The YAML references them via `import_from` and defines the
supervisor's workflow instructions (`special_instructions`). Both `main.py` and `cuga start travel_agent`
load the same YAML — there is no separate programmatic supervisor construction.

### Policy via CUGA Tool Guide

The corporate travel policy lives in `.cuga/tool_guides/policy_employee.md`. It is loaded as a
**Tool Guide** by `compliance_agent.py` at module import time using `add_tool_guide`. The Tool Guide
injects the policy rules directly into the `analyze_travel_compliance` tool's description so the LLM
reads the rules and applies them when filtering options.

This happens **inside the agent module itself** — neither `main.py` nor the CLI needs to do anything
special. The same agent instance (with its policy already loaded) is used in both run modes.

The policy is **not** hardcoded in the tool — changing `.cuga/tool_guides/policy_employee.md`
changes the policy without touching any Python code.

---

## E2E Workflow

```text
User: "Plan a trip from NY to Boston, Mon–Fri next week"
  → Supervisor delegates to flight_agent  (search_flights via SerpAPI)
  → Supervisor delegates to hotel_agent   (search_hotels via SerpAPI)
  → Supervisor delegates to compliance_agent (analyze_travel_compliance + Tool Guide policy)
  → Supervisor presents filtered options as markdown tables

User: "I'll take the first compliant flight and hotel"
  → Supervisor delegates to approval_agent (send_slack_approval)
  → Supervisor confirms: "Approval request sent to your manager"
```

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.11+ | |
| CUGA installed | `pip install cuga` or from source |
| `OPENAI_API_KEY` | Or configure another LLM provider |
| `SERPAPI_API_KEY` | Free tier at [serpapi.com](https://serpapi.com) — required for real flight/hotel data |
| `SLACK_BOT_TOKEN` + `SLACK_MANAGER_USER_ID` | **Optional** — approval is simulated if not set |

Install extra dependencies:

```bash
# If running from the cuga project source (recommended):
uv pip install google-search-results   # SerpAPI client
uv pip install slack-sdk               # optional, for real Slack messages

# Or with plain pip:
pip install google-search-results
pip install slack-sdk
```

---

## Setup

```bash
# 1. Copy the example env file
cp docs/examples/travel_agent/.env.example docs/examples/travel_agent/.env

# 2. Fill in your keys
#    OPENAI_API_KEY=sk-...
#    SERPAPI_API_KEY=...
#    SLACK_BOT_TOKEN=xoxb-...        (optional)
#    SLACK_MANAGER_USER_ID=U...      (optional)
```

---

## Run Option 1 — SDK Script (terminal, no UI)

```bash
# From the project root
uv run docs/examples/travel_agent/main.py
```

`main.py` loads the supervisor from `config/supervisor_travel_agent.yaml` via
`CugaSupervisor.from_yaml()`, then runs a two-turn conversation in the terminal:

1. **Turn 1** — asks the supervisor to plan a trip from NY to Boston for next Mon–Fri.
   The supervisor searches flights, searches hotels, runs compliance filtering, and prints
   a markdown table of compliant options.
2. **Turn 2** — selects the first compliant flight and hotel and requests approval.
   The supervisor sends (or simulates) a Slack approval message.

The `.env` file in the example directory is loaded automatically by `main.py` before
importing CUGA, so no extra environment setup is needed beyond filling in the keys.

---

## Run Option 2 — CUGA Chat UI

```bash
# From the project root
cuga start travel_agent
# Open http://localhost:7860
```

This starts the CUGA registry and demo server with supervisor mode enabled, pointing at
`config/supervisor_travel_agent.yaml`. The CLI:

1. Sets `DYNACONF_SUPERVISOR__ENABLED=true` and `DYNACONF_SUPERVISOR__CONFIG_PATH` to the YAML.
2. Loads `docs/examples/travel_agent/.env` into the process environment so `SERPAPI_API_KEY`,
   `SLACK_BOT_TOKEN`, etc. are available to the server.
3. Resets the config database and saves a "Travel Agent" agent configuration.
4. Starts the registry and demo server processes.

The YAML loads the same Python agent instances via `import_from` — the compliance agent's
Tool Guide policy is already embedded in the agent at import time, so no additional policy
loading is needed.

> **Note:** `cuga start travel_agent` must be run from the **project root** so that the
> `docs.examples.travel_agent.*` import paths resolve correctly.

---

## Corporate Travel Policy

### How It Works

The compliance agent uses the **CUGA Tool Guide** policy mechanism — not hardcoded Python logic.

```text
.cuga/tool_guides/policy_employee.md
        ↓  loaded at module import time by
compliance_agent.py  →  agent.policies.add_tool_guide(target_tools=["analyze_travel_compliance"])
        ↓  injected into
analyze_travel_compliance tool description
        ↓  read by
LLM when it calls analyze_travel_compliance()
        ↓  applied as
filtering rules on flights and hotels
```

The `analyze_travel_compliance` tool **structures** the raw search results (parses JSON, derives
nights, normalises fields). The **LLM** reads the policy rules from the Tool Guide and decides
which options are compliant. This means:

- Policy changes require **no Python code changes** — only edit the markdown file.
- The policy is visible and auditable as a plain markdown document.
- Additional roles can be added by dropping new `policy_<role>.md` files into `.cuga/tool_guides/`.
  The compliance agent globs `policy_*.md` at startup and loads each one automatically.

### Current Policy Limits (Employee Role)

| Category | Rule | Limit |
|----------|------|-------|
| Flight | Max price per person | $500 |
| Flight | Allowed cabin classes | Economy only |
| Flight | Max layovers | 2 |
| Hotel | Max rate per night | $150 |
| Hotel | Min star rating | 3.0 ★ |
| Hotel | Required amenities | Free Wi-Fi |
| Budget | Max total trip cost | $2,000 |
| Approval | Auto-approve threshold | Under $1,000 |

### Changing the Policy

**To update limits** — edit [`.cuga/tool_guides/policy_employee.md`](.cuga/tool_guides/policy_employee.md):

```markdown
| Flight | Max price per person | $600 |   ← change this number
```

No Python code changes needed. The next run picks up the new limits automatically.

**To add a new role** — create a new file `.cuga/tool_guides/policy_manager.md` following the
same YAML frontmatter + markdown table format as `policy_employee.md`. The compliance agent
globs `policy_*.md` and loads every matching file at startup.

---

## File Structure

```text
travel_agent/
├── main.py                              # SDK entry point (terminal, no UI)
├── .env.example                         # Environment variable template
├── .env                                 # Your keys (git-ignored, copy from .env.example)
├── .cuga/
│   └── tool_guides/
│       ├── policy_employee.md           # Employee travel policy (Tool Guide, YAML frontmatter)
│       └── policy_manager.md            # Manager travel policy (Tool Guide, YAML frontmatter)
├── tools/
│   ├── flights.py                       # search_flights (SerpAPI Google Flights)
│   ├── hotels.py                        # search_hotels (SerpAPI Google Hotels)
│   ├── compliance.py                    # analyze_travel_compliance (structures data for LLM)
│   └── slack.py                         # send_slack_approval (real Slack + simulated fallback)
├── agents/
│   ├── flight_agent.py                  # CugaAgent — one tool: search_flights
│   ├── hotel_agent.py                   # CugaAgent — one tool: search_hotels
│   ├── compliance_agent.py              # CugaAgent — one tool: analyze_travel_compliance + Tool Guide
│   └── approval_agent.py                # CugaAgent — one tool: send_slack_approval
└── config/
    └── supervisor_travel_agent.yaml     # Supervisor config (agents + workflow instructions)
```

---
