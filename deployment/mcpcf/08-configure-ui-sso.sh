#!/usr/bin/env bash
set -euo pipefail

# Give Forge's own landing page an "IBM Verify" login button, so admins sign in
# as themselves instead of sharing the platform-admin password.
#
# This is a DIFFERENT mechanism from 07-configure-user-identity.sh. That one
# registers account-iam with trusted_for_api_auth, which validates bearers on
# API calls and never drives an interactive login. A login page needs the
# authorization-code flow, and account-iam cannot serve it — its discovery
# document advertises only a jwks_uri:
#
#   IVIA         authorization_endpoint yes  token yes  userinfo yes  jwks yes
#   account-iam  authorization_endpoint NO   token NO   userinfo NO   jwks yes
#
# So the interactive provider is IVIA. The two coexist: different issuers, and
# resolve_trusted_provider_by_issuer keys its map on the issuer, so registering
# this one cannot clobber account-iam. (Two providers sharing ONE issuer would —
# the map is a plain dict and the last row silently wins.)
#
# The client is created by ACCOUNT-IAM's dynamic client registration API, NOT by
# IVIA's /register. IVIA's own DCR endpoint is gated and rejects everything we can
# reach with ("No bearer token"); account-iam is the platform's front door for it,
# and it is the same path the CUGA operator uses to register a client per agent.
# Two non-obvious details, both learned the hard way:
#
#   1. The endpoint is  POST /api/2.0/<isvTenantType>/clients  (isvTenantType=apps
#      here). Its body is a CUSTOM camelCase shape, not RFC 7591 — exactly three
#      fields are accepted: clientName, redirectUris, grantTypes. responseTypes,
#      scope and tokenEndpointAuthMethod are rejected as "not supported".
#
#   2. The credential in secret cuga-operator/ibm-verify-credentials
#      (IBM_VERIFY_ACCESS_TOKEN) is an API KEY, not a bearer. Sent directly as
#      Authorization: Bearer it returns 401 "Auth validation error". It must first
#      be exchanged:  POST /api/2.0/apikeys/token {"apikey": <key>} -> {token}.  # pragma: allowlist secret
#      The resulting JWT is the bearer for the /clients call.
#
# So by default this script needs nothing from anyone: it reads that secret off
# the cluster, does the exchange, and registers the client itself. Override only
# to bypass account-iam entirely:
#   IVIA_CLIENT_ID + IVIA_CLIENT_SECRET  - a client someone already registered
#                                          with the exact redirect URI below.
#
# NOTE the borrowed credential is the PLATFORM operator's, not one issued to this
# PoC: the client we create is indistinguishable from an operator-made one except
# by clientName, and it breaks if the platform rotates that key. For anything past
# a PoC, ask for a credential of your own.
#
# Usage:
#   ./08-configure-ui-sso.sh [path/to/forge.env]                    # self-service
#   IVIA_CLIENT_ID=... IVIA_CLIENT_SECRET=... ./08-configure-ui-sso.sh [forge.env]  # pragma: allowlist secret
#   ./08-configure-ui-sso.sh --delete [path/to/forge.env]           # deregister
#
# KUBECONFIG must point at the SPOKE (agent cluster) — Forge lives there, and the
# registration runs from inside the Forge pod on purpose: it proves the exact
# process that will later exchange tokens can reach and TRUST account-iam (the
# hub's Vault CA must be in the bundle behind SSL_CERT_FILE — see 07-/deployment).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DELETE=false
if [[ "${1:-}" == "--delete" ]]; then DELETE=true; shift; fi
ENV_FILE="${1:-${SCRIPT_DIR}/forge.env}"
[[ -f "$ENV_FILE" ]] || { echo "ERROR: env file not found: $ENV_FILE"; exit 1; }
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

oc whoami &>/dev/null || { echo "ERROR: not logged in (KUBECONFIG -> spoke)"; exit 1; }

