#!/bin/bash
# ============================================================
# Shared config for deploying CUGA (event-driven layer, NO Activepieces) to
# IBM Code Engine. Mirrors the routing team's CE conventions so everything lands
# in the SAME account/region/project/registry you already use — the same place the
# cuga-apps-mcp-* tool servers already run.
#
# This is an ADDITIONAL, admin-only path. It never touches local dev (`make up*`),
# never starts Activepieces, and creates exactly ONE Code Engine app.
#
# Override anything via env, e.g.:  CPU=1 MEMORY=4G ./2_deploy.sh
# ============================================================

# ---- Account / region / project (reused from the routing account) ----------
export REGION="${REGION:-us-east}"
export RESOURCE_GROUP_NAME="${RESOURCE_GROUP_NAME:-routing}"
export CE_PROJECT_NAME="${CE_PROJECT_NAME:-ce-project-routing}"

# ---- Container registry (global icr.io — us-east has no regional endpoint) ---
export REGISTRY_HOST="${REGISTRY_HOST:-icr.io}"
export REGISTRY_NAMESPACE="${REGISTRY_NAMESPACE:-routing_namespace}"
export REGISTRY_SECRET_NAME="${REGISTRY_SECRET_NAME:-icr-secret-1}"
export IMAGE_REPO="${REGISTRY_HOST}/${REGISTRY_NAMESPACE}/cuga-events"
export IMAGE_REF="${IMAGE_REF:-${IMAGE_REPO}:latest}"

# ---- The ONE Code Engine app -----------------------------------------------
export APP_NAME="${APP_NAME:-cuga-events}"
export APP_PORT="${APP_PORT:-7860}"                 # CUGA's native port; the CE route maps to it
export CPU="${CPU:-2}"
export MEMORY="${MEMORY:-8G}"
export EPHEMERAL="${EPHEMERAL:-4G}"

# SINGLE always-warm instance — REQUIRED for correctness, not a cost tweak:
#   • Telegram long-poll + Discord Gateway + the native cron/poll scheduler are
#     persistent SINGLE-OWNER loops. max-scale>1 double-processes every event.
#   • scale-to-zero (min-scale 0) kills those loops and drops events.
export MIN_SCALE="${MIN_SCALE:-1}"
export MAX_SCALE="${MAX_SCALE:-1}"
export REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-600}"    # long agent runs; CE max is 600s

# ---- The events (no-AP) runtime knobs (non-secret; secrets come via .env.ce) -
# native scheduler + direct channel backends = ZERO Activepieces.
export EVENTS_SCHEDULER="${EVENTS_SCHEDULER:-native}"
export EVENTS_TELEGRAM_BACKEND="${EVENTS_TELEGRAM_BACKEND:-direct}"
export EVENTS_DISCORD_BACKEND="${EVENTS_DISCORD_BACKEND:-direct}"
export EVENTS_SLACK_BACKEND="${EVENTS_SLACK_BACKEND:-direct}"
# The registry inside the container points at the already-deployed remote MCP servers.
export MCP_SERVERS_FILE_IN_IMAGE="${MCP_SERVERS_FILE_IN_IMAGE:-/app/src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml}"

# Agent model: the supervisor + its roster. DEFAULTS TO ON, deliberately.
#
# This used to default to "" (classic single generalist), and that default was a trap: a plain
# ./2_deploy.sh produced a CUGA whose /run/agents reported ONE agent, so every fired flow ran as the
# bare default agent with no sub-agents and no scoped tools. Nothing errors — the answers are just
# quietly worse, and the only visible symptom is a preflight line reading "agent fleet seeded —
# 1 agents". The events layer always targets the single agent "cuga" and lets the supervisor route
# internally, so shipping without the roster is never what you want here.
#
# Set CE_EVENTS_SUPERVISOR=0 explicitly if you really want the classic generalist.
export CE_EVENTS_SUPERVISOR="${CE_EVENTS_SUPERVISOR:-1}"     # "0" = classic, "1" = supervisor
export CE_ROSTER="${CE_ROSTER:-events/examples/rosters/default.yaml}"   # the 8-agent no-AP roster

# ---- Secrets: pulled from a gitignored .env.ce into a CE secret -------------
export SECRET_NAME="${SECRET_NAME:-cuga-events-secrets}"
export CE_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
export APP_ROOT="$( cd -- "$CE_DIR/../.." &> /dev/null && pwd )"
export ENV_CE_FILE="${ENV_CE_FILE:-$CE_DIR/.env.ce}"
export URLS_ENV_FILE="${URLS_ENV_FILE:-$CE_DIR/.ce_urls.env}"

# ---- Helpers ---------------------------------------------------------------
function require_login() {
  if ! ibmcloud target -o json 2>/dev/null | python3 -c "import sys,json;json.load(sys.stdin)" >/dev/null 2>&1; then
    echo "Not logged in. Run:  ibmcloud login --sso   (pick region $REGION), then re-run."; exit 1
  fi
}

function ce_target() {
  ibmcloud target -r "$REGION" -g "$RESOURCE_GROUP_NAME" >/dev/null
  ibmcloud ce project select --name "$CE_PROJECT_NAME" >/dev/null
}

# Admin gate: this path creates cloud resources. Require an explicit opt-in.
# Set YES=1 (or pass -y) to skip the interactive confirm (for automation).
function admin_guard() {
  if [[ "${CUGA_CE_ADMIN:-}" != "1" ]]; then
    echo "Refusing to run: this is the admin-only Code Engine path."
    echo "Set CUGA_CE_ADMIN=1 to proceed, e.g.:  CUGA_CE_ADMIN=1 $0"
    exit 1
  fi
  if [[ "${1:-}" == "-y" || "${YES:-}" == "1" ]]; then return 0; fi
  echo "About to act on:"
  echo "   region=$REGION  group=$RESOURCE_GROUP_NAME  project=$CE_PROJECT_NAME"
  echo "   registry=$IMAGE_REPO"
  echo "   app=$APP_NAME  scale=${MIN_SCALE}-${MAX_SCALE}  cpu=$CPU mem=$MEMORY"
  read -r -p "Proceed? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "Aborted."; exit 1; }
}

# Does a Code Engine secret define KEY? Used to decide whether the events DB is PostgreSQL (DSN
# supplied via the secret, because it carries a password) or the SQLite+COS fallback.
#
# ⚠ `ibmcloud ce secret get` PRINTS EVERY VALUE, base64-encoded — it is not a listing command.
# An earlier version of this comment claimed the opposite ("prints key NAMES but never values, so
# this leaks nothing"), which is exactly the sort of thing someone reads before pasting the command
# into a shared terminal or a bug report. Base64 is not encryption; that output is the secret.
# So: the pipeline below is safe only because stdout goes to `grep -q` and is never displayed.
# Do NOT run `ibmcloud ce secret get` by hand to check whether a key exists — use this function,
# or `--output json` piped through a key-name extractor.
secret_has_key() {
  local secret="$1" key="$2"
  ibmcloud ce secret get --name "$secret" 2>/dev/null | grep -qE "^[[:space:]]*${key}([[:space:]]|:|$)"
}

# Print only the KEY NAMES in a Code Engine secret — the safe counterpart to the warning above.
secret_key_names() {
  ibmcloud ce secret get --name "$1" --output json 2>/dev/null \
    | python3 -c 'import sys,json;print("\n".join(sorted((json.load(sys.stdin).get("data") or {}).keys())))'
}
