#!/usr/bin/env python3
"""Forge API orchestration for 03-configure-forge.sh — Track B.

Stdlib only (urllib) so it runs without extra deps on whatever machine runs
the deploy scripts. Idempotent via a small JSON state file: re-running
updates the same team/gateway/provider instead of creating duplicates
(the one exception is the team's *slug*, which must always be unique per
upstream issue #5700 — a fresh team always gets a fresh slug, but an
existing team recorded in state is reused as-is, slug included).
"""

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
import uuid

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE  # internal CA on this cluster — see cuga-sovereign skill


def call(method, url, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=20) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw.decode(errors="replace")}


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(path, state):
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--forge-url", required=True)
    p.add_argument("--admin-email", required=True)
    p.add_argument("--admin-password", required=True)
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--workspace-id", required=True)
    p.add_argument("--group-value", required=True)
    p.add_argument("--mcp-audience", required=True)
    p.add_argument("--broker-issuer", required=True)
    p.add_argument("--broker-jwks-uri", required=True)
    p.add_argument("--demo-mcp-url", required=True)
    p.add_argument("--state-file", required=True)
    args = p.parse_args()

    state = load_state(args.state_file)

    # 1. Login
    print("==> Logging in as platform admin")
    status, resp = call(
        "POST",
        f"{args.forge_url}/auth/email/login",
        body={
            "email": args.admin_email,
            "password": args.admin_password,
        },
    )
    if status != 200:
        sys.exit(f"Login failed ({status}): {resp}")
    token = resp["access_token"]

    # 2. Team — reuse from state if it still exists, else create with a fresh unique slug
    team_id = state.get("team_id")
    if team_id:
        status, resp = call("GET", f"{args.forge_url}/v1/teams/{team_id}", token=token)
        if status != 200:
            print(f"    Recorded team {team_id} no longer exists ({status}) — creating a new one")
            team_id = None
    if not team_id:
        slug = f"ws-{args.workspace_id}-{uuid.uuid4().hex[:8]}"
        print(f"==> Creating team, slug={slug}")
        status, resp = call(
            "POST",
            f"{args.forge_url}/v1/teams/",
            token=token,
            body={
                # Team name is restricted to letters, numbers, spaces, underscores,
                # periods and dashes — confirmed live (an em dash 422'd).
                "name": f"MCPCF PoC - {args.workspace_id}",
                "slug": slug,
                "description": f"MCPCF PoC workspace team for tenant {args.tenant_id}",
                "visibility": "private",
            },
        )
        if status not in (200, 201):
            sys.exit(f"Team creation failed ({status}): {resp}")
        team_id = resp["id"]
        state["team_id"] = team_id
        state["team_slug"] = slug
        save_state(args.state_file, state)
    print(f"    team_id={team_id}")

    # 3. Gateway — register (or re-verify) the demo MCP server, scoped to the team
    gateway_id = state.get("gateway_id")
    if gateway_id:
        status, _ = call("GET", f"{args.forge_url}/v1/gateways/{gateway_id}", token=token)
        if status != 200:
            print(f"    Recorded gateway {gateway_id} no longer exists ({status}) — re-registering")
            gateway_id = None
    if not gateway_id:
        print("==> Registering demo MCP server as a gateway (visibility: team)")
        status, resp = call(
            "POST",
            f"{args.forge_url}/v1/gateways",
            token=token,
            body={
                "name": f"demo-mcp-{args.workspace_id}",
                "url": args.demo_mcp_url,
                "transport": "STREAMABLEHTTP",
                "visibility": "team",
                "teamId": team_id,
                "description": "MCPCF PoC demo MCP server",
            },
        )
        if status not in (200, 201):
            sys.exit(f"Gateway registration failed ({status}): {resp}")
        gateway_id = resp["id"]
        state["gateway_id"] = gateway_id
        save_state(args.state_file, state)
    print(f"    gateway_id={gateway_id}")

    # 4. SSO provider "sovereign-broker" — fixed id, always a full read-modify-write
    #    so team_mapping accumulates across workspaces instead of clobbering them.
    provider_id = "sovereign-broker"
    print(f"==> Configuring SSO provider '{provider_id}'")
    status, existing = call("GET", f"{args.forge_url}/v1/auth/sso/admin/providers/{provider_id}", token=token)
    team_mapping = dict(existing.get("team_mapping") or {}) if status == 200 else {}
    team_mapping[args.group_value] = {"team_id": team_id, "role": "member"}

    provider_body = {
        "id": provider_id,
        "name": provider_id,
        "display_name": "Sovereign Broker (PoC)",
        "provider_type": "oidc",
        # OAuth-flow fields are required by the schema but unused by our
        # bearer-only path (trusted_for_api_auth validates tokens directly
        # against issuer+jwks_uri — it never drives an interactive login).
        "client_id": "mcpcf-poc",
        "client_secret": "unused-trusted-for-api-auth-only",  # pragma: allowlist secret
        "authorization_url": f"{args.broker_issuer}/authorize",
        "token_url": f"{args.broker_issuer}/token",
        "userinfo_url": f"{args.broker_issuer}/userinfo",
        "issuer": args.broker_issuer,
        "jwks_uri": args.broker_jwks_uri,
        "trusted_for_api_auth": True,
        "api_audience": args.mcp_audience,
        "auto_create_users": True,
        "team_mapping": team_mapping,
        "provider_metadata": {
            "groups_claim": "groups",
            "role_mappings": {
                "ServiceOwner": "team_admin",
                "ServiceAdmin": "developer",
                "ServiceUser": "viewer",
            },
        },
    }

    if status == 200:
        status, resp = call(
            "PUT",
            f"{args.forge_url}/v1/auth/sso/admin/providers/{provider_id}",
            token=token,
            body=provider_body,
        )
    else:
        status, resp = call(
            "POST", f"{args.forge_url}/v1/auth/sso/admin/providers", token=token, body=provider_body
        )
    if status not in (200, 201):
        sys.exit(f"SSO provider configuration failed ({status}): {resp}")
    state["provider_id"] = provider_id
    state["group_value"] = args.group_value
    save_state(args.state_file, state)
    print(f"    team_mapping now covers: {list(team_mapping.keys())}")


if __name__ == "__main__":
    main()
