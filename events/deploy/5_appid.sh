#!/bin/bash
# ============================================================
# Provision IBM App ID and wire it up as CUGA's OIDC login provider.
#
#   CUGA_CE_ADMIN=1 ./5_appid.sh            # create or reuse, then configure
#   CUGA_CE_ADMIN=1 RECREATE_APP=1 ./5_appid.sh   # force a NEW client id/secret
#
# WHY THIS IS A SEPARATE, RARELY-RUN SCRIPT
# -----------------------------------------
# Unlike 1_build_push_image.sh / 2_deploy.sh, this is NOT part of a normal deploy. The App ID
# instance and its registered application are long-lived: the client id and secret are baked into
# .env and flow to Code Engine through make_env_ce.sh. Re-running the registration mints a NEW
# secret and invalidates the old one, so a redeploy must NOT do it implicitly.
#
# So this script is IDEMPOTENT BY DEFAULT: an existing instance is reused, and an existing
# application of the same name is reused with its current credentials. You only get new
# credentials by asking for them with RECREATE_APP=1.
#
# WHAT IT CONFIGURES, AND WHY EACH PIECE MATTERS
# ----------------------------------------------
#   1. The instance                — Lite plan (free). graduated-tier if you need volume.
#   2. An application              — yields OIDC_CLIENT_ID / OIDC_CLIENT_SECRET / discovery URL.
#   3. Redirect URIs               — MUST match OIDC_REDIRECT_URI exactly or App ID refuses the
#                                    callback. See the note on the redirect target below.
#   4. IdP lockdown                — THE IMPORTANT ONE. A fresh App ID instance ships with Cloud
#                                    Directory self-service SIGNUP ENABLED, which means anyone on
#                                    the internet can register an account and then log in to CUGA.
#                                    Turning authentication on without this step is worse than
#                                    leaving it off: it looks secured and is not. We disable signup
#                                    and self-service, and deactivate the social providers that are
#                                    "active" with empty config.
#   5. Writes the four OIDC keys into .env, which is where make_env_ce.sh's CORE_ONLY list reads
#      them from. They land in cuga-core's secret only — the events service has no session
#      awareness and authenticates its callers with X-Gateway-Token.
#
# THE REDIRECT TARGET IS A FRONTEND PAGE, NOT THE CALLBACK ROUTE
# --------------------------------------------------------------
# It is tempting to point OIDC_REDIRECT_URI at /auth/callback. That is wrong and fails at runtime.
# CUGA's `POST /auth/callback` reads a JSON body, so it is called by the FRONTEND, not by the IdP:
# App ID redirects the browser (GET) to a React route, App.tsx picks `code`/`state` off the query
# string, and posts them as JSON. So the redirect URI must be a page the SPA serves — /manage.
#
# AFTER THIS SCRIPT
# -----------------
#   ./make_env_ce.sh && CUGA_CE_ADMIN=1 YES=1 ./2_deploy.sh
# 2_deploy.sh gates DYNACONF_AUTH__ENABLED on OIDC_CLIENT_ID actually being in the core secret, so
# it will tell you plainly whether login is on.
#
# ONE MANUAL STEP REMAINS: creating the user you log in as. Signup is disabled by design (see 4),
# so accounts are admin-created:
#   IBM Cloud > Resource list > <instance> > Cloud Directory > Users > Add user
# ============================================================
set -euo pipefail
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "$SCRIPT_DIR/config.sh"

APPID_NAME="${APPID_NAME:-cuga-appid}"
APPID_PLAN="${APPID_PLAN:-lite}"          # lite = free. graduated-tier = paid, higher limits.
APPID_REGION="${APPID_REGION:-$REGION}"
APPID_APP_NAME="${APPID_APP_NAME:-cuga-core}"
ENV_FILE="${ENV_FILE:-$APP_ROOT/.env}"
RECREATE_APP="${RECREATE_APP:-0}"

require_login
admin_guard "${1:-}"

# ---- 1. the instance (reused if it already exists) -------------------------
if ibmcloud resource service-instance "$APPID_NAME" >/dev/null 2>&1; then
  echo "== reusing existing App ID instance '$APPID_NAME' =="
else
  echo "== creating App ID instance '$APPID_NAME' ($APPID_PLAN, $APPID_REGION) =="
  ibmcloud resource service-instance-create \
    "$APPID_NAME" appid "$APPID_PLAN" "$APPID_REGION" -g "$RESOURCE_GROUP_NAME" >/dev/null
fi

