#!/usr/bin/env bash
# Reproducible local launcher for the memory-compliance PoC.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"
RUN="${CUGA_COMPLIANCE_POC_RUN_DIR:-/tmp/cuga_compliance_poc}"
CUGA_PORT="${EVENTS_CUGA_PORT:-7860}"
EVOLVE_PORT="${EVOLVE_PORT:-8201}"
mkdir -p "$RUN"

load_environment() {
  local primary
  primary="$(git worktree list --porcelain | awk '/^worktree / {print substr($0, 10); exit}')"
  set -a
  [ -f "$primary/.env" ] && . "$primary/.env"
  [ "$primary" = "$REPO" ] || [ ! -f "$REPO/.env" ] || . "$REPO/.env"
  [ -z "${CUGA_POC_ENV_FILE:-}" ] || . "$CUGA_POC_ENV_FILE"
  set +a

  export EVENTS_ENABLED=1
  export EVENTS_DB="${CUGA_COMPLIANCE_POC_EVENTS_DB:-${EVENTS_DB:-$REPO/events.db}}"
  export CUGA_COMPLIANCE_POC_SEED_ENABLED=1
  export DYNACONF_EVOLVE__ENABLED=true
  export DYNACONF_EVOLVE__MODE=direct
  export DYNACONF_EVOLVE__URL="${DYNACONF_EVOLVE__URL:-http://127.0.0.1:$EVOLVE_PORT/sse}"
}

evolve_ready() {
  local response
  response="$(curl -sN --max-time 2 "http://127.0.0.1:$EVOLVE_PORT/sse" 2>/dev/null || true)"
  grep -q "event: endpoint" <<<"$response"
}

cuga_compliance_ready() {
  curl -fsS --max-time 3 \
    "http://127.0.0.1:$CUGA_PORT/api/admin/memory/automation" 2>/dev/null \
    | python3 -c '
import json, sys
data = json.load(sys.stdin)
raise SystemExit(0 if data.get("scheduler_health") == "healthy" else 1)
' 2>/dev/null
}

status() {
  printf "Activepieces  "
  curl -fsS --max-time 3 "${AP_BASE_URL:-http://127.0.0.1:8081}/api/v1/flags" \
    >/dev/null 2>&1 && echo "ready" || echo "down"
  printf "Evolve MCP    "
  evolve_ready && echo "ready" || echo "down"
  printf "CUGA PoC      "
  curl -fsS --max-time 3 "http://127.0.0.1:$CUGA_PORT/api/admin/memory/automation" \
    >/dev/null 2>&1 && echo "ready" || echo "down"
  printf "PII filtering "
  cuga_compliance_ready && echo "ready" || echo "disabled"
}

stop() {
  for name in cuga evolve; do
    if [ -f "$RUN/$name.pid" ]; then
      kill "$(cat "$RUN/$name.pid")" 2>/dev/null || true
      rm -f "$RUN/$name.pid"
    fi
  done
}

case "${1:-}" in
  --status)
    load_environment
    status
    exit 0
    ;;
  --stop)
    stop
    exit 0
    ;;
esac

load_environment

if [ -z "${GATEWAY_TOKEN:-}" ]; then
  echo "Missing GATEWAY_TOKEN. Set it in .env or CUGA_POC_ENV_FILE." >&2
  exit 1
fi
if [ -z "${OPENAI_API_KEY:-}${GROQ_API_KEY:-}${WATSONX_APIKEY:-}" ]; then
  echo "Missing an LLM credential. Set one in .env or CUGA_POC_ENV_FILE." >&2
  exit 1
fi

if ! curl -fsS --max-time 3 "${AP_BASE_URL:-http://127.0.0.1:8081}/api/v1/flags" \
  >/dev/null 2>&1; then
  echo "Starting Activepieces..."
  scripts/ap_up.sh
fi

if ! evolve_ready; then
  EVOLVE_REPO="${EVOLVE_REPO:-}"
  if [ -z "$EVOLVE_REPO" ]; then
    for candidate in \
      "$(dirname "$REPO")/kaizen.codex-mcp-compliance" \
      "$(dirname "$REPO")/kaizen"; do
      if [ -f "$candidate/altk_evolve/frontend/mcp/mcp_server.py" ] \
        && grep -q "def run_retention" "$candidate/altk_evolve/frontend/mcp/mcp_server.py"; then
        EVOLVE_REPO="$candidate"
        break
      fi
    done
  fi
  if [ -z "$EVOLVE_REPO" ]; then
    echo "No compliance-capable Evolve checkout found. Set EVOLVE_REPO." >&2
    exit 1
  fi
  if [ -z "${EVOLVE_HOOKS_CONFIG:-}" ] \
    && [ -f "$EVOLVE_REPO/examples/cuga_compliance_poc_hooks.yaml" ]; then
    export EVOLVE_HOOKS_CONFIG="$EVOLVE_REPO/examples/cuga_compliance_poc_hooks.yaml"
  fi
  echo "Starting Evolve MCP from $EVOLVE_REPO..."
  nohup uv run --project "$EVOLVE_REPO" evolve-mcp \
    --transport sse --host 127.0.0.1 --port "$EVOLVE_PORT" \
    >"$RUN/evolve.log" 2>&1 &
  echo $! >"$RUN/evolve.pid"
  for _ in $(seq 1 30); do
    evolve_ready && break
    kill -0 "$(cat "$RUN/evolve.pid")" 2>/dev/null || {
      tail -30 "$RUN/evolve.log"
      exit 1
    }
    sleep 1
  done
fi

if curl -fsS --max-time 2 "http://127.0.0.1:$CUGA_PORT/health" >/dev/null 2>&1; then
  echo "Port $CUGA_PORT already has a CUGA server. Stop it before starting the PoC." >&2
  exit 1
fi

echo "Starting CUGA compliance PoC..."
nohup uv run uvicorn cuga.backend.server.main:app \
  --host 127.0.0.1 --port "$CUGA_PORT" >"$RUN/cuga.log" 2>&1 &
echo $! >"$RUN/cuga.pid"

for _ in $(seq 1 120); do
  if cuga_compliance_ready; then
    echo "Compliance PoC ready: http://127.0.0.1:$CUGA_PORT/chat"
    echo "Event Studio:         http://127.0.0.1:$CUGA_PORT/studio"
    exit 0
  fi
  kill -0 "$(cat "$RUN/cuga.pid")" 2>/dev/null || {
    tail -50 "$RUN/cuga.log"
    exit 1
  }
  sleep 2
done

echo "CUGA started, but end-to-end retention health did not become ready." >&2
tail -50 "$RUN/cuga.log"
exit 1
