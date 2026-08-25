# Option A — group claim → team mapping in MCP Context Forge

How an agent reaches its workspace's tools without anyone writing membership per identity.
Verified against **mcp-context-forge 1.0.7** source.

**The idea:** the token the agent presents carries the workspace and the role as claims. Context Forge maps those
claims onto a team membership and an RBAC role while it validates the token. Nothing is written per call, and
nothing is written per agent.

---

## Where each identifier lands

The GoRI hierarchy is **Tenant → Workspace → Agent → Service Context**. Workspace is the *management* boundary;
agent is the *product* entity. They map differently on purpose:

| GoRI | Context Forge | Why |
|---|---|---|
| `tenant_id` | one **instance** per tenant | Isolation is infrastructure, not RBAC |
| `workspace_id` | one **team** | The management boundary: member list, budget envelope, uniform policy |
| `agent_id` | not mapped | The product entity. Agent-level decisions need context CF does not have |
| service context | not mapped | Per-execution; nothing durable to model |

### Why tool registration belongs at workspace scope

A workspace answers *who administers this space, and what resources are available in it*. It owns the member list,
the budget envelope, and policy that applies uniformly to every agent inside it. Tools registered at workspace scope
are exactly what "shared managed resources" means, and mapping `team_id → workspace_id` gives two properties
directly:

- **A tool is registered once** and is available to every agent in the workspace — one gateway registration, one copy
  of the upstream credential, one discovery pass against the upstream system.
- **Workspace policy governs which tools appear in that team at all**, so the catalog stays a management decision
  rather than a per-agent negotiation.

### Why not one team per agent

It would make CF enforce the per-agent tool list, which is genuinely appealing. It fails on a structural point:
**a tool invocation decision cannot be made from agent identity alone.** The full evaluation context is
`(tenant_id, workspace_id, agent_id, user_id)`, and CF only ever sees part of it. Encoding agent precision in teams
therefore buys a partial answer while paying real costs — a gateway registration per agent, a credential copy per
agent, discovery load multiplied by agents, and human curators accumulating one group per agent they administer.

**The split we want:** CF is the resource gateway — *what exists for this workspace*. The AI Agent Control Plane is
the policy decision point — *may this agent, acting for this user, use this tool right now*. That layer comes later;
until then the per-agent tool list stays a CUGA configuration boundary, which is a documented gap rather than an
oversight.

> **PoC decision (Chunlong):** start with `team_id → workspace_id`. Do not make CF complicated, and do not make CF
> handle partial authorization. The authorization layer arrives with the control plane.

---

## 1. One-time — register the token broker as a trusted provider

Forge must trust **whoever issues the agent's token**. We mint that token ourselves through the exchange, so the
issuer is the broker, not IBM Verify directly. That is also why the claims below are available at all: we control
what goes into the exchanged token.

```bash
curl -X PUT https://forge.tenant-a.internal/auth/sso/admin/providers/sovereign-broker \
  -H "Authorization: Bearer $FORGE_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "issuer":   "https://broker.tenant-a.internal/oidc",
    "jwks_uri": "https://broker.tenant-a.internal/oidc/jwks",
    "trusted_for_api_auth": true,
    "api_audience": "mcp-gw.tenant-a",
    "auto_create_users": true,
    "provider_metadata": {
      "groups_claim": "groups",
      "role_mappings": {
        "ServiceOwner": "team_admin",
        "ServiceAdmin": "developer",
        "ServiceUser":  "viewer"
      }
    },
    "team_mapping": {}
  }'
```

