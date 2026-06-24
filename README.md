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
> | **Reasoning modes** (fast / balanced / accurate) | `[features] cuga_mode` in [`settings.toml`](src/cuga/settings.toml) · [`configurations/modes/`](src/cuga/configurations/modes/) |
> | **Hybrid API + browser tasks** | `[advanced_features] mode = 'hybrid'` · Playwright + [browser extension](src/frontend_workspaces/extension/readme.md) |
> | **Multi-agent (CugaSupervisor)** | `cuga start demo_supervisor` · `[supervisor]` in [`settings.toml`](src/cuga/settings.toml) · [details](docs/multi-agent.md) |
> | **A2A & remote agents** | External agent entries in supervisor config · [CugaSupervisor](https://docs.cuga.dev/docs/sdk/cuga_supervisor) |
> | **Policies & HITL** | [Policies SDK](https://docs.cuga.dev/docs/sdk/policies/) — Intent Guard, Playbook, Tool Approval, Tool Guide, Output Formatter |
> | **Manage & publish** | `cuga start manager` · draft tools, MCP, LLM, and policies in the web UI, then **publish** a versioned config for production chat ([details](#manage-publish-and-self-hosting)) |
> | **Reflection** | `[advanced_features] reflection_enabled` in [`settings.toml`](src/cuga/settings.toml) |
> | **Langflow** | Low-code visual workflows — integrates with CUGA ([langflow.org](https://www.langflow.org/)) |
> | **Memory** (optional) | `enable_memory` in `settings.toml` · `uv sync --extra memory` · `cuga start memory` |
> | **Knowledge** (RAG) | `enable_knowledge=True` (default) · ingest PDFs/Office/HTML/Markdown via **Docling** · **agent-level** + **session-level** scopes · `cuga start demo_knowledge` · [details](docs/knowledge-base.md) |
> | **Agent skills** | `SKILL.md` under `.agents/skills` · **`cuga start demo_skills`** (`sandbox_mode = "native"` by default, or **`opensandbox`**) · or **`demo --sandbox`** with `[skills]` on · [Agent skills](#agent-skills) |
> | **Self-host on a cluster** | Helm chart and deploy scripts in [`deployment/`](deployment/) · [Kubernetes guide](deployment/README.md) (local kind/minikube, or registry push for cloud clusters) |
> | **Save & reuse** _(experimental)_ | `cuga_mode = "save_reuse_fast"` in `settings.toml` · [details](docs/save-and-reuse.md) |
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

- **Save-and-reuse capabilities** _(Experimental)_ — Capture and reuse successful execution paths (plans, code, and trajectories) for faster and consistent behavior across repeated tasks

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

### Human in the Loop Task Execution

Watch CUGA pause for human approval during critical decision points:

**Example Task:** `get best accounts`

https://github.com/user-attachments/assets/d103c299-3280-495a-ba66-373e72554e78

<details>
<summary><b>Would you like to try this? (HITL Demo)</b></summary>

Experience CUGA's Human-in-the-Loop capabilities where the agent pauses for human approval at key decision points:

### Setup Steps:

1. **Enable HITL mode:**

   ```bash
   # Edit ./src/cuga/settings.toml and ensure:
   api_planner_hitl = true  # under [advanced_features] section
   ```

2. **Start the demo:**

   ```bash
   cuga start demo
   ```

3. **Try the HITL task:**
   ```
   get best accounts
   ```

**What you'll see:** CUGA will pause at critical decision points, showing you the planned actions and waiting for your approval before proceeding.

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

CUGA supports multiple LLM providers (OpenAI, IBM WatsonX, Azure OpenAI, LiteLLM, Groq, OpenRouter), configurable through TOML files or environment variables.

See **[docs/llm-providers.md](docs/llm-providers.md)** for setup instructions per provider, configuration priority, and the full list of config files. Also see [`.env.example`](.env.example) for examples.

</details>

<div style="margin: 20px 0; padding: 15px; border-left: 4px solid #2196F3; border-radius: 4px;">

**Tip:** Want to use your own tools or add your MCP tools? Check out [`src/cuga/backend/tools_env/registry/config/mcp_servers.yaml`](src/cuga/backend/tools_env/registry/config/mcp_servers.yaml) for examples of how to configure custom tools and APIs, including those for digital sales.

</div>

## Agent skills

Agent skills are reusable instruction packs: each skill is a `SKILL.md` file with YAML frontmatter and markdown body. CUGA discovers them at startup, lists short descriptions in the agent prompt, and exposes a **`load_skill`** tool so the model pulls the full body only when a task matches that skill—similar to opening a playbook instead of stuffing every procedure into the system prompt.

**Where skills live**

| Location | Role |
| -------- | ---- |
| `.agents/skills/**/SKILL.md` | Preferred project-local skills path; this is what `npx skills ... -a universal` writes |

Use **`~/.config/agents/skills/`** for global installs from `npx skills` with **`-g`**; **`~/.config/cuga/skills/`** is a legacy global path that is still scanned. Legacy **`<CUGA folder>/skills/`** and **`<CUGA folder>/.skills/`** (often `.cuga` via `CUGA_FOLDER`) are still scanned. If the same skill `name` appears in multiple places, project-local skills win over global skills, and `.agents/skills/` wins over legacy project paths.

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

This creates `.agents/skills/pptx/SKILL.md` for the current project. Restart `cuga start demo_skills` (or your app) so skills are rescanned. Add `-g` if you want the skill installed globally under `~/.config/agents/skills/` instead.

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

### Knowledge Base

CUGA includes a built-in knowledge base powered by LangChain and local vector stores, with **Docling** for document ingestion (PDFs, Office files, HTML, Markdown, images). Documents can be scoped **agent-level** (permanent) or **session-level** (per-thread). Knowledge is **enabled by default** via `settings.toml`.

**Try the knowledge demo:**

```bash
cuga start demo_knowledge
```

See **[docs/knowledge-base.md](docs/knowledge-base.md)** for programmatic access, session-scoped knowledge, and supported document types. Also see the HR-Benefits walkthrough at **[docs/examples/knowledge_demo/](./docs/examples/knowledge_demo)**.

---

## CugaSupervisor (Multi-Agent)

Orchestrate multiple agents with a single supervisor: delegate tasks to specialized sub-agents, mix local agents with remote A2A agents, and pass data between them.

**Documentation**: [CugaSupervisor](https://docs.cuga.dev/docs/sdk/cuga_supervisor)

**Try the supervisor demo:** run the multi-agent demo (CRM + email sub-agents) with:

```bash
cuga start demo_supervisor
```

See **[docs/multi-agent.md](docs/multi-agent.md)** for a full quick-start example, A2A remote agents, variable passing, and loading agents from YAML.

---

## Configurations

<details>
<summary> Code execution sandboxing (Docker/Podman or E2B cloud)</summary>

CUGA executes generated code locally by default. For stronger isolation, run it in a Docker/Podman container (`cuga start demo --sandbox`) or in an [E2B](https://e2b.dev) cloud sandbox.

See **[docs/sandboxing.md](docs/sandboxing.md)** for setup, ngrok registry exposure for E2B, sandbox modes, and troubleshooting.

</details>

<details>
<summary> Reasoning modes & Task modes</summary>

Switch reasoning between **Fast / Balanced / Accurate** (`[features] cuga_mode` in `settings.toml`), and task execution between **API / Web / Hybrid** (`[advanced_features] mode` in `settings.toml`).

See **[docs/reasoning-and-task-modes.md](docs/reasoning-and-task-modes.md)** for the mode tables, config file locations, and `settings.toml` examples. Reasoning-mode flags are also documented at [./docs/flags.html](./docs/flags.html).

</details>

<details>
<summary>📝 Special Instructions Configuration</summary>

Each `.md` file under `configurations/instructions/` contains specialized instructions automatically integrated into CUGA's internal prompts for a given component (`answer`, `api_planner`, `code_agent`, `plan_controller`, `reflection`, `shortlister`, `task_decomposition`). Edit the markdown files to customize behavior, then select the set via `instruction_set` in `configurations/instructions/instructions.toml`.

See **[docs/special-instructions.md](docs/special-instructions.md)** for the directory layout and config example.

</details>

<details>
<summary><em style="color: #666;"> 🧠 Optional: Use Evolve with CugaLite</em></summary>

Evolve can be used with **CugaLite** to bring task-specific guidance into the prompt before execution and save completed trajectories after the run. It's opt-in, non-blocking, and CugaLite-focused.

See **[docs/evolve.md](docs/evolve.md)** for setup steps (MCP registry config, environment variables, `settings.toml`), what happens during a run, and mode notes.

</details>

## Advanced Usage

<details>
<summary><b> Save & Reuse</b></summary>

Capture and reuse successful execution paths (plans, code, and trajectories) for faster, consistent behavior across repeated tasks. Change `cuga_mode = "save_reuse_fast"` in `settings.toml`, then run `cuga start demo`.

See **[docs/save-and-reuse.md](docs/save-and-reuse.md)** for the full demo walkthrough.

</details>

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
- **Comprehensive example with different tools + MCP**: [Adding Tools](./docs/examples/cuga_with_runtime_tools/README.md)
- **CUGA as MCP**: [./docs/examples/cuga_as_mcp/README.md](docs/examples/cuga_as_mcp)
- **Knowledge Engine demo**: [./docs/examples/knowledge_demo/README.md](./docs/examples/knowledge_demo) — agent-level + session-level knowledge walkthrough

</details>

### Test Scenarios - E2E

All tests are available through `./src/scripts/run_tests.sh` (`./src/scripts/run_tests.sh unit_tests` for unit tests only) — unit, policy integration, SDK integration, and stability tests.

See **[docs/testing.md](docs/testing.md)** for the full breakdown by test suite.

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
