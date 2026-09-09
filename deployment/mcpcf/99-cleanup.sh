#!/usr/bin/env bash
set -euo pipefail

# Tear down everything this PoC created. See QUICKSTART.md step 5.
# Currently covers Track A (Forge namespace + demo MCP server) and, once
# 10-clone-agent.sh exists, the cloned agent Deployment/Service/Route in the
# tenant namespace — added here as that script lands.
#
# Usage: ./99-cleanup.sh [path/to/forge.env]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${1:-${SCRIPT_DIR}/forge.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file not found: $ENV_FILE"
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

if [[ -z "${NAMESPACE:-}" ]]; then
  echo "ERROR: NAMESPACE not set in $ENV_FILE"
  exit 1
fi

if ! oc whoami &>/dev/null; then
  echo "ERROR: not logged in — 'oc login' to the target cluster first (KUBECONFIG=...)"
  exit 1
fi

echo "==> This deletes namespace '$NAMESPACE' entirely (Forge, its Postgres/Redis, the demo MCP server)."
read -r -p "    Type the namespace name to confirm: " CONFIRM
if [[ "$CONFIRM" != "$NAMESPACE" ]]; then
  echo "Aborted — confirmation did not match."
  exit 1
fi

oc delete namespace "$NAMESPACE"

echo
echo "Forge namespace deleted. The cloned agent (Track E, deployed in the"
echo "tenant namespace, not this one) is not touched by this script yet —"
echo "delete it manually until 10-clone-agent.sh is written:"
echo "  oc delete deployment,service,route -l app=poc-forge-agent -n tenant-\$TENANT_ID"
echo
echo "The original CugaAgent-managed agents are never touched by this PoC."
