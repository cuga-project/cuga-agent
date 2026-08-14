#!/bin/bash
# ============================================================
# Step 2 — Deploy CUGA + the eventing service. TWO apps, ONE image.
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
# This is THE deployment. The old "combined" mode (events mounted onto CUGA's FastAPI app) is
# gone: the eventing layer is always its own service.
#
#   ./2_deploy.sh                 # deploy both
#   CUGA_CE_ADMIN=1 YES=1 ./2_deploy.sh
#
# Known limits: single instance each (the scheduler and channel loops are process-wide singletons,
# so min=max=1).
#
# DURABLE STATE. The container filesystem is ephemeral, and it is worse than "survives a restart
# but not a revision replace" — the platform can replace the instance at any time (node drain,
# reschedule) with NO restart recorded, and the new pod starts with an empty disk. On 2026-08-05 a
# cron armed from Slack at 11:12 was gone when a new pod started at 11:24.
#
# The fix: set EVENTS_STATE_STORE to a Code Engine persistent data store (see 3_state_store.sh,
# which provisions one idempotently). This script then mounts it and points EVENTS_DB_BACKUP at it.
# The live SQLite DB deliberately stays on LOCAL disk — CE data stores are COS-backed, and SQLite
# on object storage corrupts — so the service snapshots to the mount instead (db_persist.py).
# Leave EVENTS_STATE_STORE unset and you get the old ephemeral behaviour, with a loud warning.
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
  # No EVENTS_ENABLED: this is plain CUGA. /run mounts because GATEWAY_TOKEN rides in via the
  # secret (unconfigured, it is not mounted at all), and every call must carry that token.
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
  --env "DEPLOY_REV=$DEPLOY_REV"
  # The Studio UI is served by cuga-core and calls this service cross-origin — allow it.
  --env "EVENTS_CORS_ORIGINS=$CORE_URL"
  --command "uv" --argument "run" --argument "python" --argument "-m"
  --argument "cuga.backend.events.service"
)

# ---- THE EVENTS DATABASE ----------------------------------------------------
# PREFERRED: PostgreSQL, the same engine local dev runs (`make pg`). Durability is then a property
# of the database — no mount, no snapshot loop, and a pod replace is a non-event. The DSN carries a
# password, so it lives in the Code Engine SECRET ($SECRET_NAME), not in a literal --env. Provision
# with ./4_postgres.sh, which creates the instance, reads the credentials and writes them there.
#
# FALLBACK: SQLite on the local disk plus COS snapshots (EVENTS_STATE_STORE). Kept because it needs
# no paid database, but it is single-writer and the snapshot is whole-file, so it does not scale.
STATE_MOUNT="${EVENTS_STATE_MOUNT:-/mnt/state}"
if secret_has_key "$SECRET_NAME" EVENTS_DB; then
  echo "== events DB: PostgreSQL (DSN from secret '$SECRET_NAME') — no mount, no snapshots =="
  # EVENTS_DB arrives via --env-from-secret; setting a literal here would shadow it.
elif [[ -n "${EVENTS_STATE_STORE:-}" ]]; then
  ev_args+=( --env "EVENTS_DB=/app/.cuga/events.db" )
  if ibmcloud ce pds get --name "$EVENTS_STATE_STORE" >/dev/null 2>&1; then
    ev_args+=(
      --mount-data-store "${STATE_MOUNT}=${EVENTS_STATE_STORE}"
      --env "EVENTS_DB_BACKUP=${STATE_MOUNT}/events.db"
    )
    echo "== durable state: ON — snapshots to ${STATE_MOUNT}/events.db (store '$EVENTS_STATE_STORE') =="
  else
    echo "!! EVENTS_STATE_STORE='$EVENTS_STATE_STORE' does not exist in this project."
    echo "!! Create it with ./3_state_store.sh, or unset it to deploy without durable state."
    exit 1
  fi
else
  ev_args+=( --env "EVENTS_DB=/app/.cuga/events.db" )
  echo "!! WARNING: no events database configured — armed flows will be LOST when Code Engine"
  echo "!!          replaces the instance (which happens with NO restart recorded)."
  echo "!!          Preferred fix:  ./4_postgres.sh          (managed PostgreSQL, same as local)"
  echo "!!          Cheap fallback: ./3_state_store.sh && EVENTS_STATE_STORE=cuga-events-state ./2_deploy.sh"
fi

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
  echo "# Generated by events/deploy/2_deploy.sh"
  echo "export CUGA_CE_CORE_URL=\"$CORE_URL\""
  echo "export CUGA_CE_URL=\"$EVENTS_URL\""      # the events front door — what the harness targets
} > "${SCRIPT_DIR}/.ce_urls.env"

# ---- POST-DEPLOY ASSERTIONS -------------------------------------------------
# Both of these have shipped broken before, and neither raises an error at runtime — they just make
# every answer quietly worse. Check them here, while the operator is still watching.
echo
echo "== post-deploy checks =="
_gw=$(grep -E '^GATEWAY_TOKEN=' "${SCRIPT_DIR}/.env.ce" 2>/dev/null | cut -d= -f2- | cut -d' ' -f1)

# 1. The roster. /run/agents is the authority (the roster belongs to whoever EXECUTES). One agent
#    means CUGA_SUPERVISOR_ROSTER never reached cuga-core and every fire runs the bare default.
_n=$(curl -s -m 90 -H "X-Gateway-Token: ${_gw}" "$CORE_URL/run/agents" 2>/dev/null \
     | python3 -c 'import sys,json;print(len((json.load(sys.stdin) or {}).get("agents",[])))' 2>/dev/null || echo 0)
if [[ "${_n:-0}" -gt 1 ]]; then
  echo "  ✓ roster: $_n agents on cuga-core"
else
  echo "  ✗ roster: $_n agent(s) — the supervisor roster did NOT load."
  echo "    Every fired flow will run as the bare default agent with no sub-agents or scoped tools."
  echo "    Fix: CE_EVENTS_SUPERVISOR=1 CE_ROSTER=events/examples/rosters/default.yaml ./2_deploy.sh"
fi

# 2. Durability. "durable: false" means an instance replace silently deletes every armed flow.
#    RETRY: the app was created seconds ago and may still be booting. A one-shot check reported
#    "?|?" — indistinguishable from a real failure — and a check that cries wolf gets ignored,
#    which defeats the point of having it.
_d="?|?"
for _i in $(seq 1 12); do
  _d=$(curl -s -m 30 -H "X-Gateway-Token: ${_gw}" "$EVENTS_URL/api/events/status" 2>/dev/null \
       | python3 -c 'import sys,json;d=(json.load(sys.stdin) or {}).get("durability",{});print(f"{d.get(\"durable\")}|{d.get(\"backend\",\"?\")}")' 2>/dev/null || echo "?|?")
  [[ "$_d" != "?|?" ]] && break
  sleep 10
done
[[ "$_d" == "?|?" ]] && echo "  · events service did not answer /status within 2 min — check below"
case "$_d" in
  True\|postgres) echo "  ✓ durability: PostgreSQL — an instance replace is a non-event" ;;
  True\|sqlite)   echo "  ✓ durability: SQLite + snapshots (consider ./4_postgres.sh)" ;;
  *)              echo "  ✗ durability: $_d — ARMED FLOWS WILL BE LOST on an instance replace."
                  echo "    Fix: ./4_postgres.sh   (or ./3_state_store.sh for the COS fallback)" ;;
esac
unset _gw _n _d

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
