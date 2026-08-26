#!/usr/bin/env bash
set -uo pipefail
# (not -e: we want every assertion to run and report, not stop at the first failure)

# Runs poc-plan.md's six Track B assertions against the deployed stack.
# Usage: ./05-validate.sh [path/to/forge.env]

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

if ! oc whoami &>/dev/null; then
  echo "ERROR: not logged in — 'oc login' to the target cluster first (KUBECONFIG=...)"
  exit 1
fi

ROUTE_HOST=$(oc get route forge -n "$NAMESPACE" -o jsonpath='{.spec.host}')
FORGE_URL="https://$ROUTE_HOST"
FAIL=0
pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; FAIL=1; }

echo "==> Check 1: GET /health"
CODE=$(curl -s -k -o /dev/null -w '%{http_code}' --max-time 20 "$FORGE_URL/health")
[[ "$CODE" == "200" ]] && pass "got 200" || fail "got $CODE, expected 200"

echo "==> Minting a valid agent token (groups set)"
"$SCRIPT_DIR/04-mint-token.sh" "$ENV_FILE" >/dev/null
TOKEN=$(cat "$SCRIPT_DIR/.mcpcf-token")

echo "==> Check 2: GET /v1/tools with the agent token — expect the demo server's tools"
BODY=$(curl -s -k --max-time 20 -H "Authorization: Bearer $TOKEN" "$FORGE_URL/v1/tools")
if echo "$BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); names=[t.get('name','') for t in (d if isinstance(d,list) else d.get('tools',[]))]; sys.exit(0 if any('echo' in n or 'add' in n for n in names) else 1)" 2>/dev/null; then
  pass "demo tools (echo/add) visible"
else
  fail "expected demo tools in response, got: $(echo "$BODY" | head -c 300)"
fi

echo "==> Check 3: GET /v1/tools with aud=model-gw — expect 401 (audience binding works)"
BADAUD_TOKEN=$(oc exec -n "$NAMESPACE" "$(oc get pods -n "$NAMESPACE" -l app=mock-broker -o jsonpath='{.items[0].metadata.name}')" -- uv run python -c "
import json, time
from cryptography.hazmat.primitives import serialization
import jwt
with open('/etc/mock-broker/tls.key', 'rb') as f:
    key = serialization.load_pem_private_key(f.read(), password=None)  # pragma: allowlist secret
now = int(time.time())
payload = {'iss': '$BROKER_ISSUER', 'aud': 'model-gw', 'sub': 'poc-agent', 'groups': [], 'iat': now, 'exp': now + 3600}
print(jwt.encode(payload, key, algorithm='RS256', headers={'kid': 'mock-broker-1'}))
")
CODE=$(curl -s -k -o /dev/null -w '%{http_code}' --max-time 20 -H "Authorization: Bearer $BADAUD_TOKEN" "$FORGE_URL/v1/tools")
[[ "$CODE" == "401" ]] && pass "got 401" || fail "got $CODE, expected 401"

echo "==> Check 4: GET /v1/tools with no token — expect 401, not a public-only list"
CODE=$(curl -s -k -o /dev/null -w '%{http_code}' --max-time 20 "$FORGE_URL/v1/tools")
[[ "$CODE" == "401" ]] && pass "got 401" || fail "got $CODE, expected 401"

echo "==> Check 5: POST /rpc tools/call — expect the demo tool's result"
RPC_BODY='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"echo","arguments":{"text":"mcpcf-poc"}}}'
BODY=$(curl -s -k --max-time 20 -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$RPC_BODY" "$FORGE_URL/rpc")
if echo "$BODY" | grep -q "mcpcf-poc"; then
  pass "demo tool result contains our input"
else
  fail "unexpected /rpc response: $(echo "$BODY" | head -c 300)"
fi

echo "==> Check 6: drop the group, repeat check 2 — expect denied (proves Option A, not a static grant)"
"$SCRIPT_DIR/04-mint-token.sh" "$ENV_FILE" --no-group >/dev/null
NOGROUP_TOKEN=$(cat "$SCRIPT_DIR/.mcpcf-token")
BODY=$(curl -s -k --max-time 20 -H "Authorization: Bearer $NOGROUP_TOKEN" "$FORGE_URL/v1/tools")
NAMES_COUNT=$(echo "$BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len([t for t in (d if isinstance(d,list) else d.get('tools',[])) if 'echo' in t.get('name','') or 'add' in t.get('name','')]))" 2>/dev/null || echo "parse-error")
if [[ "$NAMES_COUNT" == "0" ]]; then
  pass "demo tools no longer visible without the group claim"
else
  fail "demo tools still visible with no group claim ($NAMES_COUNT found) — team_mapping isn't gating access"
fi

echo
if [[ "$FAIL" == "0" ]]; then
  echo "All checks passed."
else
  echo "One or more checks FAILED — see above."
fi
exit "$FAIL"
