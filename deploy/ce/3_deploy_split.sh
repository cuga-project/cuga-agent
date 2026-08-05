#!/bin/bash
# ============================================================
# Step 3 (OPTIONAL) — Deploy the SPLIT topology: two apps instead of one.
#
#   cuga-core     vanilla CUGA. Serves /stream, /run and the UI. EVENTS_ENABLED is NOT set,
#                 so it carries no triggers, no scheduler, no channel loops — and, deliberately,
#                 no channel secrets.
#   cuga-events   the eventing service. Owns triggers, the native scheduler, channels, the
#                 concierge and /invoke; executes agents by calling cuga-core's /run over HTTP.
#
# ONE IMAGE, two commands — the split is a different entrypoint, not a different build, so this
# reuses whatever ./1_build_push_image.sh already pushed. Nothing to rebuild.
#
# Combined mode (2_deploy_app.sh) remains the default and is unaffected; this is additive. Run
# either, not both, against the same app names.
#
#   ./3_deploy_split.sh            # deploy both
#   CUGA_CE_ADMIN=1 YES=1 ./3_deploy_split.sh
#
# Known limits (same as combined): single instance each (the scheduler and channel loops are
# process-wide singletons, so min=max=1), and the container filesystem is ephemeral — EVENTS_DB
# survives a restart but not a revision replace.
# ============================================================
set -euo pipefail
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "${SCRIPT_DIR}/config.sh"
admin_guard "${1:-}"
require_login
ce_target

CORE_APP="${CORE_APP:-cuga-core}"
EVENTS_APP="${EVENTS_APP:-cuga-events-svc}"
EVENTS_SVC_PORT="${EVENTS_SVC_PORT:-8100}"

if [[ ! -f "$ENV_CE_FILE" ]]; then
  echo "Missing $ENV_CE_FILE — generate it from your local .env first:  ./make_env_ce.sh"
  exit 1
fi

# ---- secret ----------------------------------------------------------------
# Both apps read the SAME secret today (it carries the LLM creds both need, plus the channel
# tokens only the events app uses). Splitting it so cuga-core never sees a bot token is the next
# tightening step; it needs make_env_ce.sh to emit two files.
echo "== syncing secret '$SECRET_NAME' =="
if ibmcloud ce secret get -n "$SECRET_NAME" >/dev/null 2>&1; then
  ibmcloud ce secret update --name "$SECRET_NAME" --from-env-file "$ENV_CE_FILE" >/dev/null
else
  ibmcloud ce secret create --name "$SECRET_NAME" --from-env-file "$ENV_CE_FILE" >/dev/null
fi

DEPLOY_REV="$(date +%s)"

# ---- 1. cuga-core — vanilla CUGA, no events --------------------------------
core_args=(
  --image "$IMAGE_REF"
  --registry-secret "$REGISTRY_SECRET_NAME"
  --port "$APP_PORT"
  --min-scale 1 --max-scale "${CORE_MAX_SCALE:-1}"
  --cpu "$CPU" --memory "$MEMORY" --ephemeral-storage "$EPHEMERAL"
  --request-timeout "$REQUEST_TIMEOUT"
  --env-from-secret "$SECRET_NAME"
  --env "MCP_SERVERS_FILE=$MCP_SERVERS_FILE_IN_IMAGE"
  --env "DEPLOY_REV=$DEPLOY_REV"
  # No EVENTS_ENABLED: this is plain CUGA. /run is always available and is what the events
  # service calls; it is guarded by GATEWAY_TOKEN, which rides in via the secret.
  --command "uv" --argument "run" --argument "cuga" --argument "start" --argument "demo"
)
# THE ROSTER LIVES WHERE EXECUTION LIVES. In the split, /run on THIS app is what actually runs the
# agent, so the supervisor and its sub-agents must be loaded HERE. Putting them on the events app
# (as the first cut did) does nothing: that app's runtime only forwards over HTTP, so every fire
# ran as cuga-core's bare default agent — answers came back "no tools available".
# The events layer never selects a sub-agent; it always targets the one agent, "cuga", and the
# supervisor routes internally. One agent or twenty-seven — the events side is unchanged.
if [[ "$CE_EVENTS_SUPERVISOR" == "1" ]]; then
  # CUGA_SUPERVISOR_ROSTER makes THIS server run as the supervisor: /run builds a CugaSupervisor
  # from the roster and routes internally. EVENTS_SUPERVISOR would be inert here — that flag is
  # read by the events layer's runtime, which this app deliberately does not have.
  core_args+=( --env "CUGA_SUPERVISOR_ROSTER=$CE_ROSTER" )
  echo "   roster on cuga-core (preloaded supervisor): $CE_ROSTER"
