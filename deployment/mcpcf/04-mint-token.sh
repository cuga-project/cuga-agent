#!/usr/bin/env bash
set -euo pipefail

# Mint an agent bearer token asserting aud=$MCP_AUDIENCE and the workspace
# group claim, per Option A (poc-plan.md Track B). Two modes via forge.env's
# BROKER_MODE:
#   mock — signs locally (inside the mock-broker pod, which already holds
#          the RSA key Forge's SSO provider trusts) with PyJWT. PoC only.
#   real — not implemented. The design's open question (content.md §5) is
#          whether the real IAM proxy exchange can even emit aud+groups;
#          that needs answering with the broker team before this path can
#          be scripted, not guessed at here.
#
# Usage:
#   ./04-mint-token.sh [path/to/forge.env] [--no-group]
#   --no-group mints a token with an empty groups claim — for validate.sh
#   check 6 (drop the group, expect the previously-working call to be denied).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/forge.env"
NO_GROUP=false
for arg in "$@"; do
  if [[ "$arg" == "--no-group" ]]; then
    NO_GROUP=true
  else
    ENV_FILE="$arg"
  fi
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file not found: $ENV_FILE"
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

STATE_FILE="${SCRIPT_DIR}/.mcpcf-state.json"
if [[ ! -f "$STATE_FILE" ]]; then
  echo "ERROR: $STATE_FILE not found — run ./03-configure-forge.sh first."
  exit 1
fi
GROUP_VALUE=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['group_value'])")

if [[ "${BROKER_MODE:-mock}" != "mock" ]]; then
  echo "BROKER_MODE=${BROKER_MODE:-} — not implemented."
  echo "The open question (content.md §5) is whether the real IAM proxy"
  echo "exchange can emit aud=\$MCP_AUDIENCE and a group claim at all — that"
  echo "needs an answer from the broker team before this path is worth"
  echo "scripting. Use BROKER_MODE=mock to unblock the rest of the PoC."
  exit 1
fi

if ! oc whoami &>/dev/null; then
  echo "ERROR: not logged in — 'oc login' to the target cluster first (KUBECONFIG=...)"
  exit 1
fi

GROUPS_JSON="[]"
if [[ "$NO_GROUP" != true ]]; then
  GROUPS_JSON="[\"$GROUP_VALUE\"]"
fi

BROKER_POD=$(oc get pods -n "$NAMESPACE" -l app=mock-broker -o jsonpath='{.items[0].metadata.name}')
if [[ -z "$BROKER_POD" ]]; then
  echo "ERROR: no mock-broker pod found in $NAMESPACE — run ./03a-deploy-mock-broker.sh first."
  exit 1
fi

TOKEN=$(oc exec -n "$NAMESPACE" "$BROKER_POD" -- uv run python -c "
import json, time
from cryptography.hazmat.primitives import serialization
import jwt

with open('/etc/mock-broker/tls.key', 'rb') as f:
    key = serialization.load_pem_private_key(f.read(), password=None)  # pragma: allowlist secret

now = int(time.time())
payload = {
    'iss': '$BROKER_ISSUER',
    'aud': '$MCP_AUDIENCE',
    'sub': 'poc-agent-${WORKSPACE_ID}',
    # Required for JIT provisioning downstream (build_external_identity) —
    # a token with no email claim passes signature/issuer/audience
    # verification but then fails provisioning with reason=empty_email.
    # Confirmed live, and flagged as a known follow-on gap in upstream
    # issue #6396's own write-up.
    'email': 'poc-agent-${WORKSPACE_ID}@mcpcf-poc.example.com',
    'groups': $GROUPS_JSON,
    'iat': now,
    'exp': now + 3600,
}
print(jwt.encode(payload, key, algorithm='RS256', headers={'kid': 'mock-broker-1'}))
")

echo "$TOKEN" > "${SCRIPT_DIR}/.mcpcf-token"
echo "==> Token minted (groups=$GROUPS_JSON), written to .mcpcf-token"
echo "$TOKEN"
