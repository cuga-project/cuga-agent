#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# CUGA OpenShift Deployment Script
#
# Usage:
#   ./deploy-openshift.sh [path/to/openshift.env] [--with-postgres] [--with-vault]
#
# Prerequisites:
#   - Logged in to OpenShift cluster via `oc login` or `kubectl` with valid kubeconfig
#   - helm 3 installed
#   - openshift.env filled in (copy from openshift.env template)
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WITH_POSTGRES=false
WITH_VAULT=false
ENV_FILE=""
for arg in "$@"; do
  if [[ "$arg" == "--with-postgres" ]]; then
    WITH_POSTGRES=true
  elif [[ "$arg" == "--with-vault" ]]; then
    WITH_VAULT=true
  else
    [[ -z "$ENV_FILE" ]] && ENV_FILE="$arg"
  fi
done
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/openshift.env}"

# ---------------------------------------------------------------------------
# Load environment
# ---------------------------------------------------------------------------

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file not found: $ENV_FILE"
  echo "Usage: $0 [path/to/openshift.env]"
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

# ---------------------------------------------------------------------------
# Validate required variables
# ---------------------------------------------------------------------------

REQUIRED_VARS=(
  INSTANCE_ID
  NAMESPACE
  ICR_API_KEY
  IMAGE_REPOSITORY
  IMAGE_TAG
  GROQ_API_KEY
  MODEL_NAME
  AGENT_SETTING_CONFIG
)

if [[ "$WITH_POSTGRES" == true ]]; then
  REQUIRED_VARS+=(POSTGRES_PASSWORD)
fi

MISSING=()
for var in "${REQUIRED_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    MISSING+=("$var")
  fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "ERROR: The following required variables are not set in $ENV_FILE:"
  for v in "${MISSING[@]}"; do
    echo "  - $v"
  done
  exit 1
fi

# Derived names — all scoped to INSTANCE_ID so multiple instances coexist
RELEASE_NAME="cuga-${INSTANCE_ID}"
PULL_SECRET_NAME="${INSTANCE_ID}-icr-pull-secret"
ENV_SECRET_NAME="${INSTANCE_ID}-env-secret"
CHART_PATH="${SCRIPT_DIR}/cuga"
TOTAL_STEPS=6
[[ "$WITH_POSTGRES" != true ]] && TOTAL_STEPS=5
[[ "$WITH_VAULT" == true ]] && TOTAL_STEPS=$((TOTAL_STEPS + 1))

echo ""
echo "========================================"
echo "  CUGA OpenShift Deployment"
echo "  Instance  : ${INSTANCE_ID}"
echo "  Release   : ${RELEASE_NAME}"
echo "  Namespace : ${NAMESPACE}"
echo "  Hostname  : ${ROUTE_HOSTNAME:-<auto-assigned by OpenShift>}"
if [[ "$WITH_POSTGRES" == true ]]; then
  echo "  Postgres  : enabled (shared per namespace)"
fi
if [[ "$WITH_VAULT" == true ]]; then
  echo "  Vault     : enabled"
fi
echo "========================================"
echo ""

# ---------------------------------------------------------------------------
# 1. Create namespace (idempotent)
# ---------------------------------------------------------------------------

STEP=1
echo "[${STEP}/${TOTAL_STEPS}] Creating namespace: ${NAMESPACE}"
kubectl create namespace "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
((STEP++))

# ---------------------------------------------------------------------------
# 2. Postgres secret + Helm (when --with-postgres)
# ---------------------------------------------------------------------------

if [[ "$WITH_POSTGRES" == true ]]; then
  echo "[${STEP}/${TOTAL_STEPS}] Creating postgres secret (postgres-secret)"
  kubectl create secret generic postgres-secret \
    --from-literal=password="${POSTGRES_PASSWORD}" \
    --namespace="${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
  ((STEP++))

  echo "[${STEP}/${TOTAL_STEPS}] Deploying postgres (postgres-pgvector)"
  helm upgrade --install postgres-pgvector "${SCRIPT_DIR}/postgres-pgvector" \
    --namespace "${NAMESPACE}" \
    --set "auth.database=${POSTGRES_DB:-cuga}" \
    --set "auth.username=${POSTGRES_USER:-cuga}" \
    --set "auth.existingSecret=postgres-secret" \
    --set "auth.existingSecretKey=password"
  ((STEP++))
