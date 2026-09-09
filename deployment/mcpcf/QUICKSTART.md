# Context Forge PoC — quickstart

Deploys [MCP Context Forge](https://github.com/IBM/mcp-context-forge) into its
own namespace beside a running CUGA agent, wires token-based auth so Forge
serves only the tools a workspace is entitled to, and gives the CUGA manage UI
a browser for that catalog.

This file is the runbook. [`README.md`](README.md) covers the design decisions
and the cluster-specific constraints behind each choice.

## What you get

| Piece | Where |
|---|---|
| Context Forge, with its own Postgres and Redis | new namespace `$NAMESPACE` |
| A demo MCP server to publish through it | same namespace |
| A mock OIDC broker (PoC only) | same namespace, skipped when `BROKER_MODE=real` |
| "Browse workspace catalog" in the CUGA manage UI | `[context_forge]` in `settings.toml` |

The tenant's own Postgres and its running agents are **not** modified. Forge
gets fresh instances in a separate namespace.

## Prerequisites

- `oc`, logged in to the **spoke** (agent) cluster. Routes and workloads live
  on the spoke; the hub can carry decoy `Route` objects that return 503.
- `envsubst` (ships with `gettext`).
- `python3` with `httpx`.
- A tenant on that cluster with at least one running CUGA agent.

## 1. Configure

```bash
cd deployment/mcpcf
cp forge.env.example forge.env
```

Fill in `forge.env`. It is gitignored — **never commit it**.

| Variable | How to get it |
|---|---|
| `TENANT_ID` | The UUID in the `tenant-<uuid>` namespace on your cluster |
| `CLUSTER_DOMAIN` | Apps domain of the spoke cluster |
| `HUB_DOMAIN` | Apps domain of the hub cluster (account-iam, IVIA). Same as `CLUSTER_DOMAIN` on single-cluster setups |
| `CUGA_IMAGE` | The image the tenant's existing agents already run |
| `JWT_SECRET_KEY`, `AUTH_ENCRYPTION_SECRET` | `openssl rand -hex 32` each |
| `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `PLATFORM_ADMIN_PASSWORD`, `BASIC_AUTH_PASSWORD` | Choose strong values |

Three values in that file are not free choices, and each one cost a live
debugging session:

- **`POSTGRES_USER` must not be `postgres`.** The sclorg image treats that name
  as "just set the superuser password" and never creates `POSTGRES_DB`.
- **`PLATFORM_ADMIN_EMAIL` must use a public-suffix domain.** Forge's `EmailStr`
  validator rejects `.local` with a generic 422 that does not mention the TLD.
- **`JWT_SECRET_KEY` / `AUTH_ENCRYPTION_SECRET` must be high entropy.** Forge
  refuses to start on the placeholder or a weak string.

## 2. Deploy

Run in order. Each script takes the env file as its argument.

```bash
./01-deploy-forge.sh forge.env        # namespace, postgres, redis, forge, route, router CA
./02-deploy-demo-mcp.sh forge.env     # demo MCP server (reuses CUGA_IMAGE)
./03a-deploy-mock-broker.sh forge.env # PoC OIDC broker; skip when BROKER_MODE=real
./03-configure-forge.sh forge.env     # team, gateway, SSO provider, team_mapping
./04-mint-token.sh forge.env          # workspace token
./05-validate.sh forge.env            # six assertions on /v1/tools and /rpc
```

`05-validate.sh` is the gate. Do not continue until it passes.

Optional checks:

```bash
./06-test-per-user-identity.sh forge.env   # per-user attribution
./07-configure-user-identity.sh forge.env  # register the platform IdP
./08-configure-ui-sso.sh forge.env         # IBM Verify login on the Forge console
```

## 3. Point CUGA at Forge

Set `[context_forge]` in `settings.toml`:

```toml
[context_forge]
enabled = true
url = "https://mcpgateway-<namespace>.apps.<cluster-domain>"   # no trailing slash
audience = "mcp-gw.<tenant-id>"                                # must match the Forge SSO provider's api_audience
workspace_group = "ws-<workspace-id>"
token_source = "env"                                           # or "user"
token_env_var = "CONTEXT_FORGE_TOKEN"
```

Everything is inert while `enabled = false`, and the manage UI hides the
entry point. Existing deployments are unaffected.

`token_source`:

- **`env`** — one shared workspace credential read from `token_env_var`.
- **`user`** — forwards the caller's own login token, so Forge attributes tool
  use per user. Requires `auth.enabled` and the issuer registered by
  `07-configure-user-identity.sh`.

Restart the agent, open the manage UI, and use **Browse workspace catalog**
beside **Add tool**.

## 4. Optional — run a cloned agent on a new image

Only needed to demo the UI on an image that does not yet carry it.

```bash
# The Dockerfile and the GHCR workflow do NOT build the UI. They ship whatever
# is committed at src/cuga/frontend/dist, so build it first or you get the old UI.
(cd ../../src/frontend_workspaces && pnpm install --frozen-lockfile)
(cd ../../src/frontend_workspaces/frontend && sh build.sh)
```

Build that tree into an image, put the ref in `CLONE_IMAGE`, then:

```bash
./10-clone-agent.sh forge.env
```

`10-clone-agent.sh` renders the clone from the live source Deployment, so it
inherits Vault, Postgres, model and CA config automatically. It overrides only:

- **Identity** — a fresh `DYNACONF_SERVICE__INSTANCE_ID` and `AGENT_ID`, so the
  clone's config rows do not collide with the source's. Rows are scoped by
  `(tenant_id, instance_id, agent_id)`; the shared Postgres is untouched.
- **Auth off, plain HTTP behind an edge route** — the clone gets a hostname the
  IdP has no redirect URI for, and the source's certificate is issued for the
  source's host. The PoC exercises Forge auth, not CUGA login. **Do not carry
  this setting to anything real.**
- **No `ownerReferences`** — the operator must not manage or revert the clone.
  The script asserts afterwards that no `CugaAgent` CR with that name exists.

In mock mode the injected Forge token expires after an hour. Re-run
`04-mint-token.sh` then `10-clone-agent.sh` to refresh it.

## 5. Tear down

```bash
./99-cleanup.sh forge.env
```

Removes the Forge namespace. The tenant and its agents are untouched.

## Known constraints

- **Forge is patched.** Stock Forge rejects external-IdP bearer tokens on every
  REST endpoint — [IBM/mcp-context-forge#6396](https://github.com/IBM/mcp-context-forge/issues/6396).
  `FORGE_IMAGE` must point at a build carrying the fix. Revert it to
  `ghcr.io/ibm/mcp-context-forge:latest` once that lands upstream.
- **Image choices are SCC-driven.** On OpenShift `restricted-v2`, namespaces get
  an auto-assigned UID range. None of these manifests set `runAsUser` or
  `fsGroup`. Postgres uses `quay.io/sclorg/postgresql-16-c9s` and Redis uses
  `docker.io/bitnami/redis` because both support arbitrary UIDs; the vanilla
  images assume a fixed UID and fail admission.
- **Image pulls.** No `imagePullSecret` is set by default, which assumes the
  nodes hold registry credentials cluster-wide. If `02-deploy-demo-mcp.sh` hits
  `ImagePullBackOff`, set `PULL_SECRET` in `forge.env` to an existing dockercfg
  secret name and re-run.
- **`build.sh` exits 0 even when the build fails**, after it has already removed
  the target `dist`. Confirm `src/cuga/frontend/dist/index.html` references
  bundle filenames that actually exist before building an image.

## Files never to commit

`.gitignore` already covers these. Confirm with
`git status --untracked-files=all -- deployment/mcpcf` before pushing.

| File | Holds |
|---|---|
| `forge.env` | JWT and encryption secrets, all passwords |
| `.mcpcf-token` | A minted bearer token |
| `.ivia-client.json` | OIDC `client_id` and `client_secret` |
| `.mcpcf-state.json` | Cluster-specific resource IDs |
| `.rollback/` | Captured live Deployment state |
