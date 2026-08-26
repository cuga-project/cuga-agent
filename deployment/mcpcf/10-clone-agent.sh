#!/usr/bin/env bash
set -euo pipefail

# Clone a running CUGA agent onto the PoC image, in the same tenant, with the
# Context Forge tool browser turned on. See ../../cuga_mcpcf_poc/poc-plan.md
# Track E.
#
# The clone is a plain Deployment with NO CugaAgent owner reference, so the
# operator neither manages nor reverts it. The original agent is untouched.
# It reuses the tenant's existing Postgres as-is — no new database.
#
# Usage: ./10-clone-agent.sh [path/to/forge.env]

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

REQUIRED_VARS=(NAMESPACE TENANT_ID CLUSTER_DOMAIN CLONE_IMAGE MCP_AUDIENCE WORKSPACE_ID)
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

TENANT_NS="tenant-${TENANT_ID}"
CLONE_NAME="${CLONE_NAME:-poc-forge-agent}"
CLONE_AGENT_ID="${CLONE_AGENT_ID:-poc-forge-agent}"

# Pick the source agent: an explicit SOURCE_AGENT, else the first CugaAgent-owned
# Deployment in the tenant namespace. Never clone a previous clone.
if [[ -z "${SOURCE_AGENT:-}" ]]; then
  SOURCE_AGENT=$(oc get deploy -n "$TENANT_NS" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.ownerReferences[0].kind}{"\n"}{end}' \
    | awk -F'\t' '$2=="CugaAgent"{print $1; exit}')
fi
if [[ -z "$SOURCE_AGENT" ]]; then
  echo "ERROR: no CugaAgent-owned Deployment found in $TENANT_NS to clone from."
  echo "       Set SOURCE_AGENT in $ENV_FILE to name one explicitly."
  exit 1
fi
echo "==> Source agent:  $SOURCE_AGENT  (namespace $TENANT_NS)"
echo "==> Clone name:    $CLONE_NAME"
echo "==> Clone image:   $CLONE_IMAGE"

# A fresh instance id keeps the clone's config rows separate from the source's:
# rows are scoped by (tenant_id, instance_id, agent_id). Same tenant, same DB.
INSTANCE_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')
echo "==> New instance:  $INSTANCE_ID"

# The clone authenticates to Forge with a broker-minted token. In mock mode this
# expires (1h) — re-run 04-mint-token.sh and this script to refresh it.
if [[ ! -f "${SCRIPT_DIR}/.mcpcf-token" ]]; then
  echo "==> No .mcpcf-token found, minting one"
  "${SCRIPT_DIR}/04-mint-token.sh" "$ENV_FILE" >/dev/null
fi
FORGE_TOKEN=$(cat "${SCRIPT_DIR}/.mcpcf-token")

FORGE_HOST=$(oc get route forge -n "$NAMESPACE" -o jsonpath='{.spec.host}')
if [[ -z "$FORGE_HOST" ]]; then
  echo "ERROR: no 'forge' route in namespace $NAMESPACE — run ./01-deploy-forge.sh first."
  exit 1
fi

# Default to Forge's in-cluster Service, not its public route.
#
# The clone runs inside the cluster, so service-to-service is the right path
# anyway — but there is also a hard reason. Attaching a tool makes the CUGA
# *registry* open its own MCP connection to this URL via fastmcp, and that
# client builds its own httpx session that does real certificate verification.
# It never sees context_forge.verify_ssl (that setting only governs the
# manage-API's own catalog call). Against the route, whose cert is signed by
# the cluster's internal ingress CA, it fails with CERTIFICATE_VERIFY_FAILED
# and the tool never initialises — the attach reports status "partial" with
# the traceback in tool_errors. Plain HTTP in-cluster sidesteps it entirely.
# Set FORGE_URL_MODE=route to force the public route instead.
if [[ "${FORGE_URL_MODE:-service}" == "route" ]]; then
  FORGE_URL="https://${FORGE_HOST}"
else
  FORGE_URL="http://forge.${NAMESPACE}.svc.cluster.local"
fi
echo "==> Forge URL:     $FORGE_URL"

echo
echo "==> Rendering clone from the live Deployment"
oc get deploy "$SOURCE_AGENT" -n "$TENANT_NS" -o json \
  | python3 "${SCRIPT_DIR}/_clone_agent.py" \
      --name "$CLONE_NAME" \
      --instance-id "$INSTANCE_ID" \
      --agent-id "$CLONE_AGENT_ID" \
      --image "$CLONE_IMAGE" \
      --namespace "$TENANT_NS" \
      --cluster-domain "$CLUSTER_DOMAIN" \
      --forge-url "$FORGE_URL" \
      --forge-audience "$MCP_AUDIENCE" \
      --forge-workspace-group "ws-${WORKSPACE_ID}" \
      --forge-token "$FORGE_TOKEN" \
  | oc apply -f -

echo
echo "==> Waiting for the clone to roll out"
if ! oc rollout status "deployment/$CLONE_NAME" -n "$TENANT_NS" --timeout=300s; then
  echo
  echo "Rollout did not complete. Useful next steps:"
  echo "  oc get pods -n $TENANT_NS -l app=$CLONE_NAME"
  echo "  oc logs -n $TENANT_NS -l app=$CLONE_NAME --tail=60"
  echo "  oc get events -n $TENANT_NS --sort-by=.lastTimestamp | tail -20"
  exit 1
fi

echo
echo "==> Confirming the operator has NOT adopted the clone"
if oc get cugaagent "$CLONE_NAME" -n agent-service-broker &>/dev/null; then
  echo "    WARNING: a CugaAgent named $CLONE_NAME exists on the hub — investigate before trusting this clone."
else
  echo "    OK: no CugaAgent CR named $CLONE_NAME (clone is unmanaged, as intended)"
fi

CLONE_HOST=$(oc get route "$CLONE_NAME" -n "$TENANT_NS" -o jsonpath='{.spec.host}')
echo
echo "==> Clone route: https://${CLONE_HOST}"
curl -s -k -o /dev/null -w '    / -> %{http_code}\n' --max-time 30 "https://${CLONE_HOST}/" || true
curl -s -k -o /dev/null -w '    /api/manage/forge/catalog -> %{http_code}\n' --max-time 30 \
  "https://${CLONE_HOST}/api/manage/forge/catalog" || true
echo
echo "Done. Open https://${CLONE_HOST}/manage -> Tools -> Browse workspace catalog"