fi
if ibmcloud ce app get -n "$CORE_APP" >/dev/null 2>&1; then
  echo "Deleting existing app '$CORE_APP' for a clean redeploy ..."
  ibmcloud ce app delete --name "$CORE_APP" --force --wait --ignore-not-found
fi
echo "== creating app '$CORE_APP' (vanilla CUGA) =="
ibmcloud ce app create --name "$CORE_APP" "${core_args[@]}"
CORE_URL=$(ibmcloud ce app get --name "$CORE_APP" --output url)
echo "   cuga-core: $CORE_URL"

# ---- 2. cuga-events-svc — the eventing service -----------------------------
ev_args=(
  --image "$IMAGE_REF"
  --registry-secret "$REGISTRY_SECRET_NAME"
  --port "$EVENTS_SVC_PORT"
  --min-scale 1 --max-scale 1          # singleton: scheduler + channel loops are process-wide
  --cpu "${EVENTS_CPU:-1}" --memory "${EVENTS_MEMORY:-4G}" --ephemeral-storage "$EPHEMERAL"
  --request-timeout "$REQUEST_TIMEOUT"
  --env-from-secret "$SECRET_NAME"
  --env "EVENTS_SERVICE_PORT=$EVENTS_SVC_PORT"
  --env "EVENTS_HOST=0.0.0.0"
  --env "CUGA_URL=$CORE_URL"           # where the worker runs — resolved BEFORE this app exists
  --env "EVENTS_SCHEDULER=$EVENTS_SCHEDULER"
  --env "EVENTS_TELEGRAM_BACKEND=$EVENTS_TELEGRAM_BACKEND"
  --env "EVENTS_DISCORD_BACKEND=$EVENTS_DISCORD_BACKEND"
  --env "EVENTS_SLACK_BACKEND=$EVENTS_SLACK_BACKEND"
  --env "EVENTS_DISCORD_MEMBERS_INTENT=1"
  --env "EVENTS_DB=/app/.cuga/events.db"
  --env "DEPLOY_REV=$DEPLOY_REV"
  # The Studio UI is served by cuga-core and calls this service cross-origin — allow it.
  --env "EVENTS_CORS_ORIGINS=$CORE_URL"
  --command "uv" --argument "run" --argument "python" --argument "-m"
  --argument "cuga.backend.events.service"
)
# NB: no EVENTS_SUPERVISOR here — see the note on core_args. The roster belongs to cuga-core.
if ibmcloud ce app get -n "$EVENTS_APP" >/dev/null 2>&1; then
  echo "Deleting existing app '$EVENTS_APP' for a clean redeploy ..."
  ibmcloud ce app delete --name "$EVENTS_APP" --force --wait --ignore-not-found
fi
echo "== creating app '$EVENTS_APP' (eventing service → $CORE_URL) =="
ibmcloud ce app create --name "$EVENTS_APP" "${ev_args[@]}"
EVENTS_URL=$(ibmcloud ce app get --name "$EVENTS_APP" --output url)

# Self-URL for Slack callbacks / OAuth redirects belongs to the EVENTS app (it serves those routes).
echo "== setting EVENTS_PUBLIC_URL=$EVENTS_URL =="
ibmcloud ce app update --name "$EVENTS_APP" --env "EVENTS_PUBLIC_URL=$EVENTS_URL" >/dev/null

# Close the loop for the UI: cuga-core serves the SPA, so it must tell the SPA where the eventing
# API lives (it is read by /api/ui/config → api.ts getEventsBaseUrl). Done as a second revision
# because the events URL only exists once that app is created.
echo "== pointing the UI on $CORE_APP at the events API =="
ibmcloud ce app update --name "$CORE_APP" --env "EVENTS_API_URL=$EVENTS_URL" >/dev/null

{
  echo "# Generated by deploy/ce/3_deploy_split.sh"
  echo "export CUGA_CE_CORE_URL=\"$CORE_URL\""
  echo "export CUGA_CE_URL=\"$EVENTS_URL\""      # the events front door — what the harness targets
} > "${SCRIPT_DIR}/.ce_urls_split.env"

cat <<EOF

===================================================
 SPLIT topology is live:
   cuga-core   (vanilla CUGA, /stream + /run + UI):  $CORE_URL
   cuga-events (triggers · scheduler · channels):    $EVENTS_URL

 Point the harness at the EVENTS front door — the wire contract is unchanged:
   make test-e2e-ce CE_URL=$EVENTS_URL
   curl -s $EVENTS_URL/health

 Slack Event Subscriptions Request URL:
   $EVENTS_URL/api/events/slack/events
===================================================
EOF