fi

# ---------------------------------------------------------------------------
# 3. Image pull secret for IBM Container Registry
# ---------------------------------------------------------------------------

echo "[${STEP}/${TOTAL_STEPS}] Creating image pull secret: ${PULL_SECRET_NAME}"
kubectl create secret docker-registry "${PULL_SECRET_NAME}" \
  --docker-server=us.icr.io \
  --docker-username=iamapikey \
  --docker-password="${ICR_API_KEY}" \
  --namespace="${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
((STEP++))

# ---------------------------------------------------------------------------
# 4. Environment secret (sensitive values only)
# ---------------------------------------------------------------------------

echo "[${STEP}/${TOTAL_STEPS}] Creating env secret: ${ENV_SECRET_NAME}"

# Build --from-literal args for sensitive keys
SECRET_ARGS=(
  "--from-literal=GROQ_API_KEY=${GROQ_API_KEY}"
)

[[ -n "${OIDC_CLIENT_ID:-}" ]]            && SECRET_ARGS+=("--from-literal=OIDC_CLIENT_ID=${OIDC_CLIENT_ID}")
[[ -n "${OIDC_CLIENT_SECRET:-}" ]]        && SECRET_ARGS+=("--from-literal=OIDC_CLIENT_SECRET=${OIDC_CLIENT_SECRET}")
[[ -n "${OIDC_DISCOVERY_URL:-}" ]]        && SECRET_ARGS+=("--from-literal=OIDC_DISCOVERY_URL=${OIDC_DISCOVERY_URL}")
[[ -n "${OIDC_REDIRECT_URI:-}" ]]         && SECRET_ARGS+=("--from-literal=OIDC_REDIRECT_URI=${OIDC_REDIRECT_URI}")
[[ -n "${VAULT_TOKEN:-}" ]]               && SECRET_ARGS+=("--from-literal=VAULT_TOKEN=${VAULT_TOKEN}")
[[ -n "${CUGA_SECRET_KEY:-}" ]]           && SECRET_ARGS+=("--from-literal=CUGA_SECRET_KEY=${CUGA_SECRET_KEY}")

if [[ "$WITH_POSTGRES" == true ]]; then
  PG_URL="postgresql://${POSTGRES_USER:-cuga}:${POSTGRES_PASSWORD}@postgres-pgvector.${NAMESPACE}.svc.cluster.local:5432/${POSTGRES_DB:-cuga}"
  SECRET_ARGS+=("--from-literal=DYNACONF_STORAGE__POSTGRES_URL=${PG_URL}")
else
  [[ -n "${DYNACONF_STORAGE__POSTGRES_URL:-}" ]] && SECRET_ARGS+=("--from-literal=DYNACONF_STORAGE__POSTGRES_URL=${DYNACONF_STORAGE__POSTGRES_URL}")
fi

kubectl create secret generic "${ENV_SECRET_NAME}" \
  "${SECRET_ARGS[@]}" \
  --namespace="${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
((STEP++))

# ---------------------------------------------------------------------------
# Optional: Vault deployment
# ---------------------------------------------------------------------------

if [[ "$WITH_VAULT" == true ]]; then
  echo "[${STEP}/${TOTAL_STEPS}] Deploying HashiCorp Vault"
  VAULT_CHART_PATH="${SCRIPT_DIR}/vault"

  helm repo add hashicorp https://helm.releases.hashicorp.com 2>/dev/null || true
  helm repo update hashicorp 2>/dev/null || true
  helm dependency update "${VAULT_CHART_PATH}" 2>/dev/null || true

  helm upgrade --install vault "${VAULT_CHART_PATH}" \
    --namespace "${NAMESPACE}" \
    -f "${VAULT_CHART_PATH}/values.openshift.yaml" \
    ${VAULT_TOKEN:+--set "vault.server.extraEnvironmentVars.VAULT_DEV_ROOT_TOKEN_ID=${VAULT_TOKEN}"}
  ((STEP++))

  echo ""
  echo "  Vault deployed. Initialize it (first time only):"
  echo "    kubectl exec -n ${NAMESPACE} -it vault-0 -- vault operator init"
  echo ""
  echo "  Add to your env file:"
  echo "    VAULT_TOKEN=<root-token>"
  echo "    DYNACONF_SECRETS__MODE=vault"
  echo "    DYNACONF_SECRETS__VAULT_ADDR=http://vault.${NAMESPACE}.svc.cluster.local:8200"
  echo ""
