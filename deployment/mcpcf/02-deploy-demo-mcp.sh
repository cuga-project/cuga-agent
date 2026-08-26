#!/usr/bin/env bash
set -euo pipefail

# Deploy the tiny demo MCP server (manifests/60-demo-mcp), reusing the CUGA
# image already in the cluster registry — no new image build needed.
# See ../../cuga_mcpcf_poc/poc-plan.md Track A.
#
# Usage: ./02-deploy-demo-mcp.sh [path/to/forge.env]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${1:-${SCRIPT_DIR}/forge.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file not found: $ENV_FILE"
  echo "Usage: $0 [path/to/forge.env]"
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

REQUIRED_VARS=(NAMESPACE CUGA_IMAGE)
MISSING=()
for var in "${REQUIRED_VARS[@]}"; do
  [[ -z "${!var:-}" ]] && MISSING+=("$var")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "ERROR: the following required variables are not set in $ENV_FILE:"
  for v in "${MISSING[@]}"; do echo "  - $v"; done
  exit 1
fi

if ! oc whoami &>/dev/null; then
  echo "ERROR: not logged in — 'oc login' to the target cluster first (KUBECONFIG=...)"
  exit 1
fi

render() { envsubst < "$1"; }

echo "==> Demo MCP server config + deployment"
render "$SCRIPT_DIR/manifests/60-demo-mcp/configmap.yaml" | oc apply -f -
render "$SCRIPT_DIR/manifests/60-demo-mcp/deployment.yaml" | oc apply -f -

if [[ -n "${PULL_SECRET:-}" ]]; then  # pragma: allowlist secret
  echo "==> PULL_SECRET set — linking it to the default service account"
  oc secrets link default "$PULL_SECRET" --for=pull -n "$NAMESPACE"
  oc patch deployment demo-mcp-server -n "$NAMESPACE" --type=json \
    -p="[{\"op\":\"add\",\"path\":\"/spec/template/spec/imagePullSecrets\",\"value\":[{\"name\":\"$PULL_SECRET\"}]}]"
fi

echo "==> Waiting for rollout"
if ! oc rollout status deployment/demo-mcp-server -n "$NAMESPACE" --timeout=180s; then
  echo
  echo "Rollout did not complete. If this is an image pull failure, check:"
  echo "  oc get events -n $NAMESPACE --sort-by=.lastTimestamp | tail -20"
  echo "and set PULL_SECRET in forge.env, then re-run this script."
  exit 1
fi

echo "Done. Next: ./03-configure-forge.sh $ENV_FILE"
