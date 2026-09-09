#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Deploy Context Forge (+ its own Postgres/Redis) into a fresh namespace in
# the mcpcf-poc tenant. See QUICKSTART.md step 2.
#
# Usage:
#   ./01-deploy-forge.sh [path/to/forge.env]
#
# Prerequisites:
#   - oc logged in to the target cluster (KUBECONFIG pointed at it)
#   - forge.env filled in (copy from forge.env.example)
# ---------------------------------------------------------------------------

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

REQUIRED_VARS=(
  NAMESPACE TENANT_ID CLUSTER_DOMAIN
  FORGE_IMAGE
  JWT_SECRET_KEY AUTH_ENCRYPTION_SECRET PLATFORM_ADMIN_PASSWORD BASIC_AUTH_PASSWORD
  POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB
  REDIS_PASSWORD
  PLATFORM_ADMIN_EMAIL
)

MISSING=()
for var in "${REQUIRED_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    MISSING+=("$var")
  fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "ERROR: the following required variables are not set in $ENV_FILE:"
  for v in "${MISSING[@]}"; do
    echo "  - $v"
  done
  exit 1
fi

if ! oc whoami &>/dev/null; then
  echo "ERROR: not logged in — 'oc login' to the target cluster first (KUBECONFIG=...)"
  exit 1
fi

echo "==> Target: $(oc whoami --show-server 2>/dev/null) as $(oc whoami)"
echo "==> Namespace: $NAMESPACE"

render() {
  envsubst < "$1"
}

echo
echo "==> 1/7  Namespace"
render "$SCRIPT_DIR/manifests/00-namespace/namespace.yaml" | oc apply -f -

echo
echo "==> 2/7  Postgres (Forge's own — the tenant's shared Postgres is untouched)"
for f in secret pvc deployment; do
  render "$SCRIPT_DIR/manifests/10-postgres/$f.yaml" | oc apply -f -
done

echo
echo "==> 3/7  Redis"
render "$SCRIPT_DIR/manifests/20-redis/deployment.yaml" | oc apply -f -

echo
echo "==> 4/7  Wait for Postgres and Redis to be ready before starting Forge"
oc rollout status deployment/forge-postgres -n "$NAMESPACE" --timeout=180s
oc rollout status deployment/forge-redis -n "$NAMESPACE" --timeout=120s

echo
echo "==> 5/7  Router CA bundle (Forge needs to trust this cluster's internally-signed"
echo "         route certs for its own outbound OIDC calls — Track B, see README.md)"
if oc get configmap router-ca-bundle -n "$NAMESPACE" &>/dev/null; then
  echo "    router-ca-bundle already exists, reusing it"
else
  CA_FILE=$(mktemp)
  trap 'rm -f "$CA_FILE"' EXIT
  echo | openssl s_client -connect "console-openshift-console.apps.${CLUSTER_DOMAIN}:443" \
    -servername "console-openshift-console.apps.${CLUSTER_DOMAIN}" -showcerts 2>/dev/null \
    | awk '/BEGIN CERTIFICATE/,/END CERTIFICATE/' > "$CA_FILE"
  if ! grep -q "BEGIN CERTIFICATE" "$CA_FILE"; then
    echo "ERROR: could not extract a certificate chain from console-openshift-console.apps.${CLUSTER_DOMAIN}"
    exit 1
  fi
  oc create configmap router-ca-bundle -n "$NAMESPACE" --from-file=ca.pem="$CA_FILE"
fi

echo
echo "==> 6/7  Forge config + deployment + route"
render "$SCRIPT_DIR/manifests/30-forge-config/configmap.yaml" | oc apply -f -
render "$SCRIPT_DIR/manifests/30-forge-config/secret.yaml" | oc apply -f -
render "$SCRIPT_DIR/manifests/40-forge/deployment.yaml" | oc apply -f -
render "$SCRIPT_DIR/manifests/50-forge-route/route.yaml" | oc apply -f -

echo
echo "==> 7/7  Wait for Forge"
oc rollout status deployment/forge -n "$NAMESPACE" --timeout=300s

ROUTE_HOST=$(oc get route forge -n "$NAMESPACE" -o jsonpath='{.spec.host}')
echo
echo "==> Forge route: https://$ROUTE_HOST"
echo "==> Health check:"
curl -s -k -o /dev/null -w '    /health -> %{http_code}\n' --max-time 20 "https://$ROUTE_HOST/health" || true
echo
echo "Done. Next: ./02-deploy-demo-mcp.sh $ENV_FILE"
