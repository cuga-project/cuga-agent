#!/usr/bin/env bash
set -euo pipefail

# Register the REAL identity issuer (account-iam) as a second trusted SSO
# provider in Forge, so a user's own login token authenticates them directly.
#
# Why this works without a broker — all verified on this cluster against a real
# login token (see scratchpad.md, "Three bugs, stacked"):
#   * account-iam already puts the role in the token, but as a DICT bucketed by
#     scope: roles: {"SERVICE": ["ServiceOwner"]} — not a flat list. Stock Forge
#     drops that shape silently (list/str only) and every team_mapping no-ops,
#     which surfaces as an empty catalog rather than an auth error. Needs the
#     _coerce_claim_to_group_names fix in mcpgateway/services/sso_service.py.
#   * aud is a LIST: ["SERVICE/<instance-id>", "crn:v1:...<instance-id>::"].
#     Forge needs one of those verbatim — see the IAM_API_AUDIENCE block below.
#   * Forge can reach AND trust the issuer's JWKS (checked with httpx, no -k).
#
# So Option A's group->team mapping binds to `roles`, and the values are exactly
# the ServiceOwner/ServiceAdmin/ServiceUser the design already specified.
#
# Usage: ./07-configure-user-identity.sh <agent-instance-id> [path/to/forge.env]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTANCE_ID="${1:-}"
ENV_FILE="${2:-${SCRIPT_DIR}/forge.env}"
[[ -n "$INSTANCE_ID" ]] || { echo "Usage: $0 <agent-instance-id> [forge.env]"; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "ERROR: env file not found: $ENV_FILE"; exit 1; }
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

oc whoami &>/dev/null || { echo "ERROR: not logged in (KUBECONFIG -> spoke)"; exit 1; }

: "${IAM_ISSUER:=https://account-iam.apps.gori-agent-hub.cp.fyre.ibm.com/account-iam/api/2.0}"
: "${IAM_JWKS:=https://account-iam.apps.gori-agent-hub.cp.fyre.ibm.com/api/2.0/jwks}"

# The audience Forge validates against. This is NOT the bare instance id, even
# though the instance id is what the token is "bound to": account-iam issues
#   aud: ["SERVICE/<instance-id>", "crn:v1:...:<instance-id>::"]
# and Forge matches with PyJWT's jwt.decode(audience=...), which is an EXACT
# match against the entries. CUGA gets away with the bare id because
# auth/jwt_validator._assert_iam_token_bound_to_instance does a SUBSTRING match
# ("norm_id in entry.lower()"). Passing the bare id here yields a silent
# "Audience doesn't match" and every catalog call 401s.
#
# Prefer the CRN the platform already records on the CugaAgent CR.
if [[ -z "${IAM_API_AUDIENCE:-}" && -n "${HUB_KUBECONFIG:-}" ]]; then
  IAM_API_AUDIENCE=$(KUBECONFIG="$HUB_KUBECONFIG" oc get cugaagent "agent-${INSTANCE_ID}" \
    -n agent-service-broker -o jsonpath='{.metadata.annotations.cuga\.ibm\.com/crn}' 2>/dev/null || true)
  [[ -n "$IAM_API_AUDIENCE" ]] && echo "==> Derived api_audience from the CugaAgent CR annotation"
fi
if [[ -z "${IAM_API_AUDIENCE:-}" ]]; then
  echo "WARNING: falling back to the bare instance id as api_audience. Forge matches"
  echo "         aud EXACTLY, and account-iam tokens carry a CRN, so this will very"
  echo "         likely fail with \"Audience doesn't match\". Set IAM_API_AUDIENCE, or"
  echo "         export HUB_KUBECONFIG so the CRN can be read from the CugaAgent CR."
  IAM_API_AUDIENCE="$INSTANCE_ID"
fi

FH=$(oc get route forge -n "$NAMESPACE" -o jsonpath='{.spec.host}')
STATE="${SCRIPT_DIR}/.mcpcf-state.json"
[[ -f "$STATE" ]] || { echo "ERROR: $STATE missing — run ./03-configure-forge.sh first"; exit 1; }
TEAM_ID=$(python3 -c "import json;print(json.load(open('$STATE'))['team_id'])")

echo "==> Forge      : https://$FH"
echo "==> Issuer     : $IAM_ISSUER"
echo "==> JWKS       : $IAM_JWKS"
echo "==> api_audience: $IAM_API_AUDIENCE"
echo "==> team_id    : $TEAM_ID"