# The provider id is chosen BEFORE registration, not after: Forge's callback route
# is /auth/sso/callback/{provider_id}, so the id is baked into the redirect URI we
# hand to IVIA. Changing it later means re-registering the client.
: "${UI_PROVIDER_ID:=ivia-apps}"
: "${HUB_DOMAIN:?set HUB_DOMAIN in forge.env (apps domain of the hub cluster)}"
: "${IVIA_ISSUER:=https://ivia-apps-wrp.apps.${HUB_DOMAIN}/iviaop/oauth2}"

# Which IVIA group grants membership of the workspace team. IVIA emits directory
# groups; it does NOT emit the platform's ServiceOwner/ServiceAdmin/ServiceUser —
# those come from account-iam, which is a different issuer on a different path.
# Leave unset and UI logins authenticate but land in no team, which shows up as an
# empty console rather than an error. See the warning at the end.
: "${IVIA_TEAM_GROUP:=}"

CRED_FILE="${SCRIPT_DIR}/.ivia-client.json"
STATE="${SCRIPT_DIR}/.mcpcf-state.json"

FP=$(oc get pods -n "$NAMESPACE" -l app=forge --field-selector=status.phase=Running \
      -o jsonpath='{.items[0].metadata.name}')
[[ -n "$FP" ]] || { echo "ERROR: no running forge pod in $NAMESPACE"; exit 1; }
FH=$(oc get route forge -n "$NAMESPACE" -o jsonpath='{.spec.host}')
REDIRECT_URI="https://${FH}/auth/sso/callback/${UI_PROVIDER_ID}"

echo "==> Forge        : https://$FH"
echo "==> provider id  : $UI_PROVIDER_ID"
echo "==> redirect URI : $REDIRECT_URI"

admin_token() {
  curl -sk -X POST "https://$FH/auth/email/login" -H "Content-Type: application/json" \
    -d "{\"email\":\"$PLATFORM_ADMIN_EMAIL\",\"password\":\"$PLATFORM_ADMIN_PASSWORD\"}" --max-time 20 \
    | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])"
}

# ---------------------------------------------------------------------------
# Teardown. DCR hands back a registration_access_token + registration_client_uri
# precisely so the client can be removed without an administrator; keep them.
# ---------------------------------------------------------------------------
if [[ "$DELETE" == true ]]; then
  AT=$(admin_token)
  echo "==> Removing the Forge SSO provider"
  curl -sk -o /dev/null -w "    DELETE provider -> %{http_code}\n" -X DELETE \
    "https://$FH/v1/auth/sso/admin/providers/${UI_PROVIDER_ID}" \
    -H "Authorization: Bearer $AT" --max-time 20 || true
  if [[ -f "$CRED_FILE" ]]; then
    # account-iam's DCR response carries no registration_client_uri /
    # registration_access_token (it is not RFC 7591), so there is nothing to
    # self-delete with. Print the client_id and let a human remove it rather
    # than pretending it was cleaned up.
    CID=$(python3 -c "import json;print(json.load(open('$CRED_FILE')).get('client_id',''))" 2>/dev/null || true)
    echo "==> The OAuth client itself is NOT removed by this script."
    echo "    account-iam's registration API returns no deregistration handle."
    echo "    Ask the platform team to delete client_id: ${CID:-<unknown>}"
    rm -f "$CRED_FILE"
    echo "    removed local $CRED_FILE"
  else
    echo "    no $CRED_FILE — nothing local to clean up"
  fi
  echo "Done."
  exit 0
fi

