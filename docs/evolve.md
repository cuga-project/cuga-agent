# Optional: Use Evolve with CugaLite

[← Back to README](../README.md)

Evolve can be used with **CugaLite** to bring task-specific guidance into the prompt before execution and save completed trajectories after the run.

This flow is:

- **Opt-in** - disabled by default
- **Non-blocking** - Evolve failures do not fail the task
- **CugaLite-focused** - enabled for lite mode by default
- **Optional integration** - install `cuga[evolve]` if you want the upstream Evolve package available locally, or let `uvx` fetch it on demand

## Setup Steps

### 1. Choose how Evolve will be started

Recommended for normal CUGA usage: let the CUGA MCP registry launch Evolve for you. In the manager UI, add an MCP tool with:

- Name: `evolve`
- Connection type: `Command (stdio)`
- Command: `uvx`
- Args: `--from altk-evolve --with setuptools<70 evolve-mcp`

Important: this command starts Evolve in `stdio` mode through the upstream Evolve package. It is intended to be launched by the CUGA registry, not run manually in a separate terminal.

Alternative for standalone/manual debugging: run Evolve yourself as an SSE server. If you run Evolve from a checked-out `altk-evolve` repo instead of `uvx`, install the Postgres extras first with `uv sync --extra pgvector`.

### 2. Add environment values in the MCP tool UI

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

### 3. [Optional] Enable lite mode plus Evolve

Edit `./src/cuga/settings.toml`:

```toml
[advanced_features]
lite_mode = true

[evolve]
enabled = true
url = "http://127.0.0.1:8201/sse"
mode = "auto"
app_name = "evolve"
lite_mode_only = true
save_on_success = true
save_on_failure = true
async_save = true
timeout = 30.0
```

If you use the recommended registry-managed setup above, keep `mode = "auto"` or set `mode = "registry"`.

If you run Evolve manually as a standalone SSE server, keep `url = "http://127.0.0.1:8201/sse"` and set `mode = "direct"` if you want to skip registry lookup entirely.

If you use Evolve tip generation, make sure the environment for the Evolve MCP server includes the required Evolve model settings. Otherwise `save_trajectory` may fail later with a LiteLLM/OpenAI model access error even when the MCP connection itself works.

### 4. Start the demo with sample workspace files

```bash
cuga start demo_crm --sample-memory-data
```

### 5. Run a task that routes through CugaLite, for example:

```text
Identify the common cities between my cuga_workspace/cities.txt and cuga_workspace/company.txt
```

## What happens during a run?

1. CUGA derives the task description from the current sub-task or first user message
2. CugaLite asks Evolve for relevant guidelines
3. Returned guidelines are appended to the system prompt under an `Evolve Guidelines` section
4. The task executes normally
5. The user / assistant trajectory is saved back to Evolve after completion

## Notes

- `async_save = true` saves trajectories in the background and avoids blocking the response
- `save_on_success` and `save_on_failure` let you control which runs are recorded
- `mode = "auto"` lets CUGA use a registry-managed Evolve MCP server when available and fall back to the direct SSE URL otherwise
- `mode = "registry"` is best when you want Evolve to be fully managed as a normal CUGA MCP tool
- `mode = "direct"` is best when you are manually running an SSE Evolve server outside CUGA
- If Evolve is unavailable, times out, or returns no guidance, CUGA continues normally
