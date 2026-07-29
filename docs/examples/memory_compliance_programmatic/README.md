# Local memory compliance programmer PoC

This example exercises the same Evolve data through three local programmer
surfaces. It assumes CUGA and Evolve use the same local Evolve configuration
and namespace.

## 1. Create an isolated local workspace

```bash
export MEMORY_POC_DIR="$(mktemp -d)"
export EVOLVE_BACKEND=filesystem
export EVOLVE_NAMESPACE_ID=evolve
export EVOLVE_DATA_DIR="$MEMORY_POC_DIR/evolve"
export EVOLVE_HOOKS_CONFIG=examples/cuga_compliance_poc_hooks.yaml
export CUGA_MEMORY_POC_DB="$MEMORY_POC_DIR/cuga.db"
```

This keeps the programmer walkthrough separate from any existing CUGA or
Evolve data.

## 2. Start Evolve MCP

From an `altk_evolve` checkout:

```bash
uv run evolve-mcp --transport sse --host 127.0.0.1 --port 8201
```

Use the same `EVOLVE_BACKEND` and backend-specific environment variables for
every command below. For PostgreSQL, all processes must point to the same
database.

To use the already-seeded UI PoC instead of an isolated store:

```bash
export EVOLVE_BACKEND=filesystem
export EVOLVE_DATA_DIR=/tmp/cuga-compliance-evolve-data
export EVOLVE_HOOKS_CONFIG=examples/cuga_compliance_poc_hooks.yaml
```

Set those variables in the Evolve checkout before starting `evolve-mcp`,
running `evolve_python.py`, or invoking the `evolve` CLI. A different
`EVOLVE_DATA_DIR` means a different memory store.

## 3. Load the same demonstration data

From the CUGA checkout, while Evolve MCP is running:

```bash
uv run python docs/examples/memory_compliance_programmatic/load_demo_data.py \
  --url http://127.0.0.1:8201/sse \
  --cuga-db "$CUGA_MEMORY_POC_DB"
```

The loader uses the same CUGA bootstrap as the web PoC. It creates:

- ten realistic conversation histories in the CUGA database, including one
  two-turn flow where CUGA saves a preference through Evolve and retrieves it
  in the follow-up response;
- 43 related memories in Evolve;
- eleven SIL-generated assistant answers with complete CUGA detail trajectories,
  clickable per-response memory disclosures, and matching append-only usage records;
- the default automation configuration;
- one dry simulated scheduled-retention run; and
- matching activity and simulated delivery records.

The seed is idempotent. Running the command again reports the existing
conversations and memories without duplicating them or adding another
simulated run. Use `--simulate-again` only when another ledger run is wanted.

## 4. Exercise memory through CUGA

This uses CUGA's existing `EvolveIntegration`, the same MCP integration used by
CugaLite and the compliance UI:

```bash
uv run python docs/examples/memory_compliance_programmatic/cuga_programmatic.py \
  --namespace evolve \
  --agent-id cuga-default \
  --user-id USER_ID
```

The default run:

- reads protection health;
- lists the selected user's memories;
- validates the PoC retention policy; and
- performs a dry run scoped to `cuga-default`.

Destructive or privileged examples are explicit:

```bash
# Apply a legal hold
uv run python docs/examples/memory_compliance_programmatic/cuga_programmatic.py \
  --user-id USER_ID --legal-hold ENTITY_ID

# Exercise the same protected delete used by "Forget"
uv run python docs/examples/memory_compliance_programmatic/cuga_programmatic.py \
  --user-id USER_ID --forget ENTITY_ID
```

`EvolveIntegration` is currently an internal CUGA API. This example proves the
programmatic capability but does not claim that CUGA already has a stable
public `agent.memory` SDK.

## 5. Use Evolve's Python client directly

Run this from the Evolve checkout so its package and backend extras are
available:

```bash
uv run python \
  ../cuga-agent.pii-retention-poc/docs/examples/memory_compliance_programmatic/evolve_python.py \
  --namespace evolve \
  --agent-id cuga-default \
  --user-id USER_ID
```

This reads the backend directly, so it is an administrator/developer surface.
It does not apply CUGA's user-role projections.

The `--legal-hold` and `--forget` options demonstrate direct metadata and
deletion operations. The legal-hold hook can reject the subsequent deletion.
Keep `EVOLVE_HOOKS_CONFIG` set for both operations; otherwise a second local
client can start without the protection plugins configured.

## 6. Use the existing Evolve CLI

```bash
uv run evolve entities list evolve --type fact --limit 20
uv run evolve entities show evolve ENTITY_ID
uv run evolve entities search evolve "sales preferences" --limit 10
uv run evolve retention run evolve \
  --policy ../cuga-agent.pii-retention-poc/docs/examples/memory_compliance_programmatic/retention.yaml
```

Retention is a dry run unless `--apply` is supplied:

```bash
uv run evolve retention run evolve \
  --policy ../cuga-agent.pii-retention-poc/docs/examples/memory_compliance_programmatic/retention.yaml \
  --apply
```

The Evolve Python client and CLI are backend-level tools. CUGA's web
application remains the appropriate surface for role-scoped user and
administrator experiences.

Ordinary Evolve entity reads run the configured read hooks and may update
`metadata.last_accessed`. Administrative inventory and retention code should
use `scan_entities`, as `evolve_python.py` does, when a read must not count as
memory use.