# ---------------------------------------------------------------------------
# 1. Get the OAuth client, then attach IVIA discovery to it.
#
# The registration runs INSIDE the Forge pod, not from this laptop. That is not
# incidental: the hub's routes are signed by an in-cluster Vault CA that is never
# sent on the wire, so trust depends on the bundle behind SSL_CERT_FILE. Running
# here proves the exact process that will later exchange tokens can reach and
# TRUST account-iam. A curl from a workstation (or any curl -sk) proves neither,
# and quietly passed for an hour while every token was being rejected.
#
# $CRED_FILE holds, once obtained: {client_id, client_secret, _discovery{...}}.
# ---------------------------------------------------------------------------
: "${IAM_BASE:=https://account-iam.apps.${HUB_DOMAIN}}"
: "${IAM_TENANT_TYPE:=apps}"   # the <isvTenantType> in /api/2.0/<type>/clients
ATTACH_DISCOVERY='
import json, sys, httpx
issuer = sys.argv[1]
d = httpx.Client(timeout=25).get(issuer.rstrip("/") + "/.well-known/openid-configuration").json()
c = json.load(sys.stdin)
c["_discovery"] = {k: d.get(k) for k in
    ("issuer","authorization_endpoint","token_endpoint","userinfo_endpoint","jwks_uri")}
print(json.dumps(c))
'

if [[ -f "$CRED_FILE" ]]; then
  echo
  echo "==> Reusing the client in $CRED_FILE (delete that file to re-register)"
elif [[ -n "${IVIA_CLIENT_ID:-}" && -n "${IVIA_CLIENT_SECRET:-}" ]]; then  # pragma: allowlist secret
  echo
  echo "==> Using the pre-registered client from IVIA_CLIENT_ID/IVIA_CLIENT_SECRET"
  echo "    (its redirect URI must be exactly: $REDIRECT_URI)"
  TMP=$(mktemp)
  # Built on one line so the allowlist pragma can sit on it — the literals are
  # JSON key names, the values come from the environment.
  CRED_JSON=$(printf '{"client_id":"%s","client_secret":"%s"}' "$IVIA_CLIENT_ID" "$IVIA_CLIENT_SECRET")  # pragma: allowlist secret
  printf '%s' "$CRED_JSON" \
    | oc exec -n "$NAMESPACE" "$FP" -i -- python3 -c "$ATTACH_DISCOVERY" "$IVIA_ISSUER" > "$TMP"
  unset CRED_JSON
  mv "$TMP" "$CRED_FILE"; chmod 600 "$CRED_FILE"
  echo "    saved -> $CRED_FILE"
else
  # Self-service: register through account-iam's DCR API using the operator's
  # API key. Two steps, both from inside the pod (stdin carries the key; argv
  # carries only non-secret values):
  #   apikey  -> POST /api/2.0/apikeys/token {"apikey": key}   -> bearer JWT  # pragma: allowlist secret
  #   bearer  -> POST /api/2.0/<type>/clients {clientName,redirectUris,grantTypes}
  echo
  echo "==> Reading the operator's IVIA API key (cuga-operator/ibm-verify-credentials)"
  IVK=$(oc get secret ibm-verify-credentials -n cuga-operator \
        -o jsonpath='{.data.IBM_VERIFY_ACCESS_TOKEN}' 2>/dev/null | base64 -d 2>/dev/null || true)
  if [[ -z "$IVK" ]]; then
    echo "ERROR: could not read IBM_VERIFY_ACCESS_TOKEN, and no IVIA_CLIENT_ID/SECRET given."
    echo "       Either grant access to that secret, or pass a pre-registered client:"
    echo "         IVIA_CLIENT_ID=... IVIA_CLIENT_SECRET=... $0 $ENV_FILE"  # pragma: allowlist secret
    echo "       (redirect URI must be exactly: $REDIRECT_URI)"
    exit 1
  fi
  echo "==> Registering the client via account-iam DCR ($IAM_BASE)"
  echo "    NOTE: borrows the PLATFORM operator's key — fine for a PoC, see header."
  TMP=$(mktemp)
  set +e
  printf '%s' "$IVK" | oc exec -n "$NAMESPACE" "$FP" -i -- \
    env IAM_BASE="$IAM_BASE" TENANT="$IAM_TENANT_TYPE" REDIRECT="$REDIRECT_URI" python3 -c '
