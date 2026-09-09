# MCPCF PoC deployment

Deploys Context Forge into its own namespace in the same tenant as a running
CUGA agent, wires Option A auth, and runs a cloned CUGA
agent with a settings-driven Forge tool browser.

**To deploy it, follow [`QUICKSTART.md`](QUICKSTART.md).** This file records
why each non-obvious choice is what it is.

## Status

Tracks A–D are done and were verified live on an OpenShift dev topology in
namespace `mcpcf-poc`; Track E's clone script is written. Several real bugs
were found and fixed by running each stage live rather than trusting the
authored manifests — the constraints below are the result, and each one
matters. **Read them before changing anything here.**

| Track | What | State |
|---|---|---|
| A | Forge + its own Postgres/Redis + demo MCP server | `/health` returns `200` |
| B | Option A auth (broker token → team-scoped tools) | all six checks pass on `/v1/tools` + `/rpc` |
| C | Settings-driven tool browser in the manage UI | backend verified live against Forge |
| D | Agent image carrying the new UI | built **in-cluster**, not published to GHCR |
| E | Cloned CUGA agent on that image | scripted; see `10-clone-agent.sh` |

**Track B runs a patched Forge.** Stock Forge rejects external-IdP bearer
tokens on every REST endpoint — upstream bug
[IBM/mcp-context-forge#6396](https://github.com/IBM/mcp-context-forge/issues/6396).
`FORGE_IMAGE` in `forge.env` points at a locally-patched build. Revert it to
`ghcr.io/ibm/mcp-context-forge:latest` once that lands upstream.

## Prerequisites

- `oc` logged in to the target cluster's **spoke**. On a dev topology that is
  a direct `oc login --server=https://api.<cluster-domain>:6443`; other
  topologies may require a tunnel.
- `envsubst` (part of `gettext`).
- `cp forge.env.example forge.env` and fill it in. **Never commit `forge.env`**
  — it holds real secrets. Generate `JWT_SECRET_KEY` / `AUTH_ENCRYPTION_SECRET`
  with `openssl rand -hex 32` each.

## Runbook

```bash
cp forge.env.example forge.env    # fill in secrets, NAMESPACE, CLUSTER_DOMAIN, TENANT_ID
./01-deploy-forge.sh forge.env    # namespace, postgres, redis, forge, route, router CA
./02-deploy-demo-mcp.sh forge.env # tiny demo MCP server (reuses the CUGA image)
./03a-deploy-mock-broker.sh forge.env  # PoC OIDC broker (skipped when BROKER_MODE=real)
./03-configure-forge.sh forge.env # team, gateway, SSO provider + team_mapping
./04-mint-token.sh forge.env      # agent token  (--no-group for the negative test)
./05-validate.sh forge.env        # the plan's six assertions
# --- Track D: build the agent image carrying the new UI ---
(cd ../../src/frontend_workspaces/frontend && sh build.sh)   # MUST run: the
    # Dockerfile and the GHCR workflow do NOT build the UI; they ship whatever
    # is committed at src/cuga/frontend/dist.
# then build that tree into an image (in-cluster BuildConfig, or GHCR) and put
# the resulting ref in CLONE_IMAGE — see QUICKSTART.md step 4.
./10-clone-agent.sh forge.env     # clone a running agent onto that image
./99-cleanup.sh forge.env         # tears the Forge namespace back down
```

### Track E notes

`10-clone-agent.sh` renders the clone *from the live source Deployment*
(`_clone_agent.py`) rather than a hand-written manifest, so it inherits Vault,
Postgres, model and CA config automatically. It overrides only:

- **identity** — a fresh `DYNACONF_SERVICE__INSTANCE_ID` plus `AGENT_ID`, so
  the clone's config rows don't collide with the source's. Rows are scoped by
  `(tenant_id, instance_id, agent_id)`; the tenant and its Postgres are shared
  and **untouched** — no new database.
- **auth off, plain HTTP behind an edge route** — the clone gets a new
  hostname that the Verify app has no registered redirect URI for, and the
  source's TLS cert is issued for the source's host. The PoC exercises Forge
  auth, not CUGA login, so the sanctioned option (`auth.enabled=false`) is
  taken. Do not carry this to anything real.
- **no `ownerReferences`** — that is the whole point: the operator must not
  manage or revert it. The script asserts afterwards that no `CugaAgent` CR
  with the clone's name exists.

The Forge token it injects is broker-minted and, in mock mode, expires in an
hour. Re-run `04-mint-token.sh` then `10-clone-agent.sh` to refresh it.

## Notes specific to this topology (OpenShift, hub + spoke)

- **SCC.** The tenant's namespaces run `restricted-v2` with a per-namespace
  auto-assigned UID range (confirmed live, not from the upstream docs).
  None of these manifests set `runAsUser` or `fsGroup`;
  OpenShift assigns both from the new namespace's range at admission. This
  is why Postgres uses `quay.io/sclorg/postgresql-16-c9s` (built for
  arbitrary-UID operation) instead of the vanilla `postgres` image, and Redis
  uses `docker.io/bitnami/redis` instead of the vanilla `redis` image — both
  vanilla images assume a fixed UID and fight the SCC.
- **Forge's Postgres is separate from the tenant's.** The CUGA agent clone
  (Track E) reuses the tenant's existing Postgres as-is — no new DB for the
  agent. Only Forge gets a fresh one, in its own namespace.
- **Demo MCP server reuses the CUGA image** already pulled into this cluster
  (`fastmcp` is already a dependency), rather than building a new one. No
  `imagePullSecret` is set by default — the tenant's own agent pods pull that
  same image with none, which only makes sense if the cluster nodes have the
  registry credentials configured cluster-wide rather than per-pod. Not
  proven for a brand-new namespace until `02-deploy-demo-mcp.sh` actually
  runs; if it `ImagePullBackOff`s, set `PULL_SECRET` in `forge.env` to an
  existing dockercfg secret name and re-run.
- **Route hostnames.** The platform cluster (hub) can carry decoy `Route`
  objects that 503 — the real route always lives on the spoke (agent
  cluster), same as the tenant's own agents. `50-forge-route/route.yaml`
  deploys there, alongside Forge itself.