echo
echo "==> Can Forge actually reach that JWKS — and TRUST it?"
# Deliberately NOT `curl -sk`. -k skips verification, so it proves routing and
# says nothing about the trust chain: it stayed green while Forge was failing
# every token with "unable to get local issuer certificate". Two things matter:
#   * no -k, because SSL_CERT_FILE REPLACES the distro trust store rather than
#     adding to it, so a bundle missing one cluster's CA breaks that issuer only;
#   * httpx, because that is what PyJWT actually fetches JWKS with. curl and
#     httpx do not resolve trust from the same place.
FP=$(oc get pods -n "$NAMESPACE" -l app=forge -o jsonpath='{.items[0].metadata.name}')
CODE=$(oc exec -n "$NAMESPACE" "$FP" -- python3 -c "
import httpx, sys
try: print(httpx.Client(timeout=15).get(sys.argv[1]).status_code)
except Exception as e: print(f'FAIL {type(e).__name__}: {e}')
" "$IAM_JWKS" 2>/dev/null || echo "FAIL exec")
echo "    httpx -> $CODE"
if [[ "$CODE" != "200" ]]; then
  echo "    ERROR: Forge cannot verify the JWKS endpoint — it could never validate these tokens."
  echo "    If this is a cert-verify failure, the issuer's CA is missing from the bundle behind"
  echo "    SSL_CERT_FILE. Note that 'openssl s_client -showcerts' only yields what the server"
  echo "    sends: a reencrypt route with a private CA (e.g. Vault) transmits the leaf and never"
  echo "    the issuer. Take the CA from the cluster instead, e.g. on the hub:"
  echo "      oc get secret vault-root-ca-secret -n vault -o jsonpath='{.data.ca\\.crt}' | base64 -d"
  echo "    then confirm OFFLINE before deploying:  openssl verify -CAfile <bundle> <leaf>"
  exit 1
fi

ADMIN_TOKEN=$(curl -sk -X POST "https://$FH/auth/email/login" -H "Content-Type: application/json" \
  -d "{\"email\":\"$PLATFORM_ADMIN_EMAIL\",\"password\":\"$PLATFORM_ADMIN_PASSWORD\"}" --max-time 20 \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")

# Map the platform's real roles onto the workspace team. Every role lands in the
# same team — the role_mappings below are what differentiate their RBAC. This is
# Option A with `roles` as the group claim instead of `groups`.
BODY=$(python3 - "$IAM_ISSUER" "$IAM_JWKS" "$IAM_API_AUDIENCE" "$TEAM_ID" <<'PY'
import json, sys
iss, jwks, aud, team = sys.argv[1:5]
print(json.dumps({
    "id": "sovereign-account-iam",
    "name": "sovereign-account-iam",
    "display_name": "Sovereign account-iam (per-user)",
    "provider_type": "oidc",
    # Required by the schema but unused: trusted_for_api_auth validates bearers
    # directly against issuer+jwks_uri and never drives an interactive login.
    "client_id": "cuga-agent",
    "client_secret": "unused-trusted-for-api-auth-only",  # pragma: allowlist secret
    "authorization_url": f"{iss}/authorize",
    "token_url": f"{iss}/token",
    "userinfo_url": f"{iss}/userinfo",
    "issuer": iss,
    "jwks_uri": jwks,
    "trusted_for_api_auth": True,
    "api_audience": aud,
    "auto_create_users": True,
    "team_mapping": {
        r: {"team_id": team, "role": role}
        for r, role in (("ServiceOwner", "owner"), ("ServiceAdmin", "member"), ("ServiceUser", "member"))
    },
    "provider_metadata": {
        # account-iam emits "roles", not "groups"
        "groups_claim": "roles",
        "role_mappings": {
            "ServiceOwner": "team_admin",
            "ServiceAdmin": "developer",
            "ServiceUser": "viewer",
        },
    },
}))
PY
)

echo
echo "==> Registering provider 'sovereign-account-iam'"
CODE=$(curl -sk -o /tmp/.p.json -w '%{http_code}' -X GET "https://$FH/v1/auth/sso/admin/providers/sovereign-account-iam" \
        -H "Authorization: Bearer $ADMIN_TOKEN" --max-time 20)
if [[ "$CODE" == "200" ]]; then M=PUT; U="https://$FH/v1/auth/sso/admin/providers/sovereign-account-iam"
else M=POST; U="https://$FH/v1/auth/sso/admin/providers"; fi
curl -sk -o /tmp/.r.json -w "    $M -> %{http_code}\n" -X "$M" "$U" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d "$BODY" --max-time 25
python3 -c "
import json
d=json.load(open('/tmp/.r.json'))
if 'id' in d:
    print('    ok:', d['id'], '| groups_claim=', (d.get('provider_metadata') or {}).get('groups_claim'),
          '| api_audience=', d.get('api_audience'))
else:
    print('    response:', json.dumps(d)[:300])
"
rm -f /tmp/.p.json /tmp/.r.json

echo
echo "Done. Set the agent to token_source=user, then log in as a real user:"
echo "  DYNACONF_CONTEXT_FORGE__TOKEN_SOURCE=user"
echo "Forge will then attribute the catalog and tool calls to THAT user, not a shared account."
