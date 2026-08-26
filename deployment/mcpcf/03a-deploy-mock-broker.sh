#!/usr/bin/env bash
set -euo pipefail

# Deploy the PoC-only mock OIDC broker (poc-plan.md Track B, BROKER_MODE=mock)
# and fill BROKER_ISSUER/BROKER_JWKS_URI into forge.env if they're empty.
# Skipped entirely when BROKER_MODE=real — see forge.env's BROKER_MODE.
#
# Usage: ./03a-deploy-mock-broker.sh [path/to/forge.env]

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

if [[ "${BROKER_MODE:-mock}" != "mock" ]]; then
  echo "BROKER_MODE=$BROKER_MODE — nothing to do, this script only deploys the mock broker."
  exit 0
fi

if [[ -z "${NAMESPACE:-}" || -z "${CUGA_IMAGE:-}" || -z "${CLUSTER_DOMAIN:-}" ]]; then
  echo "ERROR: NAMESPACE, CUGA_IMAGE and CLUSTER_DOMAIN must be set in $ENV_FILE"
  exit 1
fi

if ! oc whoami &>/dev/null; then
  echo "ERROR: not logged in — 'oc login' to the target cluster first (KUBECONFIG=...)"
  exit 1
fi

# Default the mock broker's own issuer/jwks_uri to its Route — NOT the
# in-cluster Service DNS. Forge's SSRF defense hard-requires jwks_uri to be
# https (confirmed live: an http in-cluster jwks_uri gets rejected with
# "jwks_uri ... does not match issuer origin ...; rejecting (SSRF defense)"
# — the check is `scheme != "https" OR netloc mismatch`, so same-origin http
# still fails on the scheme half alone). The Route gives us edge TLS with no
# need to hand-roll certs in the broker itself.
: "${BROKER_ISSUER:=https://mock-broker-${NAMESPACE}.apps.${CLUSTER_DOMAIN}}"
: "${BROKER_JWKS_URI:=${BROKER_ISSUER}/jwks}"
export BROKER_ISSUER BROKER_JWKS_URI

if ! grep -q "^BROKER_ISSUER=.\+" "$ENV_FILE"; then
  sed -i.bak "s#^BROKER_ISSUER=.*#BROKER_ISSUER=$BROKER_ISSUER#" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
  echo "==> Set BROKER_ISSUER=$BROKER_ISSUER in $ENV_FILE"
fi
if ! grep -q "^BROKER_JWKS_URI=.\+" "$ENV_FILE"; then
  sed -i.bak "s#^BROKER_JWKS_URI=.*#BROKER_JWKS_URI=$BROKER_JWKS_URI#" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
  echo "==> Set BROKER_JWKS_URI=$BROKER_JWKS_URI in $ENV_FILE"
fi

render() { envsubst < "$1"; }

echo "==> RSA keypair for the mock broker (idempotent — kept across re-runs)"
if oc get secret mock-broker-key -n "$NAMESPACE" &>/dev/null; then
  echo "    mock-broker-key already exists, reusing it"
else
  KEY_DIR=$(mktemp -d)
  trap 'rm -rf "$KEY_DIR"' EXIT
  openssl genrsa -out "$KEY_DIR/tls.key" 2048 2>/dev/null
  oc create secret generic mock-broker-key -n "$NAMESPACE" --from-file="$KEY_DIR/tls.key"
fi

echo "==> Mock broker config + deployment + route"
render "$SCRIPT_DIR/manifests/70-mock-broker/configmap.yaml" | oc apply -f -
render "$SCRIPT_DIR/manifests/70-mock-broker/deployment.yaml" | oc apply -f -
render "$SCRIPT_DIR/manifests/70-mock-broker/route.yaml" | oc apply -f -

echo "==> Waiting for rollout"
oc rollout status deployment/mock-broker -n "$NAMESPACE" --timeout=120s

echo "==> Verifying the route serves https jwks (Forge's SSRF check requires it)"
if ! curl -s -k --max-time 15 "${BROKER_JWKS_URI}" | grep -q '"keys"'; then
  echo "WARNING: ${BROKER_JWKS_URI} did not return a JWKS — check the route/pod before running 03-configure-forge.sh"
fi

echo "Done. Next: ./03-configure-forge.sh $ENV_FILE"
