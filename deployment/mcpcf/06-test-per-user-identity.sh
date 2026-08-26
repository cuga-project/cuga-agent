#!/usr/bin/env bash
set -uo pipefail

# Does Forge actually support PER-USER identity, or only a shared workspace
# credential? Mints several broker tokens that differ only in identity/groups
# and checks what Forge does with each.
#
# Scope: this tests the FORGE side only. Whether CUGA can *obtain* a per-user
# token is the separate, still-open broker question (content.md §5) — the agent
# today holds one workspace credential in its pod env.
#
# Usage: ./06-test-per-user-identity.sh [path/to/forge.env]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${1:-${SCRIPT_DIR}/forge.env}"
[[ -f "$ENV_FILE" ]] || { echo "ERROR: env file not found: $ENV_FILE"; exit 1; }
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

oc whoami &>/dev/null || { echo "ERROR: not logged in (KUBECONFIG must point at the spoke)"; exit 1; }

FH=$(oc get route forge -n "$NAMESPACE" -o jsonpath='{.spec.host}')
BP=$(oc get pods -n "$NAMESPACE" -l app=mock-broker -o jsonpath='{.items[0].metadata.name}')
[[ -n "$FH" && -n "$BP" ]] || { echo "ERROR: forge route or mock-broker pod missing in $NAMESPACE"; exit 1; }

PGPOD=$(oc get pods -n "$NAMESPACE" -l app=forge-postgres -o jsonpath='{.items[0].metadata.name}')

# mint <email> <sub> <groups-json>
mint() {
  oc exec -n "$NAMESPACE" "$BP" -- uv run python -c "
import time, jwt
from cryptography.hazmat.primitives import serialization
k = serialization.load_pem_private_key(open('/etc/mock-broker/tls.key','rb').read(), password=None)  # pragma: allowlist secret
n = int(time.time())
print(jwt.encode({
    'iss': '$BROKER_ISSUER', 'aud': '$MCP_AUDIENCE',
    'sub': '$2', 'email': '$1', 'groups': $3,
    'iat': n, 'exp': n + 3600,
}, k, algorithm='RS256', headers={'kid': 'mock-broker-1'}))
" 2>/dev/null
}

tools_for() {  # -> count of demo tools visible to this token
  curl -sk --max-time 25 -H "Authorization: Bearer $1" "https://$FH/v1/tools" \
    | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    items = d if isinstance(d,list) else d.get('tools',d.get('data',[]))
    print(len([t for t in items if 'demo-mcp' in (t.get('name') or '')]))
except Exception:
    print('ERR')
"
}

sql() { oc exec -n "$NAMESPACE" "$PGPOD" -- bash -lc \
  "PGPASSWORD=\$POSTGRESQL_PASSWORD psql -tA -U \$POSTGRESQL_USER -d \$POSTGRESQL_DATABASE -c \"$1\"" 2>/dev/null; }  # pragma: allowlist secret

WS="[\"ws-${WORKSPACE_ID}\"]"
echo "=== Forge: https://$FH"
echo "=== workspace group: ws-${WORKSPACE_ID}"
echo

echo "--- 1. Two DIFFERENT users, same workspace group ---"
TA=$(mint alice@mcpcf-poc.example.com user-alice "$WS")
TB=$(mint bob@mcpcf-poc.example.com   user-bob   "$WS")
echo "  alice sees demo tools: $(tools_for "$TA")"
echo "  bob   sees demo tools: $(tools_for "$TB")"

echo
echo "--- 2. Did Forge provision them as SEPARATE users? ---"
sql "select email, is_admin, is_active from email_users order by email;" | sed 's/^/    /'

echo
echo "--- 3. Team membership per user (is it really group-derived?) ---"
sql "select u.email, t.name, m.role from email_team_members m
       join email_users u on u.email = m.user_email
       join email_teams t on t.id = m.team_id
      order by u.email;" | sed 's/^/    /'

echo
echo "--- 4. A user with NO workspace group ---"
TC=$(mint carol@mcpcf-poc.example.com user-carol "[]")
echo "  carol sees demo tools: $(tools_for "$TC")  (expect 0)"
echo "  carol provisioned anyway?"
sql "select email from email_users where email='carol@mcpcf-poc.example.com';" | sed 's/^/    /'

echo
echo "--- 5. Is a tool CALL attributed to the calling user? ---"
RPC='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"demo-mcp-poc-workspace-1-echo","arguments":{"text":"who-am-i"}}}'
for who in alice bob; do
  T=$([ "$who" = alice ] && echo "$TA" || echo "$TB")
  ok=$(curl -sk --max-time 25 -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
        -d "$RPC" "https://$FH/rpc" | grep -c "who-am-i")
  echo "  $who tool call succeeded: $([ "$ok" -gt 0 ] && echo yes || echo NO)"
done
echo "  distinct principals in Forge's audit/metrics for those calls:"
sql "select user_email, count(*) from tool_metrics group by user_email order by user_email;" 2>/dev/null | sed 's/^/    /' \
  || echo "    (tool_metrics not queryable — see note below)"

echo
echo "=== READ THIS ==="
echo "Rows 1-4 test whether FORGE can distinguish users and scope them by group."
echo "Row 5 tests whether a tool CALL carries that identity through."
echo "Neither tests whether CUGA can OBTAIN a per-user token — the agent holds one"
echo "workspace credential in pod env today. That is the open broker question."
