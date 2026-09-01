<picture>
  <source media="(prefers-color-scheme: dark)" srcset="/docs/images/cuga-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="/docs/images/cuga-light.png">
  <img alt="CUGA" src="/docs/images/cuga-dark.png">
</picture>

<div align="center">

# CUGA: Configurable Generalist Agent — Agent Harness for the Enterprise

### Start with a generalist. Customize for your domain. Deploy faster!

Building a domain-specific enterprise agent from scratch is complex and requires significant effort: agent and tool orchestration, planning logic, safety and alignment policies, evaluation for performance/cost tradeoffs and ongoing improvements. CUGA is a state-of-the-art generalist agent designed with enterprise needs in mind, so you can focus on configuring your domain tools, policies and workflow.

---

[![🦉🤗 Try CUGA Live on Hugging Face Spaces](https://img.shields.io/badge/🦉🤗_Try_CUGA_Live_on_Hugging_Face_Spaces-FFD21E?style=for-the-badge)](https://huggingface.co/spaces/ibm-research/cuga-agent)

[![Python](https://shields.io/badge/Python-3.12-blue?logo=python&style=for-the-badge)](https://www.python.org/)
[![CugaAgent SDK](https://shields.io/badge/CugaAgent_SDK-Documentation-blue?logo=python&style=for-the-badge)](https://docs.cuga.dev/docs/sdk/cuga_agent/)
[![Status](https://shields.io/badge/Status-Active-success?logo=checkmarx&style=for-the-badge)]()
[![Documentation](https://shields.io/badge/Documentation-Available-blue?logo=gitbook&style=for-the-badge)](https://docs.cuga.dev)
[![Discord](https://shields.io/badge/Discord-Join-blue?logo=discord&style=for-the-badge)](https://discord.gg/aH6rAEEW)

[![AppWorld](https://img.shields.io/badge/%F0%9F%A5%87%20%231%20(07%2F25-02%2F26)%20on-AppWorld-gold?style=for-the-badge)](https://appworld.dev/leaderboard)
[![WebArena](https://img.shields.io/badge/%F0%9F%A5%87%20%231%20(02%2F25-09%2F25)%20on-WebArena-gold?style=for-the-badge)](https://docs.google.com/spreadsheets/d/1M801lEpBbKSNwP-vDBkC_pF7LdyGU1f_ufZb_NWNBZQ/edit?gid=0#gid=0)

</div>

---

> **Why CUGA?** — A generalist agent harness for the enterprise: wire your APIs and MCP servers, tune reasoning and task modes, and govern behavior with policies—without rebuilding orchestration from scratch.
>
> | Feature | How |
> |---------|-----|
> | **MCP, OpenAPI & LangChain tools** | [`mcp_servers.yaml`](src/cuga/backend/tools_env/registry/config/mcp_servers.yaml) · `CugaAgent(tools=[...])` |
> | **Code generation profiles** (fast / balanced / accurate) | `[features] cuga_mode` in [`settings.toml`](src/cuga/settings.toml) · [`configurations/modes/`](src/cuga/configurations/modes/) |
> | **Hybrid API + browser tasks** | `[advanced_features] mode = 'hybrid'` · Playwright + [browser extension](src/frontend_workspaces/extension/readme.md) |
> | **Multi-agent (CugaSupervisor)** | `cuga start demo_supervisor` · `[supervisor]` in [`settings.toml`](src/cuga/settings.toml) |
> | **Event-driven agents** (channels · triggers · standing flows) | `cuga start demo` **+** `python -m cuga.backend.events.service` — web chat, Slack/Discord/Telegram, webhooks, cron/poll/push flows armed from natural language with a human confirming each one, one supervisor agent over a YAML roster. Runs as **a second service beside CUGA** (:7860 + :8100); CUGA is unchanged when it is not deployed. [**Setup**](events_docs/SETUP.md) · [connector guides](events_docs/setup/) |
> | **A2A & remote agents** | External agent entries in supervisor config · [CugaSupervisor](https://docs.cuga.dev/docs/sdk/cuga_supervisor) |
> | **Policies & HITL** | [Policies SDK](https://docs.cuga.dev/docs/sdk/policies/) — Intent Guard, Playbook, Tool Approval, Tool Guide, Output Formatter |
> | **Manage & publish** | `cuga start manager` · draft tools, MCP, LLM, and policies in the web UI, then **publish** a versioned config for production chat ([details](#manage-publish-and-self-hosting)) |
> | **Reflection** | `[advanced_features] reflection_enabled` in [`settings.toml`](src/cuga/settings.toml) |
> | **Tool-call budgets** | `[advanced_features] max_tool_calls_per_block / _per_run / _per_thread` in [`settings.toml`](src/cuga/settings.toml) |
> | **Langflow** | Low-code visual workflows — integrates with CUGA ([langflow.org](https://www.langflow.org/)) |
> | **Knowledge** (RAG) | `enable_knowledge=True` (default) · ingest PDFs/Office/HTML/Markdown via **Docling** · **agent-level** + **session-level** scopes · `cuga start demo_knowledge` · [details](#knowledge-base) |
> | **Agent skills** | `SKILL.md` under `.cuga/skills` (default) · **`cuga start demo_skills`** (`sandbox_mode = "native"` by default, or **`opensandbox`**) · or **`demo --sandbox`** with `[skills]` on · [Agent skills](#agent-skills) |
> | **Self-host on a cluster** | Helm chart and deploy scripts in [`deployment/`](deployment/) · [Kubernetes guide](deployment/README.md) (local kind/minikube, or registry push for cloud clusters) |
>
> [SDK](https://docs.cuga.dev/docs/sdk/cuga_agent/) · [Policies](https://docs.cuga.dev/docs/sdk/policies/) · [Quick Start →](#quick-start)

## Why CUGA?

### Benchmark Performance

CUGA achieves state-of-the-art performance on leading benchmarks:

- **#1 on [AppWorld](https://appworld.dev/leaderboard)** (#1 from 07/25 - 02/26) — a benchmark with 750 real-world tasks across 457 APIs
- **#1 on [WebArena](https://docs.google.com/spreadsheets/d/1M801lEpBbKSNwP-vDBkC_pF7LdyGU1f_ufZb_NWNBZQ/edit?gid=0#gid=0)** (#1 from 02/25 - 09/25) — a complex benchmark for autonomous web agents across application domains

### Key Features & Capabilities

- **High-performing generalist agent** — Benchmarked on complex web and API tasks. Combines best-of-breed agentic patterns (e.g. planner-executor, code-act) with structured planning and smart variable management to prevent hallucination and handle complexity

- **Flexible agent and tool integration** — Seamlessly integrate tools via OpenAPI specs, MCP servers, and Langchain, enabling rapid connection to REST APIs, custom protocols, and Python functions

- **Integrates with Langflow** — Low-code visual build experience for designing and deploying agent workflows without extensive coding

- **Open-source and composable** — Built with modularity in mind, CUGA itself can be exposed as a tool to other agents, enabling nested reasoning and multi-agent collaboration. Evolving toward enterprise-grade reliability

- **Policy System** — Configure agent behavior with 5 policy types (Intent Guard, Playbook, Tool Approval, Tool Guide, Output Formatter) via the Python SDK or standalone UI in demo mode. Includes human-in-the-loop approval gates for safe agent behavior in enterprise contexts. See [SDK Docs](https://docs.cuga.dev/docs/sdk/cuga_agent/) and [Policies Guide](https://docs.cuga.dev/docs/sdk/policies/)

- **Agent skills** — Package domain workflows as `SKILL.md` files with frontmatter; the agent discovers them and loads full instructions on demand via the `load_skill` tool (see [Agent skills](#agent-skills))

- **Knowledge engine** — Built-in RAG over your documents: ingest PDFs, Office files, HTML, Markdown, and images through **Docling**, then search and reason over them via auto-injected knowledge tools. Documents can be scoped to **agent-level** (permanent, shared across conversations) or **session-level** (per-thread, isolated to a single conversation) — so long-lived reference material and ephemeral per-user uploads can coexist (see [Knowledge Base](#knowledge-base))

### Manage, publish, and self-hosting

**Manage and publish** — Run `cuga start manager` to start the manage-mode stack. You edit agent configuration (tools, MCP servers, LLM selection, policies) as a **draft**, try it in the draft chat, then **publish** to create a new version that production chat uses. Published versions are tracked so you can roll forward and audit what shipped.

**Self-host on Kubernetes** — The repo includes a Helm chart under [`deployment/helm/`](deployment/helm/), helper scripts such as [`deployment/deploy-local.sh`](deployment/deploy-local.sh), and documentation for building images, pushing to a registry, and wiring API keys via Kubernetes secrets for clusters such as kind, minikube, Docker Desktop Kubernetes, GKE, EKS, or AKS. See [deployment/README.md](deployment/README.md).

Explore the [Roadmap](#roadmap) to see what's ahead, or join the [Call for the Community](#call-for-the-community) to get involved.


## CUGA in Action

### Hybrid Task Execution

Watch CUGA seamlessly combine web and API operations in a single workflow:

**Example Task:** `get top account by revenue from digital sales, then add it to current page`

https://github.com/user-attachments/assets/0cef8264-8d50-46d9-871a-ab3cefe1dde5

<details>
<summary><b>Would you like to test this? (Advanced Demo)</b></summary>

Experience CUGA's hybrid capabilities by combining API calls with web interactions:

### Setup Steps:

1. **Switch to hybrid mode:**

   ```bash
   # Edit ./src/cuga/settings.toml and change:
   mode = 'hybrid'  # under [advanced_features] section
   ```

2. **Install browser API support:**

   - Installs playwright browser API and Chromium browser
   - The `playwright` installer should already be included after installing with [Quick Start](#quick-start)

   ```bash
   playwright install chromium
   ```

3. **Start the demo:**

   ```bash
   cuga start demo
   ```

4. **Enable the browser extension:**

   - Click the extension puzzle icon in your browser
   - Toggle the CUGA extension to activate it
   - This will open the CUGA side panel

5. **Open the test application:**

   - Navigate to: [Sales app](https://samimarreed.github.io/sales/)

6. **Try the hybrid task:**
   ```
   get top account by revenue from digital sales then add it to current page
   ```

**What you'll see:** CUGA will fetch data from the Digital Sales API and then interact with the web page to add the account information directly to the current page - demonstrating seamless API-to-web workflow integration!

</details>

## Quick Start

<details>
<summary><em style="color: #666;"> Prerequisites (click to expand)</em></summary>

- **Python 3.12+** - [Download here](https://www.python.org/downloads/)
- **uv package manager** - [Installation guide](https://docs.astral.sh/uv/getting-started/installation/)

</details>

```bash
# In terminal, clone the repository and navigate into it
git clone https://github.com/cuga-project/cuga-agent.git
cd cuga-agent

# 1. Create and activate virtual environment
uv venv --python=3.12 && source .venv/bin/activate

# 2. Install dependencies
uv sync

# 3. Set up environment variables
# Create .env file with your API keys
echo "OPENAI_API_KEY=your-openai-api-key-here" > .env

# 4. Start the demo
cuga start demo_crm --read-only

# Chrome will open automatically at https://localhost:7860
# then try sending your task to CUGA: 'from contacts.txt show me which users belong to the crm system'

# 5. View agent trajectories (optional)
cuga viz

# This launches a web-based dashboard for visualizing and analyzing
# agent execution trajectories, decision-making, and tool usage

```


<details>
<summary> LLM Configuration - Advanced Options</summary>

---

Refer to: [`.env.example`](.env.example) for detailed examples.

CUGA supports multiple LLM providers with flexible configuration options. You can configure models through TOML files or override specific settings using environment variables.

## Supported Platforms

- **OpenAI** - GPT models via OpenAI API (also supports LiteLLM via base URL override)
- **IBM WatsonX** - IBM's enterprise LLM platform
- **Azure OpenAI** - Microsoft's Azure OpenAI service
- **Groq** - High-performance inference platform with fast LLM models
- **RITS** - Internal IBM research platform
- **OpenRouter** - LLM API gateway provider
- **watsonx Orchestrate (wxO)** - Routes LLM calls through an Orchestrate tenant for tracing, guardrails, and entitlement-based billing

## Configuration Priority

1. **Environment Variables** (highest priority)
2. **TOML Configuration** (medium priority)
3. **Default Values** (lowest priority)

### Option 1: OpenAI 

**Setup Instructions:**

1. Create an account at [platform.openai.com](https://platform.openai.com)
2. Generate an API key from your [API keys page](https://platform.openai.com/api-keys)
3. Add to your `.env` file:
   ```env
   # OpenAI Configuration
   OPENAI_API_KEY=sk-...your-key-here...
   AGENT_SETTING_CONFIG="settings.openai.toml"

   # Optional overrides
   MODEL_NAME=gpt-4o                    # Override model name
   OPENAI_BASE_URL=https://api.openai.com/v1  # Override base URL
   OPENAI_API_VERSION=2024-08-06        # Override API version
   ```

**Default Values:**

- Model: `gpt-4o`
- API Version: OpenAI's default API Version
- Base URL: OpenAI's default endpoint

### Option 2: IBM WatsonX 

**Setup Instructions:**

1. Access [IBM WatsonX](https://www.ibm.com/watsonx)
2. Create a project or space and get your credentials:
   - Project ID or Space ID
   - API Key
   - Region/URL
3. Add to your `.env` file:

   ```env
   # WatsonX Configuration
   WATSONX_API_KEY=your-watsonx-api-key
   WATSONX_PROJECT_ID=your-project-id
   # WATSONX_SPACE_ID=your-space-id  # Alternative to WATSONX_PROJECT_ID
   WATSONX_URL=https://us-south.ml.cloud.ibm.com  # or your region
   AGENT_SETTING_CONFIG="settings.watsonx.toml"

   # Optional override
   MODEL_NAME=meta-llama/llama-4-maverick-17b-128e-instruct-fp8  # Override model for all agents
   ```

**Default Values:**

- Model: `meta-llama/llama-4-maverick-17b-128e-instruct-fp8`

### Option 3: Azure OpenAI

**Setup Instructions:**

1. Add to your `.env` file:
   ```env
    AGENT_SETTING_CONFIG="settings.azure.toml"  # Default config uses ETE
    AZURE_OPENAI_API_KEY="<your azure apikey>"
    AZURE_OPENAI_ENDPOINT="<your azure endpoint>"
    OPENAI_API_VERSION="2024-08-01-preview"
   ```

### Option 4: LiteLLM Support

CUGA supports LiteLLM through the OpenAI configuration by overriding the base URL:

1. Add to your `.env` file:

   ```env
   # LiteLLM Configuration (using OpenAI settings)
   OPENAI_API_KEY=your-api-key
   AGENT_SETTING_CONFIG="settings.openai.toml"

   # Override for LiteLLM
   MODEL_NAME=Azure/gpt-4o              # Override model name
   OPENAI_BASE_URL=https://your-litellm-endpoint.com  # Override base URL
   OPENAI_API_VERSION=2024-08-06        # Override API version
   ```
### Option 5: Groq Support 

**Setup Instructions:**

1. Create an account at [groq.com](https://groq.com)
2. Generate an API key from your [API keys page](https://console.groq.com/keys)
3. Add to your `.env` file:
   ```env
   # Groq Configuration
   GROQ_API_KEY=your-groq-api-key-here
   AGENT_SETTING_CONFIG="settings.groq.toml"
   
   # Optional override
   MODEL_NAME=llama-3.1-70b-versatile  # Override model name
   ```

**Default Values:**

- Model: Configured in `settings.groq.toml`
- Base URL: Groq's default endpoint

### Option 6: OpenRouter Support
**Setup Instructions:**
1. Create an account at [openrouter.ai](https://openrouter.ai)
2. Generate an API key from your account settings
3. Add to your `.env` file:
   ```env
   # OpenRouter Configuration
   OPENROUTER_API_KEY=your-openrouter-api-key
   AGENT_SETTING_CONFIG="settings.openrouter.toml"
   OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"
    # Optional override
   MODEL_NAME=openai/gpt-4o                    # Override model name
    ```

### Option 7: RITS Support
**Setup Instructions:**
1. Obtain a RITS API key from the RITS platform admin.
2. Add to your `.env` file:
   ```env
   # RITS Configuration — direct RITS endpoint (default preset)
   RITS_API_KEY=your-rits-api-key  # pragma: allowlist secret
   AGENT_SETTING_CONFIG="settings.rits.toml"

   # Optional overrides — update MODEL_NAME and RITS_BASE_URL together for
   # direct RITS setups, since each model has a model-specific URL path.
   # Setting MODEL_NAME alone will leave you pointed at the previous model's URL.
   MODEL_NAME=google/gemma-4-31B-it
   RITS_BASE_URL="https://inference-3scale-apicast-production.apps.rits.fmaas.res.ibm.com/google-gemma-4-31b-it-a100/v1"
   ```

To front RITS with a local LiteLLM proxy instead, use `AGENT_SETTING_CONFIG="settings.rits.proxy.toml"`.

**Default Values:**

- Model: `google/gemma-4-31B-it` (direct preset)
- Base URL: gemma-4-31B-it `/v1` endpoint on the RITS 3scale apicast host (direct preset)
- Local proxy URL: `http://localhost:4000` (proxy preset)

### Option 8: watsonx Orchestrate (wxO) Support

Routes every agent role's LLM calls through your watsonx Orchestrate tenant instead of a
provider directly — calls become traced/observable in the tenant, pass through its
guardrails, and are billed against its entitlement.

**Setup Instructions:**

1. Install the optional SDK (requires Python >= 3.11): `pip install "cuga[wxo]"` or `uv sync --extra wxo`.
2. In watsonx Orchestrate, go to **Settings -> API details** and click **Generate API key**; copy the **Service instance URL** shown on the same page.
3. Add to your `.env` file:
   ```env
   # watsonx Orchestrate Configuration
   WXO_API_KEY=your-wxo-api-key  # pragma: allowlist secret
   WXO_INSTANCE_URL=https://api.dl.watson-orchestrate.ibm.com/instances/your-instance-id
   AGENT_SETTING_CONFIG="settings.wxo.toml"

   # Optional override — model ids are provider-prefixed per your tenant's Settings -> Models list
   MODEL_NAME=groq/openai/gpt-oss-120b  # e.g. the Groq-hosted variant, ~25% faster than the Bedrock one
   ```

To target a local ADK dev server instead of a hosted tenant, set `WXO_INSTANCE_URL=http://localhost:4321`
and omit `WXO_API_KEY` — local instances are auto-detected and don't require a key.

**Default Values:**

- Model: `watsonx/openai/gpt-oss-120b`
- Instance URL: `http://localhost:4321` (local ADK dev server)


## Configuration Files

CUGA uses TOML configuration files located in `src/cuga/configurations/models/`:

- `settings.openai.toml` - OpenAI configuration (also supports LiteLLM via base URL override)
- `settings.watsonx.toml` - WatsonX configuration
- `settings.azure.toml` - Azure OpenAI configuration
- `settings.groq.toml` - Groq configuration
- `settings.openrouter.toml` - OpenRouter configuration
- `settings.rits.toml` - RITS configuration (direct endpoint)
- `settings.rits.proxy.toml` - RITS configuration (local LiteLLM proxy fronting RITS)
- `settings.wxo.toml` - watsonx Orchestrate configuration

Each file contains agent-specific model settings that can be overridden by environment variables.

</details>

<div style="margin: 20px 0; padding: 15px; border-left: 4px solid #2196F3; border-radius: 4px;">

**Tip:** Want to use your own tools or add your MCP tools? Check out [`src/cuga/backend/tools_env/registry/config/mcp_servers.yaml`](src/cuga/backend/tools_env/registry/config/mcp_servers.yaml) for examples of how to configure custom tools and APIs, including those for digital sales.

</div>

## Agent skills

Agent skills are reusable instruction packs: each skill is a `SKILL.md` file with YAML frontmatter and markdown body. CUGA discovers them at startup, lists short descriptions in the agent prompt, and exposes a **`load_skill`** tool so the model pulls the full body only when a task matches that skill—similar to opening a playbook instead of stuffing every procedure into the system prompt.

**Where skills live**

Configure a single root in [`settings.toml`](src/cuga/settings.toml) (`[skills] root`, default **`cuga`**) or via **`DYNACONF_SKILLS__ROOT`** env var. CUGA scans one directory only — no merge across paths.

| `skills.root` | Project path | Use when |
| ------------- | ------------ | -------- |
| `cuga` (default) | `<CUGA folder>/skills/**/SKILL.md` (e.g. `.cuga/skills/`) | CUGA-native layout; keeps skills with other CUGA config |
| `agents` | `.agents/skills/**/SKILL.md` | [skills.sh](https://skills.sh) / `npx skills` universal installs |
| `global_agents` | `~/.config/agents/skills/` | Global `npx skills -g` installs |
| `global_cuga` | `~/.config/cuga/skills/` | Legacy global CUGA path |

**Why default `cuga`?** CUGA already uses `.cuga/` for policy, workspace, and uploads. Keeping skills there avoids colliding with other agents that write `.agents/skills/`. If you install skills with `npx skills`, set `root = "agents"` or copy skills into `.cuga/skills/`.

**`SKILL.md` shape**

Frontmatter must include **`name`** and **`description`** (shown in the available-skills list). You can add optional **`requirements`** (string or list). The markdown below the frontmatter is the full instruction text returned by `load_skill`.

**Try it**

From the repository root:

```bash
npx skills add https://github.com/anthropics/skills --skill pptx -a universal
cuga start demo_skills
```

That preset turns on skills for the run and uses **`[advanced_features] sandbox_mode`** in [`settings.toml`](src/cuga/settings.toml) (default **`native`**). For **`opensandbox`**, run **`uv sync --extra opensandbox`** first so the client deps are installed and OpenSandbox can be reached.

For Docker/Podman isolation instead, use **`uv sync --group sandbox`** then **`cuga start demo --sandbox`** and enable **`[skills]`**—see [Configurations](#configurations).

For settings you keep beyond a one-off run, configure `[skills]` and `[advanced_features]` in [`settings.toml`](src/cuga/settings.toml) (Dynaconf env overrides apply as documented there).

**Install a sample skill (Anthropic `pptx`)**

The [Anthropic skills repo](https://github.com/anthropics/skills) publishes ready-made folders such as [`skills/pptx`](https://github.com/anthropics/skills/tree/main/skills/pptx) (`SKILL.md`, scripts, and helper markdown). Install the `pptx` skill into the project-local universal agent skills folder from the repository root:

```bash
npx skills add https://github.com/anthropics/skills --skill pptx -a universal
```

This creates `.agents/skills/pptx/SKILL.md` for the current project (or set `[skills] root = "agents"` in `settings.toml`). To use the CUGA default layout instead, copy or symlink skills into `.cuga/skills/`. Restart `cuga start demo_skills` (or your app) so skills are rescanned. Add `-g` if you want the skill installed globally under `~/.config/agents/skills/` instead.

---

## Using CUGA as a Python SDK

CUGA can be easily integrated into your Python applications as a library. The SDK provides a clean, minimal API for creating and invoking agents with custom tools.

**SDK Documentation**: [SDK Documentation](https://docs.cuga.dev/docs/sdk/cuga_agent/)

### Quick Start

```python
from cuga import CugaAgent
from langchain_core.tools import tool
import asyncio

@tool
def add_numbers(a: int, b: int) -> int:
    '''Add two numbers together'''
    return a + b

@tool
def multiply_numbers(a: int, b: int) -> int:
    '''Multiply two numbers together'''
    return a * b

# Create agent with tools
agent = CugaAgent(tools=[add_numbers, multiply_numbers])


async def main():
    # Add an Intent Guard to block specific operations
    await agent.policies.add_intent_guard(
        name="Block Delete Operations",
        description="Prevents deletion of critical data",
        keywords=["delete", "remove", "erase"],
        response="Deletion operations are not permitted for security reasons.",
        priority=100  # Higher priority = checked first
    )

    # Add a Playbook to provide step-by-step guidance for complex workflows
    await agent.policies.add_playbook(
        name="Budget Analysis Workflow",
        description="Multi-step process for analyzing financial budgets",
        natural_language_trigger=["When user asks to analyze their budget"],
        content="""# Budget Analysis Workflow

    ## Step 1: Calculate Total Expenses
    - Sum all expense categories using add_numbers
    - Document each category amount

    ## Step 2: Calculate Total Revenue
    - Sum all revenue streams using add_numbers
    - Include all income sources

    ## Step 3: Calculate Profit Margin
    - Use multiply_numbers to calculate profit (revenue - expenses)
    - Calculate margin percentage

    ## Step 4: Generate Recommendations
    - Compare against target budget
    - Identify areas for optimization
    - Provide actionable insights""",
        priority=50
    )

    result = await agent.invoke("Analyze my budget: expenses are 5000 and 3000, revenue is 12000")
    print(result.answer)  # The agent's response

if __name__ == "__main__":
    asyncio.run(main())
```

### Key Features

- **Simple API**: `CugaAgent(tools=[...])` → `await agent.invoke(message)`
- **Streaming**: Monitor execution in real-time with `agent.stream()`
- **State Isolation**: Per-user sessions with `thread_id`
- **LangGraph Integration**: Access underlying graph for advanced use cases
- **Flexible Tools**: Direct tools or custom tool providers
- **Policy System**: Comprehensive policy framework with 5 types:
  - **Intent Guard**: Block or modify specific user intents
  - **Playbook**: Step-by-step guidance for complex workflows
  - **Tool Approval**: Require human approval before executing tools
  - **Tool Guide**: Enhance tool descriptions with additional context
  - **Output Formatter**: Format agent responses based on triggers

**Documentation**: [SDK Guide](https://docs.cuga.dev/docs/sdk/cuga_agent/) | [Policies Guide](https://docs.cuga.dev/docs/sdk/policies/)

### Run Receipt

Answer "where did the tokens and the time go?" without an external observability
stack. Enable the flag and every `invoke()` returns a per-run receipt:

```toml
# settings.toml
[advanced_features]
run_receipt = true  # default: false — zero overhead when disabled
```

```python
result = await agent.invoke("how many accounts are there?")
print(result.receipt)
# ┌─ Run Receipt ─────────────────────────────────┐
# │ model: gpt-4o                                 │
# │ tokens: 18,342 in / 2,101 out (20,443)        │
# │ llm calls: 7   tool calls: 4                  │
# │ time: 9.4s (llm 6.1s / tools 2.8s)            │
# │ slowest tool: get_accounts 1.9s               │
# └───────────────────────────────────────────────┘
result.receipt.input_tokens    # 18342
result.receipt.tool_timings    # per-tool call counts and total durations
```

**Tokens, not cost** — CUGA runs against self-hosted and internal deployments
whose prices we don't know, so multiply by your own rates. `cache_read_tokens`
and `reasoning_tokens` are included when the provider reports them.

Enabling it puts tool tracking in a **timings-only** mode unless you passed
`track_tool_calls=True`: only name, app and duration are recorded — never
arguments, results or errors. Coverage matches `track_tool_calls` (registry/MCP
tools and `@tracked_tool` functions).

### Knowledge Base

CUGA includes a built-in knowledge base powered by LangChain and local vector stores. **Docling** is integrated for document ingestion: it parses and normalizes PDFs, Office files, HTML, Markdown, images, and other supported types before chunking and embedding, so the pipeline stays self-contained with no external document services.

When enabled, the agent can search, ingest, and manage documents.

**Try the knowledge demo:** same as the main demo but with the knowledge engine on (upload documents and query them):

```bash
cuga start demo_knowledge
```

> Walk through a full HR-Benefits demo with sample documents and example prompts:
> **[docs/examples/knowledge_demo/](./docs/examples/knowledge_demo)**

Knowledge is **enabled by default** via `settings.toml`. The SDK auto-injects knowledge tools
and awareness into the agent, so it knows what documents are available and how to search them.

#### Programmatic Access

```python
from cuga import CugaAgent
import asyncio

agent = CugaAgent(enable_knowledge=True)

async def main():
    # Ingest a document
    await agent.knowledge.ingest("/path/to/quarterly_report.pdf")

    # The agent now automatically knows about this document
    result = await agent.invoke("What does the report say about Q4 revenue?")
    print(result.answer)  # Agent searches knowledge base and answers

    # Direct search
    results = await agent.knowledge.search("Q4 revenue figures")
    for r in results:
        print(f"{r['filename']} (page {r['page']}): {r['text'][:100]}")

    # List documents
    docs = await agent.knowledge.list_documents()

    # Clean up
    await agent.aclose()

asyncio.run(main())
```

#### Session-Scoped Knowledge

Documents can be scoped to a specific conversation thread:

```python
thread_id = "user-session-123"

# Ingest into session scope (temporary, per-conversation)
await agent.knowledge.ingest("/path/to/file.pdf", scope="session", thread_id=thread_id)

# Search session documents
results = await agent.knowledge.search("query", scope="session", thread_id=thread_id)

# Agent scope (default) — permanent, shared across conversations
await agent.knowledge.ingest("/path/to/file.pdf", scope="agent")
```

#### Disabling Knowledge

```python
agent = CugaAgent(tools=[my_tools], enable_knowledge=False)
```

#### Supported Document Types

PDF, DOCX, XLSX, PPTX, HTML, Markdown, images, and more (via Docling).

#### Embedding providers + tuning

The knowledge engine ships four built-in provider categories — `fastembed`
(default, local), `huggingface` (local), `openai` (network, accepts any
OpenAI-compatible endpoint via `base_url`), and `ollama` (network) — plus
`openrouter` for one-key-many-models access to embedding models on
[openrouter.ai/models](https://openrouter.ai/models?output_modalities=embeddings).
Provider, model, batch size, and concurrency are all set under
`[knowledge.embeddings]` in `settings.toml` or via CLI overrides
(`--embeddings-provider`, `--embeddings-base-url`, `--embeddings-api-key`,
`--embeddings-model`, `--embeddings-batch-size`, `--embeddings-concurrency`).

> **Full provider matrix + tuning guide** — see the
> [knowledge engine docs](https://docs.cuga.dev/docs/sdk/knowledge/).
> Switching provider or model invalidates existing vectors (different
> dim), so the manage UI surfaces a "Re-index recommended" banner
> automatically.

---

## CugaSupervisor (Multi-Agent)

Orchestrate multiple agents with a single supervisor: delegate tasks to specialized sub-agents, mix local agents with remote A2A agents, and pass data between them.

**Documentation**: [CugaSupervisor](https://docs.cuga.dev/docs/sdk/cuga_supervisor)

**Try the supervisor demo:** run the multi-agent demo (CRM + email sub-agents) with:

```bash
cuga start demo_supervisor
```

> [!IMPORTANT]
> **Sub-agents are scoped to the registry apps they name.** An agent that lists `apps:` or
> `mcp_servers:` receives tools from *those* only; an agent that lists neither still receives
> everything.
>
> This was not always true. `_create_tool_provider` used to build the tool provider unscoped and
> merely log the names, so every sub-agent silently got the whole registry and the declaration was
> decorative. If a roster named one server while relying on tools from another, it worked by
> accident and will now see only what it names — add the missing entry to `apps:`/`mcp_servers:`.
> Hyphenated names are mapped to the registry's underscore keys (`cuga-finance` → `cuga_finance`).

### Quick Start

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

### Supervisor features

- **Delegation**: Supervisor hands work to sub-agents and can pass variables between them when needed.
- **Internal + external**: Combine local `CugaAgent` instances with external agents via **A2A**, task-only or variables in metadata if enabled.
- **Variable passing**: Use `variables=["var_name"]` to pass previous agent outputs or context to the next agent (for internal agents, or A2A when `pass_variables_a2a` is enabled in settings).
- **Agent cards**: For A2A agents, capabilities and description are taken from the agent card and shown in the supervisor prompt.

You can also load agents from YAML with `CugaSupervisor.from_yaml("path/to/config.yaml")`. Enable the supervisor in `settings.toml` under `[supervisor]` when using the server.

---

## Configurations

<details>
<summary> Running with a secure code sandbox</summary>

Cuga supports isolated code execution using Docker/Podman containers for enhanced security.

1. **Install container runtime**: Download and install [Rancher Desktop](https://rancherdesktop.io/) or Docker.

2. **Install sandbox dependencies**:

   ```bash
   uv sync --group sandbox
   ```

3. **Start with remote sandbox enabled**:

   ```bash
   cuga start demo --sandbox
   ```

   This automatically configures Cuga to use Docker/Podman for code execution instead of local execution.

4. **Test your sandbox setup** (optional):

   ```bash
   # Test local sandbox (default)
   cuga test-sandbox

   # Test remote sandbox with Docker/Podman
   cuga test-sandbox --remote
   ```

   You should see the output: `('test succeeded\n', {})`

**Note**: Without the `--sandbox` flag, Cuga uses local Python execution (default), which is faster but provides less isolation.

</details>

<details>
<summary> Running with E2B Cloud Sandbox</summary>

CUGA supports [E2B](https://e2b.dev) for cloud-based code execution in secure, ephemeral sandboxes. This provides better isolation than local execution while being faster than Docker/Podman containers.

### Prerequisites:

1. **Get an E2B API key**:
   - Sign up at [e2b.dev](https://e2b.dev)
   - Create an API key from your [dashboard](https://e2b.dev/dashboard)

2. **Set up the E2B template**:
   ```bash
   # Install E2B CLI
   npm install -g @e2b/cli

   # Login with your API key
   e2b auth login

   # Create a template (one-time setup)
   # This creates a 'cuga-langchain' template that CUGA uses
   e2b template build --name cuga-langchain
   ```

3. **Install E2B dependencies**:
   ```bash
   uv sync --group e2b
   ```

4. **Configure environment**:
   Add to your `.env` file:
   ```env
   E2B_API_KEY=your-e2b-api-key-here
   ```

### Exposing Registry to E2B (Required)

E2B runs in the cloud and needs to call your local API registry to execute tools. You need to expose your local registry publicly using a tunneling service like [ngrok](https://ngrok.com).

#### Option 1: Expose Registry Directly (Port 8001)

Best if you have multiple ports available:

```bash
# In a separate terminal, start ngrok tunnel to registry
ngrok http 8001

# You'll get a public URL like: https://abc123.ngrok.io
# Copy this URL
```

Then edit `./src/cuga/settings.toml`:
```toml
[server_ports]
function_call_host = "https://abc123.ngrok.io"  # Your ngrok URL
```

#### Option 2: Expose CUGA Port with Proxy (Port 7860)

Best if you're restricted to 1 port - CUGA will proxy calls to the registry:

```bash
# In a separate terminal, start ngrok tunnel to CUGA
ngrok http 7860

# You'll get a public URL like: https://xyz789.ngrok.io
# Copy this URL
```

Then edit `./src/cuga/settings.toml`:
```toml
[server_ports]
function_call_host = "https://xyz789.ngrok.io"  # Your ngrok URL
```

CUGA automatically proxies `/functions/call` requests to the registry when using the CUGA port.

### Enable E2B in Settings

Edit `./src/cuga/settings.toml`:
```toml
[advanced_features]
e2b_sandbox = true
e2b_sandbox_mode = "per-session"  # Options: "per-session" | "single" | "per-call"
e2b_sandbox_ttl = 600  # Cache TTL in seconds (10 minutes)
```

### Sandbox Modes:

- **`per-session`** (default): One sandbox per conversation thread, cached for reuse
- **`single`**: Single shared sandbox across all threads (most cost-effective)
- **`per-call`**: New sandbox for each execution (most isolated, highest cost)

### Start CUGA with E2B:

```bash
# Make sure ngrok is running in another terminal
cuga start demo
```

E2B will automatically execute code in cloud sandboxes. You'll see logs indicating "CODE SENT TO E2B SANDBOX" when E2B is active.

### Troubleshooting:

- **Error: "function_call_host not configured"**: Make sure you've set `function_call_host` in settings.toml with your ngrok URL
- **Tool execution fails**: Verify ngrok is running and the URL in settings.toml matches your ngrok URL
- **Connection timeout**: Check that your firewall allows ngrok connections

**Benefits of E2B**:
- No Docker/Podman required
- Faster than container-based sandboxing
- Cloud-native with automatic scaling
- Better isolation than local execution
- Supports per-session caching for cost optimization

**Note**: E2B is a paid service with a free tier. Check [e2b.dev/pricing](https://e2b.dev/pricing) for details.

</details>

<details>
<summary> Code generation profiles - fast / balanced / accurate</summary>

## Available profiles under `./src/cuga/configurations/modes`

| Profile    | File                                   | Description                                     |
| ---------- | -------------------------------------- | ----------------------------------------------- |
| `fast`     | `./configurations/modes/fast.toml`     | Optimized for speed                             |
| `balanced` | `./configurations/modes/balanced.toml` | Balance between speed and precision _(default)_ |
| `accurate` | `./configurations/modes/accurate.toml` | Optimized for precision                         |
| `custom`   | `./configurations/modes/custom.toml`   | User-defined settings                           |

These profiles tune code generation, reflection, and related feature flags. Graph routing is handled by the entry graph (CugaLite, CugaSupervisor, or CugaBrowser).

</details>

<details>
<summary> Task Mode Configuration - Switch between API/Web/Hybrid modes</summary>

## Available Task Modes

| Mode     | Description                                                                 |
| -------- | --------------------------------------------------------------------------- |
| `api`    | API-only mode - executes API tasks _(default)_                              |
| `web`    | Web-only mode - executes web tasks using browser extension                  |
| `hybrid` | Hybrid mode - executes both API tasks and web tasks using browser extension |

## How Task Modes Work

### API Mode (`mode = 'api'`)

- Opens tasks in a regular web browser
- Best for API/Tools-focused workflows and testing

### Web Mode (`mode = 'web'`)

- Interface inside a browser extension (available next to browser)
- Optimized for web-specific tasks and interactions
- Direct access to web page content and controls

### Hybrid Mode (`mode = 'hybrid'`)

- Opens inside browser extension like web mode
- Can execute both API/Tools tasks and web page tasks simultaneously
- Starts from configurable URL defined in `demo_mode.start_url`
- Most versatile mode for complex workflows combining web and API operations

## Configuration

Edit `./src/cuga/settings.toml`:

```toml
[demo_mode]
start_url = "https://opensource-demo.orangehrmlive.com/web/index.php/auth/login"  # Starting URL for hybrid mode


[advanced_features]
mode = 'api'  # 'api', 'web', or 'hybrid'
```

</details>

<details>
<summary>🔍 Tool Shortlisting</summary>

## What it is

When an app exposes many tools, CUGA shrinks the set before the model sees it. This happens in two places: `find_tools` (the agent asking "what tools exist for X?") and the `bind_tools` provider cap. Both are pluggable.

## Strategies

| Strategy | How it ranks | Cost |
|---|---|---|
| `llm` (default) | asks the model | one LLM call per shortlist |
| `embedding` | local cosine similarity | no LLM call for ranking, ~65ms warm |
| `hybrid` | cosine cuts to `top_k`, then the LLM picks | one LLM call, much smaller prompt |

`embedding` compares one vector built from your question against one vector per tool (name + description + parameter names + return field names). It is strong at **recall** but weak at separating near-identical tools such as `get_contacts` (list) and `get_contact` (by id) — so `hybrid` is usually the better choice for discovery, and `embedding` for the provider cap where only "don't drop the needed tool" matters.

Embeddings are local by default (`BAAI/bge-small-en-v1.5` via fastembed) — the same weights knowledge and policy already load, so there is one ONNX session and nothing extra for airgapped preload to fetch. Ranking makes no network call and is not billed. Setting `embedding_provider = "openai"` trades that away for a hosted model. Either way the *agent* still calls an LLM for its own reasoning — only the shortlister's ranking step is affected. The first call while a local model is still downloading is served by `fallback_strategy`, so **a query never waits on a download**.

In **server mode** the catalogue is embedded at startup and again whenever the tool list changes, so the first `find_tools` after boot uses cosine rather than falling back. Vectors are keyed by content hash, so adding a tool embeds one document rather than the whole catalogue. The SDK stays lazy.

## Configuration

### Every option

| Key | Default | What it does |
|---|---|---|
| `strategy` | `"llm"` | `llm` \| `embedding` \| `hybrid` \| a dotted class path |
| `fallback_strategy` | `"llm"` | Used when `strategy` cannot run (model still downloading, missing dependency) |
| `threshold` | `128` | Engage the cosine stage only **above** this many candidates. `0` = always engage |
| `top_k` | `128` | How many candidates the cosine stage keeps (also the `hybrid` prefilter width) |
| `max_results` | `10` | `find_tools` only — how many tools are actually shown to the agent |
| `min_score` | `0.15` | Cosine floor. Deliberately low: this is a **recall** filter, not a precision knob |
| `query_weight` | `0.7` | Blend of step query vs. initial user message when embedding the query (0–1) |
| `embedding_provider` | `"local"` | `local` (fastembed, offline, free) or `openai` |
| `embedding_model` | `bge-small-en-v1.5` | Any fastembed model, or an OpenAI embedding model |

### Four ways to set it

**Precedence, highest first:**

1. raw `shortlister_*` keys you set yourself in `configurable`
2. per-invoke `invoke(..., shortlister=...)`
3. constructor `CugaAgent(shortlister=...)`
4. `[shortlister.discovery]` / `[shortlister.bind_cap]`
5. `[shortlister]` — also where environment variables land
6. built-in defaults

Raw keys sit **above** the SDK objects because `_apply_shortlister` merges with `setdefault`: a key you already placed in `configurable` is never overwritten.

**1. `settings.toml`** — deployment-wide:

```toml
[shortlister]
strategy = "hybrid"
top_k = 64

# Optional: different settings per call site.
[shortlister.discovery]   # find_tools
strategy = "hybrid"
[shortlister.bind_cap]    # bind_tools provider cap
strategy = "embedding"    # no LLM round-trip per call_model
```

**2. Environment** — one-off or per-container:

```bash
DYNACONF_SHORTLISTER__STRATEGY=hybrid
DYNACONF_SHORTLISTER__TOP_K=64
```

**3. SDK** — per agent, or per call:

```python
from cuga import CugaAgent, Shortlister

agent = CugaAgent(tools=[...], shortlister=Shortlister(strategy="hybrid"))
await agent.invoke("...", shortlister=Shortlister(top_k=32))   # per-call override
```

**4. Raw `configurable`** — when driving the graph directly:

```python
config = {"configurable": {"shortlister_strategy": "embedding", "shortlister_top_k": 32}}
```

### Which strategy should I use?

| Situation | Use | Why |
|---|---|---|
| Default / unsure | `llm` | Unchanged behavior; nothing to tune |
| Large catalogue, accuracy matters | `hybrid` | Cosine cuts the prompt cost; the LLM still makes the final call |
| Provider cap on every `call_model` | `embedding` | Removes an LLM round-trip per step, and works on models without native structured output |
| Offline / airgapped | `embedding` or `hybrid` with a warm cache | Local model, no network at query time |

### Gotchas

**At or below `threshold` candidates nothing changes** — the cosine stage does not run and shortlisting behaves exactly as it always has. Set `threshold = 0` to always engage the configured strategy.

> **Two similarly-named thresholds.** `[shortlister] threshold` (128) decides when the *cosine stage* engages inside the shortlister. `advanced_features.shortlisting_tool_threshold` (35) decides when tools are *hidden behind `find_tools`* in the prompt. They are unrelated.

**`top_k` never raises the provider cap.** At the `bind_tools` seam the caller's cap is a hard ceiling; a configured `top_k` can lower how many tools are bound, never push past what the provider accepts.

**`max_results` exists for a reason.** `find_tools` renders each tool with full parameter and schema docs. Showing 128 of them would exceed `advanced_features.execution_output_max_length` and be silently truncated mid-render, so the rendered count is capped separately from `top_k`.

**First call on a cold cache.** The embedding model (~90MB) downloads in the background and that call is served by `fallback_strategy`; subsequent calls use cosine. No query ever blocks on the download.

## Custom strategies

Point `strategy` at a dotted class path, or pass an instance:

```python
class MyShortlister:
    name = "mine"
    async def shortlist(self, request) -> "list[ShortlistCandidate]": ...

agent = CugaAgent(tools=[...], shortlister=Shortlister(instance=MyShortlister()))
```

See `docs/design/pluggable-shortlister.md` for the full design.

</details>

<details>
<summary>📝 Special Instructions Configuration</summary>

## How It Works

Custom instructions support `## Plan` for execution guidance and `## Answer` for final-response formatting. Unsectioned text is treated as plan guidance. The default instruction set keeps reusable answer instructions in `answer.md`; runtime plan instructions can be supplied by clients such as Langflow.

## Configuration

```
configurations/
└── instructions/
    ├── instructions.toml
    ├── default/
    │   └── answer.md
    └── [other instruction sets]/
```

Edit `configurations/instructions/instructions.toml`:

```toml
[instructions]
instruction_set = "default"  # or any instruction set above
```

</details>

<details>
<summary><em style="color: #666;"> 🧠 Optional: Use Evolve with CUGA</em></summary>

Evolve can bring task-specific guidance into the prompt before execution and save completed trajectories after the run.

This flow is:

- **Opt-in** - disabled by default
- **Non-blocking** - Evolve failures do not fail the task
- **Optional integration** - install `cuga[evolve]` if you want the upstream Evolve package available locally, or let `uvx` fetch it on demand

### Setup Steps:

1. Choose how Evolve will be started.
  Recommended for normal CUGA usage: let the CUGA MCP registry launch Evolve for you.
   In the manager UI, add an MCP tool with:
  - Name: `evolve`
  - Connection type: `Command (stdio)`
  - Command: `uvx`
  - Args: `--from altk-evolve --with setuptools<70 evolve-mcp`
   Important: this command starts Evolve in `stdio` mode through the upstream Evolve package. It is intended to be launched by the CUGA registry, not run manually in a separate terminal.
   Alternative for standalone/manual debugging: run Evolve yourself as an SSE server:
   If you run Evolve from a checked-out `altk-evolve` repo instead of `uvx`, install the Postgres extras first with `uv sync --extra pgvector`.
2. Add these environment values in the MCP tool UI:

```env
EVOLVE_BACKEND=postgres
EVOLVE_PG_HOST=localhost
EVOLVE_PG_PORT=5432
EVOLVE_PG_USER=postgres
EVOLVE_PG_PASSWORD=postgres
EVOLVE_PG_DBNAME=evolve
EVOLVE_MODEL_NAME=Azure/gpt-4o
OPENAI_API_KEY=env://OPENAI_API_KEY
OPENAI_BASE_URL=env://OPENAI_BASE_URL
```

Each `env://...` value tells CUGA to read the real secret or setting from its own process environment at runtime, so make sure PostgreSQL is reachable, `pgvector` is available, and the configured OpenAI/LiteLLM-compatible model is one your gateway is allowed to use.

3. **[Optional]** Edit `./src/cuga/settings.toml` to configure Evolve:

```toml
[evolve]
enabled = true
url = "http://127.0.0.1:8201/sse"
mode = "auto"
app_name = "evolve"
save_on_success = true
save_on_failure = true
async_save = true
timeout = 30.0
```

If you use the recommended registry-managed setup above, keep `mode = "auto"` or set `mode = "registry"`.

If you run Evolve manually as a standalone SSE server, keep `url = "http://127.0.0.1:8201/sse"` and set `mode = "direct"` if you want to skip registry lookup entirely.

If you use Evolve tip generation, make sure the environment for the Evolve MCP server includes the required Evolve model settings. Otherwise `save_trajectory` may fail later with a LiteLLM/OpenAI model access error even when the MCP connection itself works.

1. Start the same CRM demo with sample workspace files:

```bash
cuga start demo_crm --sample-memory-data
```

1. Run a task that routes through CugaLite, for example:

```text
Identify the common cities between my cuga_workspace/cities.txt and cuga_workspace/company.txt
```

### What happens during a run?

1. CUGA derives the task description from the current sub-task or first user message
2. CUGA asks Evolve for relevant guidelines
3. Returned guidelines are appended to the system prompt under an `Evolve Guidelines` section
4. The task executes normally
5. The user / assistant trajectory is saved back to Evolve after completion

### Notes

- `async_save = true` saves trajectories in the background and avoids blocking the response
- `save_on_success` and `save_on_failure` let you control which runs are recorded
- `mode = "auto"` lets CUGA use a registry-managed Evolve MCP server when available and fall back to the direct SSE URL otherwise
- `mode = "registry"` is best when you want Evolve to be fully managed as a normal CUGA MCP tool
- `mode = "direct"` is best when you are manually running an SSE Evolve server outside CUGA
- If Evolve is unavailable, times out, or returns no guidance, CUGA continues normally

</details>

## Advanced Usage

<details>
<summary><b> Adding Tools: Comprehensive Examples</b></summary>

CUGA supports three types of tool integrations. Each approach has its own use cases and benefits:

## **Tool Types Overview**

| Tool Type     | Best For                               | Configuration      | Runtime Loading |
| ------------- | -------------------------------------- | ------------------ | --------------- |
| **OpenAPI**   | REST APIs, existing services           | `mcp_servers.yaml` | Build        |
| **MCP**       | Custom protocols, complex integrations | `mcp_servers.yaml` | Build        |
| **LangChain** | Python functions, rapid prototyping    | Direct import      | Runtime      |

## **Additional Resources**

- **Tool Registry**: [./src/cuga/backend/tools_env/registry/README.md](./src/cuga/backend/tools_env/registry/README.md)
- **Comprehensive example with different tools + MCP**: [./docs/examples/cuga_with_runtime_tools/README.md](Adding Tools)
- **CUGA as MCP**: [./docs/examples/cuga_as_mcp/README.md](docs/examples/cuga_as_mcp)
- **Knowledge Engine demo**: [./docs/examples/knowledge_demo/README.md](./docs/examples/knowledge_demo) — agent-level + session-level knowledge walkthrough

</details>

### Test Scenarios - E2E

All tests run through pytest (configured in `pyproject.toml`):

**Unit Tests**
- Registry: OpenAPI integration, MCP server functionality, service configurations
- Variables Manager: Core functionality, metadata handling, singleton pattern
- Code Executors: Local sandbox and E2B lite execution

**Policy Integration Tests** (`src/cuga/backend/cuga_graph/policy/tests/`)
- Intent Guard: Blocking behavior, priority resolution, multiple guard scenarios
- Playbook: Guidance injection, plan refinement, workflow execution
- Tool Approval: Human-in-the-loop approval flows (approve/deny)
- Tool Guide: Context enhancement and metadata injection
- Output Formatter: Response formatting and routing
- NL Trigger Conflict Resolution: Embedding-based similarity search with LLM conflict resolution
- Embedding Similarity: Vector search, policy matching, threshold validation
- Keyword Operators: AND/OR logic, case sensitivity, multi-keyword matching

**SDK Integration Tests** (`src/cuga/sdk_core/tests/`)
- SDK functionality: Agent invocation, streaming, tool integration
- Policy management: Policy loading, matching, and execution via SDK

**Stability Tests** (`@pytest.mark.stability` in `src/system_tests/e2e/`)
- Fast Mode: Get top account by revenue, list accounts, find VP sales high-value accounts
- CRM Workflows: Contacts management, email operations, tool discovery
- HF Utterances: Account queries, revenue calculations, playbook execution
- Execution: Sequential (`-n0`) so the 87% pass-rate gate aggregates on the controller; CI uses `--stability-threshold 87` (one LLM flake allowed on the 8-test suite)

## Running Tests

Lint:

```bash
uv run ruff check && uv run ruff format --check
```

Run the default suite (excludes manual and pgvector; pgvector needs a container):

```bash
uv run pytest
```

Run the CI-equivalent subset (mocked unit/load; live LLM jobs are split in `tests.yml`):

```bash
uv run pytest -m "not stability and not pgvector and not manual and not e2e and not load"
uv run pytest src/system_tests/load/load_test_with_mocked_llm.py -m load --load-test-users 5
```

Stability CI equivalent (scoped to e2e so collection stays small):

```bash
uv run pytest src/system_tests/e2e -m stability --stability-threshold 87 -n0
```

Run a faster local loop:

```bash
uv run pytest -m "not stability and not slow and not pgvector and not manual and not e2e and not load"
```

Run stability tests only (87% pass-rate gate; use `-n0` so threshold aggregation works):

```bash
uv run pytest src/system_tests/e2e -m stability --stability-threshold 87 -n0
```

Run pgvector tests (requires a running pgvector container):

```bash
uv run pytest -m pgvector -o addopts="-ra --strict-markers --import-mode=importlib"
```

## Evaluation

For information on how to evaluate, see the [CUGA Evaluation Documentation](src/cuga/evaluation/README.md)

## Resources

- [Example applications](./docs/examples)
- Contact: [CUGA Team](https://forms.office.com/pages/responsepage.aspx?id=V3D2_MlQ1EqY8__KZK3Z6UtMUa14uFNMi1EyUFiZFGRUQklOQThLRjlYMFM2R1dYTk5GVTFMRzNZVi4u&route=shorturl)


## Call for the Community

CUGA is open source because we believe **trustworthy enterprise agents must be built together**.  
Here's how you can help:

- **Share use cases** → Show us how you'd use CUGA in real workflows.
- **Request features** → Suggest capabilities that would make it more useful.
- **Report bugs** → Help improve stability by filing clear, reproducible reports.

All contributions are welcome through [GitHub Issues](../../issues/new/choose) - whether it's sharing use cases, requesting features, or reporting bugs!

## Roadmap

Amongst other, we're exploring the following directions:

- **Policy support**: procedural SOPs, domain knowledge, input/output guards, context- and tool-based constraints
- **Performance improvements**: dynamic reasoning strategies that adapt to task complexity

### Before Submitting a PR

Please follow the contribution guide in [CONTRIBUTING.md](CONTRIBUTING.md).

---

[![Star History Chart](https://api.star-history.com/svg?repos=cuga-project/cuga-agent&type=Timeline)](https://star-history.com/#cuga-project/cuga-agent&Date)

## Contributors

[![cuga agent contributors](https://contrib.rocks/image?repo=cuga-project/cuga-agent)](https://github.com/cuga-project/cuga-agent/graphs/contributors)
