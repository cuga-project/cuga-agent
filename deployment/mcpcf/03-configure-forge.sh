#!/usr/bin/env bash
set -euo pipefail

# Wire Option A on Forge: register the demo MCP server as a team-visibility
# gateway, create a workspace team with a unique slug, and configure the
# "sovereign-broker" SSO provider (issuer/jwks_uri/api_audience/team_mapping/
# role_mappings). Idempotent — state persisted to .mcpcf-state.json (gitignored)
# so re-runs update existing objects instead of creating duplicates.
# See QUICKSTART.md step 2.
#
# Usage: ./03-configure-forge.sh [path/to/forge.env]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${1:-${SCRIPT_DIR}/forge.env}"
STATE_FILE="${SCRIPT_DIR}/.mcpcf-state.json"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file not found: $ENV_FILE"
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

REQUIRED_VARS=(NAMESPACE TENANT_ID PLATFORM_ADMIN_EMAIL PLATFORM_ADMIN_PASSWORD MCP_AUDIENCE WORKSPACE_ID)
MISSING=()
for var in "${REQUIRED_VARS[@]}"; do
  [[ -z "${!var:-}" ]] && MISSING+=("$var")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "ERROR: the following required variables are not set in $ENV_FILE:"
  for v in "${MISSING[@]}"; do echo "  - $v"; done
  exit 1
fi

if [[ "${BROKER_MODE:-mock}" == "mock" && ( -z "${BROKER_ISSUER:-}" || -z "${BROKER_JWKS_URI:-}" ) ]]; then
  echo "ERROR: BROKER_MODE=mock but BROKER_ISSUER/BROKER_JWKS_URI are empty."
  echo "Run ./03a-deploy-mock-broker.sh $ENV_FILE first."
  exit 1
fi
if [[ "${BROKER_MODE:-mock}" == "real" && ( -z "${BROKER_ISSUER:-}" || -z "${BROKER_JWKS_URI:-}" ) ]]; then
  echo "ERROR: BROKER_MODE=real needs BROKER_ISSUER/BROKER_JWKS_URI set to the IAM proxy's real values in $ENV_FILE."
  exit 1
fi

if ! oc whoami &>/dev/null; then
  echo "ERROR: not logged in — 'oc login' to the target cluster first (KUBECONFIG=...)"
  exit 1
fi

ROUTE_HOST=$(oc get route forge -n "$NAMESPACE" -o jsonpath='{.spec.host}')
if [[ -z "$ROUTE_HOST" ]]; then
  echo "ERROR: no 'forge' route found in namespace $NAMESPACE — run ./01-deploy-forge.sh first."
  exit 1
fi
FORGE_URL="https://$ROUTE_HOST"
echo "==> Forge: $FORGE_URL"

DEMO_MCP_URL="http://demo-mcp-server.${NAMESPACE}.svc.cluster.local:8080/mcp"
GROUP_VALUE="ws-${WORKSPACE_ID}"

python3 "$SCRIPT_DIR/_configure_forge.py" \
  --forge-url "$FORGE_URL" \
  --admin-email "$PLATFORM_ADMIN_EMAIL" \
  --admin-password "$PLATFORM_ADMIN_PASSWORD" \
  --tenant-id "$TENANT_ID" \
  --workspace-id "$WORKSPACE_ID" \
  --group-value "$GROUP_VALUE" \
  --mcp-audience "$MCP_AUDIENCE" \
  --broker-issuer "$BROKER_ISSUER" \
  --broker-jwks-uri "$BROKER_JWKS_URI" \
  --demo-mcp-url "$DEMO_MCP_URL" \
  --state-file "$STATE_FILE"

echo
echo "Done. State written to $STATE_FILE. Next: ./04-mint-token.sh $ENV_FILE"
