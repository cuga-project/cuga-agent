# Healthcare insurance demo (OAK / `demo_health`)

This demo wires the CUGA manage experience to **member-focused insurance APIs** served by **`cuga-oak-health`** (OpenAPI on port **8090** by default) and built-in **OAK playbooks** shipped in this repo (`oak_policies.json`). The workspace **filesystem MCP** is optional (`cuga start demo_health --filesystem`).

## Prerequisites

- CUGA from this repo with optional **health** extra (pulls `cuga-oak-health` into the project env; **Python 3.12+** required for that package):

  ```bash
  uv sync --extra health
  ```

- Or rely on **`uvx`** at runtime on **Python 3.12+** (no `health` extra needed): the CLI runs `uvx cuga-oak-health` (the current PyPI release starts uvicorn on **8090** with no CLI flags—keep `[server_ports] oak_health_api = 8090` so the agent’s OpenAPI URL matches).

## Run locally

```bash
# Default port 8090 — override with DYNACONF_SERVER_PORTS__OAK_HEALTH_API if needed
cuga start demo_health
```

Services started:

- **Oak Health OpenAPI** — `http://localhost:8090/openapi.json` (config: `[server_ports] oak_health_api` in `settings.toml`)
- **Registry** + **Demo / Manage UI** — same ports as other demos
- **Filesystem MCP** — only if you run `cuga start demo_health --filesystem` (workspace under `./cuga_workspace`)

Stop:

```bash
cuga stop demo_health
cuga status demo_health
```

## Manage setup

`cuga start demo_health` resets the manage config store and publishes:

- Tool **`oak_health`** (OpenAPI) pointing at the local health API
- **Policies** from `src/cuga/backend/server/demo_setup_utils/oak_policies.json` (claims, providers, benefits, referrals, etc.)
- **Homescreen** greeting and starters tailored to insurance scenarios
- **Agent** display name: *Member & Benefits Assistant*

## Optional: add insurance APIs to manager mode

```bash
cuga start manager --oak-health
```

## Docker / OpenShift

Use `CUGA_DEMO_MODE=health` with the UBI entrypoint to run `demo_health` (no filesystem MCP by default).

## Related

- Main app optional dependency: `health = ["cuga-oak-health"]` in `pyproject.toml`
- CLI: `src/cuga/cli/main.py` (`demo_health` service)
- Demo config helper: `src/cuga/backend/server/demo_manage_setup.py`
