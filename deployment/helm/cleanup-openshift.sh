#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# CUGA OpenShift Cleanup Script
#
# Removes all resources deployed by deploy-openshift.sh for a given instance.
# Does NOT touch other instances or the namespace itself (unless --delete-namespace).
# Use --with-postgres to also remove the shared postgres (postgres-pgvector + postgres-secret).
#
# Usage:
#   ./cleanup-openshift.sh [path/to/openshift.env] [--with-postgres] [--delete-namespace]
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WITH_POSTGRES=false
DELETE_NAMESPACE=false
ENV_FILE=""
for arg in "$@"; do
  if [[ "$arg" == "--with-postgres" ]]; then
    WITH_POSTGRES=true
  elif [[ "$arg" == "--delete-namespace" ]]; then
    DELETE_NAMESPACE=true
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
  echo "Usage: $0 [path/to/openshift.env] [--with-postgres] [--delete-namespace]"
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

if [[ -z "${INSTANCE_ID:-}" ]]; then
  echo "ERROR: INSTANCE_ID is not set in $ENV_FILE"
  exit 1
fi

NAMESPACE="${NAMESPACE:-cuga}"
RELEASE_NAME="cuga-${INSTANCE_ID}"
KUBECTL_TIMEOUT="${KUBECTL_REQUEST_TIMEOUT:-120}"
HELM_TIMEOUT="${HELM_TIMEOUT:-10m}"
PULL_SECRET_NAME="${INSTANCE_ID}-icr-pull-secret"
ENV_SECRET_NAME="${INSTANCE_ID}-env-secret"

echo ""
echo "========================================"
echo "  CUGA OpenShift Cleanup"
echo "  Instance  : ${INSTANCE_ID}"
echo "  Release   : ${RELEASE_NAME}"
echo "  Namespace : ${NAMESPACE}"
if [[ "$WITH_POSTGRES" == true ]]; then
  echo "  Postgres  : will be removed (postgres-pgvector + postgres-secret)"
fi
if [[ "$DELETE_NAMESPACE" == true ]]; then
  echo "  WARNING   : Namespace will be DELETED"
fi
echo "========================================"
echo ""
read -r -p "Are you sure you want to delete all resources for instance '${INSTANCE_ID}'? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
echo ""

# ---------------------------------------------------------------------------
# 1. Uninstall Helm release (removes deployment, service, route, pvc)
# ---------------------------------------------------------------------------

echo "[1/4] Uninstalling Helm release: ${RELEASE_NAME}"
if helm status "${RELEASE_NAME}" --namespace "${NAMESPACE}" &>/dev/null; then
  helm uninstall "${RELEASE_NAME}" --namespace "${NAMESPACE}" --timeout "${HELM_TIMEOUT}"
  echo "      Release ${RELEASE_NAME} uninstalled."
else
  echo "      Release ${RELEASE_NAME} not found, skipping."
fi

# ---------------------------------------------------------------------------
# 2. Delete secrets and NetworkPolicies
# ---------------------------------------------------------------------------

echo "[2/4] Deleting secrets and policies for instance: ${INSTANCE_ID}"

for secret in "${PULL_SECRET_NAME}" "${ENV_SECRET_NAME}"; do
  if kubectl get secret "${secret}" --namespace "${NAMESPACE}" --request-timeout="${KUBECTL_TIMEOUT}" &>/dev/null; then
    kubectl delete secret "${secret}" --namespace "${NAMESPACE}" --request-timeout="${KUBECTL_TIMEOUT}"
    echo "      Deleted secret: ${secret}"
  else
    echo "      Secret ${secret} not found, skipping."
  fi
done

if kubectl get networkpolicy "${INSTANCE_ID}-airgap-egress" --namespace "${NAMESPACE}" --request-timeout="${KUBECTL_TIMEOUT}" &>/dev/null; then
  kubectl delete networkpolicy "${INSTANCE_ID}-airgap-egress" --namespace "${NAMESPACE}" --request-timeout="${KUBECTL_TIMEOUT}"
  echo "      Deleted NetworkPolicy: ${INSTANCE_ID}-airgap-egress"
fi

# ---------------------------------------------------------------------------
# 3. Optionally remove postgres (shared per namespace)
# ---------------------------------------------------------------------------

if [[ "$WITH_POSTGRES" == true ]]; then
  echo "[3/4] Removing postgres (postgres-pgvector + postgres-secret)"
  if helm status postgres-pgvector --namespace "${NAMESPACE}" &>/dev/null; then
    helm uninstall postgres-pgvector --namespace "${NAMESPACE}" --timeout "${HELM_TIMEOUT}"
    echo "      Release postgres-pgvector uninstalled."
  else
    echo "      Release postgres-pgvector not found, skipping."
  fi
  if kubectl get secret postgres-secret --namespace "${NAMESPACE}" --request-timeout="${KUBECTL_TIMEOUT}" &>/dev/null; then
    kubectl delete secret postgres-secret --namespace "${NAMESPACE}" --request-timeout="${KUBECTL_TIMEOUT}"
    echo "      Deleted secret: postgres-secret"
  else
    echo "      Secret postgres-secret not found, skipping."
  fi
else
  echo "[3/4] Postgres left intact (pass --with-postgres to remove it)."
fi

# ---------------------------------------------------------------------------
# 4. Optionally delete namespace
# ---------------------------------------------------------------------------

if [[ "$DELETE_NAMESPACE" == true ]]; then
  echo "[4/4] Deleting namespace: ${NAMESPACE}"
  read -r -p "This will delete the ENTIRE namespace '${NAMESPACE}' and ALL resources inside it. Confirm? [y/N] " confirm2
  if [[ "$confirm2" =~ ^[Yy]$ ]]; then
    kubectl delete namespace "${NAMESPACE}" --request-timeout="${KUBECTL_TIMEOUT}"
    echo "      Namespace ${NAMESPACE} deleted."
  else
    echo "      Skipped namespace deletion."
  fi
else
  echo "[4/4] Namespace '${NAMESPACE}' left intact (pass --delete-namespace to remove it)."
fi

echo ""
echo "========================================"
echo "  Cleanup complete for instance: ${INSTANCE_ID}"
echo "========================================"
echo ""