TENANT=$(ibmcloud resource service-instance "$APPID_NAME" --output json \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print((d[0] if isinstance(d,list) else d)["guid"])')
[ -n "$TENANT" ] || { echo "could not resolve the App ID tenant (instance GUID)"; exit 1; }
MGMT="https://${APPID_REGION}.appid.cloud.ibm.com/management/v4/${TENANT}"
echo "   tenant: $TENANT"

# The IAM token is a credential: captured into a variable, never echoed.
TOKEN=$(ibmcloud iam oauth-tokens --output json | python3 -c 'import sys,json;print(json.load(sys.stdin)["iam_token"])')
[ -n "$TOKEN" ] || { echo "could not obtain an IAM token"; exit 1; }
api() { curl -s -H "Authorization: $TOKEN" -H "Content-Type: application/json" "$@"; }

# ---- 2. where the browser comes back to ------------------------------------
# Prefer the live cuga-core route; fall back to the URL 2_deploy.sh recorded. A stale or guessed
# host here is the single most common cause of "redirect_uri mismatch" at the IdP.
CORE_URL="${CORE_URL:-}"
if [ -z "$CORE_URL" ]; then
  ce_target >/dev/null 2>&1 || true
  CORE_URL=$(ibmcloud ce app get --name "$CORE_APP" --output url 2>/dev/null || true)
fi
if [ -z "$CORE_URL" ] && [ -f "$URLS_ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$URLS_ENV_FILE"; CORE_URL="${CUGA_CE_CORE_URL:-}"
fi
[ -n "$CORE_URL" ] || { echo "could not determine the cuga-core URL — deploy it first, or set CORE_URL="; exit 1; }
CORE_URL="${CORE_URL%/}"
REDIRECT="$CORE_URL/manage"
echo "   redirect: $REDIRECT"

# ---- 3. the application (reused unless RECREATE_APP=1) ---------------------
APP_JSON="$(mktemp)"; trap 'rm -f "$APP_JSON"' EXIT
EXISTING=$(api "$MGMT/applications" | python3 -c "
import sys, json
apps = (json.load(sys.stdin) or {}).get('applications') or []
print(next((a['clientId'] for a in apps if a.get('name') == '$APPID_APP_NAME'), ''))
" 2>/dev/null || true)

if [ -n "$EXISTING" ] && [ "$RECREATE_APP" != "1" ]; then
  echo "== reusing registered application '$APPID_APP_NAME' =="
  echo "   (RECREATE_APP=1 to mint a NEW client secret — invalidates the current one)"
  api "$MGMT/applications/$EXISTING" > "$APP_JSON"
else
  [ -n "$EXISTING" ] && echo "== RECREATE_APP=1: registering a NEW application (old secret dies) =="
  echo "== registering application '$APPID_APP_NAME' =="
  api -X POST "$MGMT/applications" \
    --data "$(python3 -c "import json;print(json.dumps({'name':'$APPID_APP_NAME','type':'regularwebapp'}))")" \
    > "$APP_JSON"
fi
python3 -c "
import json,sys
d = json.load(open('$APP_JSON'))
if 'clientId' not in d:
    sys.exit('App ID did not return an application: %s' % str(d)[:200])
"

# ---- 4. redirect URIs ------------------------------------------------------
# localhost is included so you can exercise the login flow before touching the deployment.
echo "== setting redirect URIs =="
api -X PUT "$MGMT/config/redirect_uris" --data "$(python3 - "$CORE_URL" <<'PY'
import json, sys
base = sys.argv[1]
print(json.dumps({"redirectUris": [f"{base}/manage", f"{base}/chat", "http://localhost:7860/manage"]}))
PY
)" -o /dev/null -w "   HTTP %{http_code}\n"

# ---- 5. LOCK DOWN THE IDENTITY PROVIDERS -----------------------------------
# Read the header note. Skipping this is how you end up with an "authenticated" deployment that
# any stranger can sign up for.
echo "== locking down identity providers =="
api -X PUT "$MGMT/config/idps/cloud_directory" --data '{
  "isActive": true,
  "config": {
    "selfServiceEnabled": false,
    "signupEnabled": false,
    "interactions": {
      "identityConfirmation": {"accessMode": "FULL", "methods": ["email"]},
      "welcomeEnabled": false,
      "resetPasswordEnabled": true,
      "resetPasswordNotificationEnable": false
    }
  }}' -o /dev/null -w "   cloud_directory (signup off): HTTP %{http_code}\n"
for idp in facebook google; do
  api -X PUT "$MGMT/config/idps/$idp" --data '{"isActive": false}' \
    -o /dev/null -w "   $idp disabled: HTTP %{http_code}\n"
done

# ---- 5b. ANONYMOUS ACCESS — the second way in that is on by default --------
# Disabling signup is not enough. A fresh instance also has `anonymousAccess.enabled: true`, and
# that is a SEPARATE, CREDENTIAL-FREE login path: a plain GET to
#   /oauth/v4/<tenant>/authorization?client_id=…&response_type=code&idp=appid_anon&redirect_uri=…
# returns 302 with a real authorization code, no account and no password. Reproduced against this
# instance before it was turned off. Anyone who can reach the CUGA login page can append
# `&idp=appid_anon` to the authorization URL they were redirected to — they hold a state and PKCE
# challenge CUGA itself issued, so the callback validates and they land inside with no account.
# With authorization off (the current posture) that is full access.
# Verified off: the same request now redirects with "Anonymous token is disabled".
echo "== disabling anonymous access =="
api -X PUT "$MGMT/config/tokens" --data '{
  "access": {"expires_in": 3600},
  "refresh": {"enabled": false, "expires_in": 2592000},
  "anonymousAccess": {"enabled": false},
  "accessTokenClaims": [{"source": "roles", "destinationClaim": "roles"}],
  "idTokenClaims": [{"source": "roles", "destinationClaim": "roles"}]
}' -o /dev/null -w "   HTTP %{http_code}\n"
# The claim mapping above is what makes DYNACONF_AUTH__AUTHORIZATION_ENABLED usable at all:
# jwt_validator._extract_roles reads a top-level `roles` claim, and App ID emits none by default.
# Mapping it costs nothing while authorization is off and is the prerequisite for turning it on.
# STILL REQUIRED before you flip that flag: define roles named ServiceOwner / ServiceAdmin /
# ServiceUser in App ID and assign them, or every authenticated user gets 403 on Manage.
api "$MGMT/config/tokens" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print("   anonymousAccess:", (d.get("anonymousAccess") or {}).get("enabled"))
print("   roles claim mapped:", bool(d.get("idTokenClaims")))'