| Field | Why |
|---|---|
| `trusted_for_api_auth` | Lets tokens from this issuer authenticate API and MCP calls, not just browser logins (shipped in [#3567](https://github.com/IBM/mcp-context-forge/issues/3567)) |
| `api_audience` | **Per instance.** Tenant B's Forge uses `mcp-gw.tenant-b`, so a token minted for one tenant is refused by the other. Forge requires this whenever `trusted_for_api_auth` is on |
| `groups_claim` | Which claim Forge normalises into its group list. Defaults to `groups` |
| `role_mappings` | Group value → Forge RBAC role |
| `team_mapping` | Group value → team membership (filled in per workspace, below) |

`ServiceUser → viewer` is deliberate: the team-scoped `viewer` role carries `tools.read` **and** `tools.execute`,
which is see-and-run without catalog write.

Also set globally on that Forge instance:

```
MCP_CLIENT_AUTH_ENABLED=true          # unchanged default — JWT auth stays on
MCP_REQUIRE_AUTH=true                 # no token → reject, not "public-only"
SSO_API_TOKEN_AUTH_ENABLED=true       # accept external IdP tokens on API/MCP endpoints
EXTERNAL_IDENTITY_CACHE_TTL=0|60      # 60s default; 0 for immediate remapping
```

Nothing named `TRUST_PROXY_AUTH*` is involved.

---

## 2. Per workspace — create the team, then add one mapping entry

Runs when a workspace is created. Not per agent, and not per user.

```bash
# a. create the workspace team — the caller becomes owner, which is what lets it manage members later
TEAM_ID=$(curl -sX POST https://forge.tenant-a.internal/teams \
  -H "Authorization: Bearer $FORGE_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "ws-42-a3f9c1", "description": "workspace 42"}' | jq -r .id)

# b. merge the new entry into team_mapping and PUT the whole object back
curl -X PUT https://forge.tenant-a.internal/auth/sso/admin/providers/sovereign-broker \
  -H "Authorization: Bearer $FORGE_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"team_mapping\": {
        \"ws-41\": {\"team_id\": \"b2c1...\",   \"role\": \"member\"},
        \"ws-42\": {\"team_id\": \"$TEAM_ID\", \"role\": \"member\"}
      }}"
```

Two traps, both verified in source:

- **`team_mapping` is a whole-object replace.** Read the current value and merge, or concurrent workspace creation
  will clobber entries.
- **Use a unique slug, never a reusable name.** A soft-deleted team is revived by slug and inherits the previous
  occupant's RBAC, tokens and resources ([#5700](https://github.com/IBM/mcp-context-forge/issues/5700)).

`team_mapping` accepts either form:

```jsonc
{ "ws-42": "8f31c0d2-..." }                                  // role defaults to member
{ "ws-42": { "team_id": "8f31c0d2-...", "role": "member" } } // role is owner | member
```

Each workspace needs a matching group — `ws-42` — administered wherever groups live, and the broker must include it
in the exchanged token.

---

## 3. What the tokens look like

Both `team_mapping` and `role_mappings` match against the **same normalised group list**, so the workspace and the
role travel in one claim.

**Agent runtime, from the exchange:**

```json
{
  "iss":    "https://broker.tenant-a.internal/oidc",
  "aud":    "mcp-gw.tenant-a",
  "sub":    "agent-7",
  "tid":    "tenant-a",
  "wid":    "ws-42",
  "groups": ["ws-42", "ServiceUser"],
  "exp":    1755600000
}
```

→ member of workspace 42's team, `viewer` role: it can list and run that workspace's tools, not change the catalog.
`sub` and `wid` are not used by CF for authorization — they are there for audit, and for the control plane later,
which needs the full `(tenant, workspace, agent, user)` tuple.

**A human curating tools:**

```json
{
  "iss":    "https://broker.tenant-a.internal/oidc",
  "aud":    "mcp-gw.tenant-a",
  "sub":    "ada@ibm.com",
  "tid":    "tenant-a",
  "groups": ["ws-42", "ServiceAdmin"]
}
```

→ same team, `developer` role: Ada can register gateways and edit the catalog. `ServiceOwner` maps to `team_admin`,
which adds managing the team's members. One group per workspace she administers — not one per agent.

Matching is case-insensitive on both maps.

---

## 4. What Forge does at runtime

1. Verifies signature, issuer, expiry and `aud` against the broker's JWKS.
2. Resolves the caller. A client-credentials token becomes a service principal
   (`svc-<client>@<provider>.service.local`), and the `groups`, `roles`, `realm_access` and `resource_access` claims
   are deliberately carried through so mapping still applies.
3. `_apply_team_mapping` turns `ws-42` into membership of the workspace team; `role_mappings` assigns the RBAC role.
4. Tools with `visibility: team` on that team become listable and callable.

**Reconciliation runs both ways.** Remove `ws-42` from the token's groups and the next authentication revokes the
membership. It only touches rows it created (`grant_source = "sso"`), so anything added by hand survives.

**Revocation lag** is the identity cache — 60s by default, `0` for immediate.

---

## 5. What this deliberately does not do

**CF enforces at workspace granularity.** Every agent in a workspace can reach every tool on that workspace's team.
The per-agent tool list in CUGA is configuration, not enforcement — a prompt-injected agent is not stopped by it.

That is the accepted trade for the PoC. The finer decision needs `(tenant_id, workspace_id, agent_id, user_id)`
together, which is the control plane's job, not CF's. Two consequences to keep visible:

- Until the control plane exists, treat a workspace as the blast radius of any one agent in it. Put an agent that
  touches something sensitive in **its own workspace** — it then gets its own team, and CF enforces the boundary
  without any new mapping concept.
- Do not mark workspace tools `public`. Public is global within the instance and shows to callers scoped to another
  team.

---

## 6. Before building on this

**Confirm `role_mappings` scope.** In at least one branch, team-scoped SSO role assignments resolve via the user's
*personal* team, which is not what we want. Verify on the deployed build that `ServiceUser → viewer` lands scoped to
the workspace team. If it doesn't, fall back to `default_role` in `provider_metadata` — the MCP execute check has an
any-team fallback, so a single role assignment should still satisfy `tools.execute`.

**Confirm `GET /tools` returns team tools** for a caller mapped this way — [#5591](https://github.com/IBM/mcp-context-forge/issues/5591)
reports empty results in some configurations, and the CUGA tool picker depends on it.

**Owner vs Admin is a new distinction.** CUGA treats `ServiceOwner` and `ServiceAdmin` as one gate today (both in
`auth.manage_roles`). Splitting them across `team_admin` and `developer` on the Forge side is a choice to confirm,
not an existing behaviour.

---

## 7. How this relates to CUGA's existing auth config

Most of this is not new machinery — it is the IAM-proxy exchange we already run, with three extra requirements
placed on the token it returns.

| CUGA today (`[auth]`) | Role now | Notes |
|---|---|---|
| OIDC `discovery_url`, `client_id`, `client_secret`, `redirect_uri` | Human login, unchanged | Moves behind APISIX at the north edge; CUGA keeps it for `/manage` and chat |
| `jwks_cache_ttl`, `oidc_ca_bundle`, `oidc_skip_verify` | Same job, mirrored on the Forge side | Forge fetches the broker's JWKS itself and needs the same TLS trust — an internal CA bundle, not `skip_verify` |
| `iam_proxy_url` + `role_token_source = "iam_proxy"` | **This is the broker** | The exchange endpoint that mints the token Forge will trust |
| `iam_proxy_ca_bundle`, `iam_proxy_skip_verify` | Same problem, other direction | Today CUGA trusts the proxy's cert; now Forge must too |
| `manage_roles` / `chat_roles` (`ServiceOwner`, `ServiceAdmin`, `ServiceUser`) | The values in `role_mappings` | Keep the names identical so one vocabulary spans both systems |

CUGA already validates tokens the way Forge will: `jwt_validator.py` normalises an issuer, appends
`/.well-known/openid-configuration`, fetches the JWKS and verifies. `issuer_allowlist.py` even rejects non-HTTPS
discovery URLs. So the pattern is familiar — what changes is **who** is doing the verifying.

### The delta to ask the IAM-proxy / broker owners for

Today the exchanged token is consumed *inside* CUGA to resolve session roles. Nobody else inspects it, so it needs
no particular shape. In this design a third party verifies it, which imposes four things it may not do yet:

1. **A stable `iss` with published JWKS**, reachable from the gateway namespace — this is the `issuer` and
   `jwks_uri` in section 1. If the proxy already serves OIDC discovery, Forge can use it as-is.
2. **A per-instance `aud`** — `mcp-gw.tenant-a`. There is no audience discipline today because the token never
   leaves CUGA; without it, one tenant's token verifies at another tenant's Forge.
3. **A group claim** carrying the workspace id and the `Service*` role, in whatever claim `groups_claim` names,
   plus `tid`, `wid` and `sub` for audit and for the control plane later.
4. **A short TTL plus a refresh path**, since the token now sits in the registry process behind long-lived MCP
   transports rather than being used once at login.

### What becomes redundant

`role_token_source = "iam_proxy"` on the inbound path. Once APISIX resolves roles and passes identity context, CUGA
resolving them again is a second source of truth that will eventually disagree with the first. One of them should
own it — APISIX is the better candidate, since it is required at the edge regardless.

### Config shape, side by side

```bash
# CUGA — inbound, human login (existing)
DYNACONF_AUTH__ENABLED=true
DYNACONF_AUTH__IAM_PROXY_URL=https://broker.tenant-a.internal
DYNACONF_AUTH__IAM_PROXY_CA_BUNDLE=/etc/ssl/internal-ca.pem
DYNACONF_AUTH__ROLE_TOKEN_SOURCE=iam_proxy      # redundant once APISIX supplies roles

# Forge — outbound, the same broker, now verified by a third party
SSO_API_TOKEN_AUTH_ENABLED=true
MCP_CLIENT_AUTH_ENABLED=true
MCP_REQUIRE_AUTH=true
# per-provider, set through PUT /auth/sso/admin/providers/sovereign-broker:
#   issuer       = https://broker.tenant-a.internal/oidc
#   jwks_uri     = https://broker.tenant-a.internal/oidc/jwks
#   api_audience = mcp-gw.tenant-a
```

---

## What this replaces

The earlier plan wrote membership per identity: create user → add team member → assign role, by a provisioning
component. That still works and is the right stand-in for a PoC — one `curl` and no dependency on a group existing
yet — but it needs a runtime writer and has no revocation story beyond remembering to delete the row.

Longer term, Forge's trust-mode epic ([#5885](https://github.com/IBM/mcp-context-forge/issues/5885)) would derive
teams from claims with no database write at all. Adopting it later is a provisioning change, not an architecture
change, because both models resolve to the same statement: **a group claim decides the team.**