import os, sys, json, httpx
key = sys.stdin.read().strip()
base, tenant, redirect = os.environ["IAM_BASE"], os.environ["TENANT"], os.environ["REDIRECT"]
c = httpx.Client(timeout=25, follow_redirects=True)
# 1. exchange the API key for a bearer
tr = c.post(base + "/api/2.0/apikeys/token", json={"apikey": key})  # pragma: allowlist secret
if tr.status_code != 200:
    print(json.dumps({"error": f"apikey exchange HTTP {tr.status_code}", "body": tr.text[:300]})); raise SystemExit(1)
tok = tr.json().get("token")
if not tok:
    print(json.dumps({"error": "apikey exchange returned no token", "body": tr.text[:300]})); raise SystemExit(1)
# 2. register — exactly these three fields; the API rejects any others as "not supported"
r = c.post(f"{base}/api/2.0/{tenant}/clients",
           headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
           json={"clientName": "MCP Context Forge",
                 "redirectUris": [redirect],
                 "grantTypes": ["authorization_code", "client_credentials", "refresh_token"]})
if r.status_code not in (200, 201):
    print(json.dumps({"error": f"client registration HTTP {r.status_code}", "body": r.text[:300]})); raise SystemExit(1)
d = r.json()
# account-iam returns camelCase; normalise to the snake_case the rest of the script reads.
print(json.dumps({"client_id": d.get("clientId") or d.get("client_id"),
                  "client_secret": d.get("clientSecret") or d.get("client_secret"),
                  "_account_iam_client": True}))
' > "$TMP"
  RC=$?
  set -e
  if [[ $RC -ne 0 ]]; then
    echo "    FAILED:"; python3 -c "
import json
try:
    d=json.load(open('$TMP')); print('     ', d.get('error'), d.get('body',''))
except Exception:
    print('      (no JSON response)')
"
    rm -f "$TMP"; exit 1
  fi
  # Attach discovery in a second pass so a discovery hiccup can't strand a created client.
  oc exec -n "$NAMESPACE" "$FP" -i -- python3 -c "$ATTACH_DISCOVERY" "$IVIA_ISSUER" < "$TMP" > "${TMP}.d" \
    && mv "${TMP}.d" "$TMP"
  mv "$TMP" "$CRED_FILE"; chmod 600 "$CRED_FILE"
  python3 -c "
import json
d = json.load(open('$CRED_FILE'))
print('    client_id     :', d.get('client_id'))
print('    client_secret :', 'yes' if d.get('client_secret') else 'NONE (Forge needs a confidential client)')
"
  echo "    saved -> $CRED_FILE  (holds a client secret; not for git)"
fi

# ---------------------------------------------------------------------------
# 2. Register the provider in Forge.
#
# trusted_for_api_auth stays FALSE here. This provider exists to log a human in
# through the browser; it must not also make IVIA access tokens acceptable as API
# bearers. That is account-iam's job, and it is audience-bound to one agent
# instance — an unbound second API-trusted issuer would widen the door well past
# what this is for.
# ---------------------------------------------------------------------------
TEAM_ID=$(python3 -c "import json;print(json.load(open('$STATE'))['team_id'])" 2>/dev/null || echo "")

BODY=$(python3 - "$CRED_FILE" "$UI_PROVIDER_ID" "$TEAM_ID" "$IVIA_TEAM_GROUP" <<'PY'
import json, sys
cred_file, pid, team, group = sys.argv[1:5]
c = json.load(open(cred_file))
d = c['_discovery']
body = {
    "id": pid,
    "name": pid,
    "display_name": "IBM Verify",
    "provider_type": "oidc",
    "client_id": c["client_id"],
    "client_secret": c.get("client_secret", ""),  # pragma: allowlist secret
    "authorization_url": d["authorization_endpoint"],
    "token_url": d["token_endpoint"],
    "userinfo_url": d["userinfo_endpoint"],
    "issuer": d["issuer"],
    "jwks_uri": d["jwks_uri"],
    # email is not cosmetic: Forge keys users by email (get_user_by_email), so it
    # is what makes a browser login and an API call resolve to the SAME user
    # rather than creating a second account for the same person.
    "scope": "openid profile email",
    "auto_create_users": True,
    "trusted_for_api_auth": False,
    "team_mapping": ({group: {"team_id": team, "role": "member"}} if (group and team) else {}),
    "provider_metadata": {"groups_claim": "groups"},
}
print(json.dumps(body))
PY
)