echo "   active providers now:"
api "$MGMT/config/idps" | python3 -c '
import sys, json
for i in json.load(sys.stdin)["idps"]:
    if i.get("isActive"):
        c = i.get("config") or {}
        extra = ""
        if i["idpName"] == "cloud_directory":
            extra = "  signup=%s selfService=%s" % (c.get("signupEnabled"), c.get("selfServiceEnabled"))
        print("     %s%s" % (i["idpName"], extra))'

# ---- 6. write the four keys into .env --------------------------------------
# make_env_ce.sh's CORE_ONLY list reads them from here and puts them in cuga-core's secret ONLY.
# .env is also what a local `cuga start demo` reads, so one home serves both.
echo "== updating $ENV_FILE =="
python3 - "$APP_JSON" "$ENV_FILE" "$REDIRECT" <<'PY'
import json, os, re, sys
app, env_path, redirect = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
vals = {
    "OIDC_CLIENT_ID": app["clientId"],
    "OIDC_CLIENT_SECRET": app["secret"],
    "OIDC_DISCOVERY_URL": app["discoveryEndpoint"],
    "OIDC_REDIRECT_URI": redirect,
}
lines = open(env_path).read().splitlines() if os.path.exists(env_path) else []
out, seen = [], set()
for line in lines:
    m = re.match(r"^(OIDC_[A-Z_]+)=", line)
    if m and m.group(1) in vals:
        out.append(f"{m.group(1)}={vals[m.group(1)]}"); seen.add(m.group(1))
    else:
        out.append(line)
missing = [k for k in vals if k not in seen]
if missing:
    out += ["", "# ---- IBM App ID (OIDC) — written by events/deploy/5_appid.sh ----"]
    out += [f"{k}={vals[k]}" for k in missing]
open(env_path, "w").write("\n".join(out) + "\n")
print("   OIDC_CLIENT_ID     =", vals["OIDC_CLIENT_ID"])
print("   OIDC_CLIENT_SECRET = (written, not printed)")
print("   OIDC_DISCOVERY_URL =", vals["OIDC_DISCOVERY_URL"])
print("   OIDC_REDIRECT_URI  =", vals["OIDC_REDIRECT_URI"])
print("   updated in place:", sorted(seen) or "none")
print("   appended        :", missing or "none")
PY

cat <<EOF

===================================================
 App ID is configured.

 STILL MANUAL — create the account you will log in as (signup is disabled on purpose):
   IBM Cloud > Resource list > $APPID_NAME > Cloud Directory > Users > Add user

 Then push it to Code Engine:
   ./make_env_ce.sh
   CUGA_CE_ADMIN=1 YES=1 ./2_deploy.sh

 Look for:  "auth: OIDC login ENABLED"
 Verify  :  curl -s $CORE_URL/api/auth/config     -> {"enabled":true,...}
            curl -s -o /dev/null -w '%{http_code}\\n' -X POST \\
              $CORE_URL/api/events/admin/users    -> 401
===================================================
EOF