fi

# ---------------------------------------------------------------------------
# 5. Helm deploy (cuga)
# ---------------------------------------------------------------------------

echo "[${STEP}/${TOTAL_STEPS}] Deploying Helm release: ${RELEASE_NAME}"

STORAGE_MODE="${DYNACONF_STORAGE__MODE:-local}"
[[ "$WITH_POSTGRES" == true ]] && STORAGE_MODE=prod

HELM_ARGS=(
  upgrade --install "${RELEASE_NAME}" "${CHART_PATH}"
  --namespace "${NAMESPACE}"
  --set "image.repository=${IMAGE_REPOSITORY}"
  --set "image.tag=${IMAGE_TAG}"
  --set "image.pullPolicy=Always"
  --set "imagePullSecrets[0].name=${PULL_SECRET_NAME}"
  --set "existingSecret=${ENV_SECRET_NAME}"
  --set "env.DYNACONF_SERVICE__INSTANCE_ID=${DYNACONF_SERVICE__INSTANCE_ID:-${INSTANCE_ID}}"
  --set "env.DYNACONF_SERVICE__TENANT_ID=${DYNACONF_SERVICE__TENANT_ID:-${NAMESPACE}}"
  --set "env.UV_CACHE_DIR=${UV_CACHE_DIR:-/tmp/uv-cache}"
  --set "env.MODEL_NAME=${MODEL_NAME}"
  --set "env.AGENT_SETTING_CONFIG=${AGENT_SETTING_CONFIG}"
  --set "env.DYNACONF_AUTH__ENABLED=${DYNACONF_AUTH__ENABLED:-true}"
  --set "env.DYNACONF_STORAGE__MODE=${STORAGE_MODE}"
  --set "route.enabled=true"
)

if [[ "$WITH_VAULT" == true ]]; then
  VAULT_INTERNAL_ADDR="http://vault.${NAMESPACE}.svc.cluster.local:8200"
  HELM_ARGS+=(
    "--set" "env.DYNACONF_SECRETS__MODE=vault"
    "--set" "env.DYNACONF_SECRETS__VAULT_ADDR=${VAULT_ADDR:-${VAULT_INTERNAL_ADDR}}"
    "--set" "env.DYNACONF_SECRETS__VAULT_TOKEN_ENV=VAULT_TOKEN"
  )
fi

if [[ -n "${DYNACONF_SECRETS__MODE:-}" ]]; then
  HELM_ARGS+=("--set" "env.DYNACONF_SECRETS__MODE=${DYNACONF_SECRETS__MODE}")
fi

if [[ -n "${ROUTE_HOSTNAME:-}" ]]; then
  HELM_ARGS+=("--set" "route.hostname=${ROUTE_HOSTNAME}")
fi

helm "${HELM_ARGS[@]}"

# ---------------------------------------------------------------------------
# 6. Print access URLs
# ---------------------------------------------------------------------------

echo ""
echo "[${STEP}/${TOTAL_STEPS}] Deployment complete. Fetching route..."

# Give the route a moment to be assigned a host if no hostname was specified
sleep 2

ASSIGNED_HOST=$(kubectl get route "${RELEASE_NAME}" \
  --namespace="${NAMESPACE}" \
  -o jsonpath='{.spec.host}' 2>/dev/null || true)

echo ""
echo "========================================"
echo "  Access URLs (HTTPS)"
if [[ -n "${ASSIGNED_HOST}" ]]; then
  echo "  App     : https://${ASSIGNED_HOST}/"
  echo "  Chat    : https://${ASSIGNED_HOST}/chat"
  echo "  Manage  : https://${ASSIGNED_HOST}/manage"
else
  echo "  Route not yet ready. Check with:"
  echo "  kubectl get route ${RELEASE_NAME} -n ${NAMESPACE}"
fi
echo "========================================"
echo ""