AT=$(admin_token)
echo
echo "==> Registering provider '$UI_PROVIDER_ID' in Forge"
CODE=$(curl -sk -o /dev/null -w '%{http_code}' -X GET \
  "https://$FH/v1/auth/sso/admin/providers/${UI_PROVIDER_ID}" \
  -H "Authorization: Bearer $AT" --max-time 20)
if [[ "$CODE" == "200" ]]; then M=PUT; U="https://$FH/v1/auth/sso/admin/providers/${UI_PROVIDER_ID}"
else M=POST; U="https://$FH/v1/auth/sso/admin/providers"; fi
curl -sk -o /tmp/.uisso.json -w "    $M -> %{http_code}\n" -X "$M" "$U" \
  -H "Authorization: Bearer $AT" -H "Content-Type: application/json" \
  -d "$BODY" --max-time 25
python3 -c "
import json
d = json.load(open('/tmp/.uisso.json'))
print('    response:', json.dumps(d)[:240] if 'id' not in d else 'ok: ' + d['id'])
"
rm -f /tmp/.uisso.json

# ---------------------------------------------------------------------------
# 3. Validate. A stored row proves nothing: the login endpoint is what actually
# builds the authorize URL, and it is where a bad client_id or a redirect URI
# IVIA does not recognise will surface.
# ---------------------------------------------------------------------------
echo
echo "==> Does the login endpoint build an authorize URL?"
# redirect_uri is a REQUIRED query param on this endpoint; without it Forge
# answers 422 and the check looks like a config failure when it is not.
curl -sk "https://$FH/auth/sso/login/${UI_PROVIDER_ID}?redirect_uri=$(
    python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$REDIRECT_URI"
  )" --max-time 20 \
  | python3 -c "
import json, sys
from urllib.parse import urlparse, parse_qs
try:
    d = json.load(sys.stdin)
except Exception:
    print('    no JSON back — is SSO_ENABLED=true?'); raise SystemExit(1)
url = d.get('authorization_url') or d.get('auth_url') or ''
if not url:
    print('    no authorization_url in response:', json.dumps(d)[:220]); raise SystemExit(1)
q = parse_qs(urlparse(url).query)
print('    host         :', urlparse(url).netloc)
print('    client_id    :', (q.get('client_id') or ['-'])[0])
print('    redirect_uri :', (q.get('redirect_uri') or ['-'])[0])
print('    PKCE         :', 'yes' if q.get('code_challenge') else 'no')
"

echo
echo "Sign in at:  https://$FH/admin   (the IBM Verify button)"
if [[ -z "$IVIA_TEAM_GROUP" || -z "$TEAM_ID" ]]; then
  echo
  echo "WARNING: no team mapping configured for this provider."
  echo "  A UI login will authenticate and provision the user, then show an EMPTY"
  echo "  console — no error, just nothing, because the account belongs to no team."
  echo "  IVIA emits directory 'groups'; it does NOT emit the platform's"
  echo "  ServiceOwner/ServiceAdmin/ServiceUser (those come from account-iam)."
  echo "  Log in once, then read the group values actually present:"
  echo "    oc exec -n $NAMESPACE deploy/forge-postgres -- \\"
  echo "      psql -U $POSTGRES_USER -d $POSTGRES_DB -tAc \\"
  echo "      \"select email, auth_provider from email_users order by email;\""
  echo "  then re-run with IVIA_TEAM_GROUP=<group> to map it onto the workspace team."
fi
