#!/usr/bin/env bash
# events_up.sh — bring up the runtime services for the event-driven platform, one command.
# Starts: MCP registry (cuga-apps config) + 2 cloudflared tunnels (AP + CUGA) + CUGA server.
# Does NOT start Activepieces (your long-lived container) or create external accounts.
#   scripts/events_up.sh          # start
#   scripts/events_up.sh --stop   # stop
#   scripts/events_up.sh --status # show what's running + URLs
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
REGISTRY_PORT="${EVENTS_REGISTRY_PORT:-8001}"
CUGA_PORT="${EVENTS_CUGA_PORT:-8100}"
AP_PORT="$(grep -E '^AP_BASE_URL=' .env 2>/dev/null | sed -E 's|.*:([0-9]+).*|\1|' || echo 8081)"
CFG="src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml"
RUN=/tmp/events_up

stop() {
  echo "stopping events services…"
  for p in "$RUN"/*.pid; do [ -f "$p" ] && kill "$(cat "$p")" 2>/dev/null && rm -f "$p" || true; done
  pkill -f "uvicorn cuga.backend.tools_env.registry" 2>/dev/null || true
  pkill -f "events_up_server.py" 2>/dev/null || true
  pkill -f "cloudflared tunnel" 2>/dev/null || true
  echo "stopped."
}
[ "${1:-}" = "--stop" ] && { stop; exit 0; }

if [ "${1:-}" = "--status" ]; then
  echo "registry :$REGISTRY_PORT  $(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:$REGISTRY_PORT/applications || echo down)"
  echo "cuga     :$CUGA_PORT      $(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:$CUGA_PORT/api/events/status || echo down)"
  echo "AP tunnel:   $(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN"/ap_tunnel.log 2>/dev/null | tail -1 || echo none)"
  echo "CUGA tunnel: $(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN"/cuga_tunnel.log 2>/dev/null | tail -1 || echo none)"
  exit 0
fi

mkdir -p "$RUN"
echo "== prereqs =="
for c in uv cloudflared; do command -v $c >/dev/null || { echo "MISSING: $c (see events_docs/SETUP.md)"; exit 1; }; done
[ -d .venv ] || { echo "no .venv — running uv sync (minutes)…"; uv sync --python 3.12; }
curl -s -o /dev/null --max-time 4 "http://localhost:$AP_PORT/api/v1/flags" || \
  echo "WARN: Activepieces not reachable on :$AP_PORT — start your AP container (SETUP.md §2)."

echo "== MCP registry :$REGISTRY_PORT =="
MCP_SERVERS_FILE="$CFG" .venv/bin/python -m uvicorn \
  cuga.backend.tools_env.registry.registry.api_registry_server:app --host 127.0.0.1 --port "$REGISTRY_PORT" \
  > "$RUN/registry.log" 2>&1 & echo $! > "$RUN/registry.pid"

echo "== tunnels (cloudflared) =="
cloudflared tunnel --url "http://localhost:$AP_PORT" --no-autoupdate > "$RUN/ap_tunnel.log" 2>&1 & echo $! > "$RUN/ap_tunnel.pid"
cloudflared tunnel --url "http://localhost:$CUGA_PORT" --no-autoupdate > "$RUN/cuga_tunnel.log" 2>&1 & echo $! > "$RUN/cuga_tunnel.pid"

echo "== CUGA server :$CUGA_PORT =="
cat > "$RUN/events_up_server.py" <<PY
import os
repo = "$REPO"
for line in open(os.path.join(repo, ".env")):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line: continue
    k, v = line.split("=", 1); v = v.split(" #", 1)[0].strip().strip('"').strip("'")
    os.environ.setdefault(k.strip(), v)
os.environ["EVENTS_ENABLED"] = "1"
os.environ.setdefault("EVENTS_WORKER_BACKEND", "cuga")
os.environ.setdefault("EVENTS_SEED_AGENTS", "1")
os.environ.setdefault("EVENTS_USER_ID", "admin")   # web Studio browses as admin (matches telegram identity)
os.environ.setdefault("EVENTS_DB", os.path.join(os.getcwd(), ".events.db"))  # persist subs/identity across restarts
# arXiv/Semantic Scholar are ~5.5s/call from here; the papers agent does several calls + retries,
# which blows the 30s default sandbox timeout. 120s gives slow external APIs room to complete.
os.environ.setdefault("DYNACONF_ADVANCED_FEATURES__SANDBOX_EXECUTION_TIMEOUT", "120")
os.environ["DYNACONF_SERVER_PORTS__REGISTRY_HOST"] = "http://localhost:$REGISTRY_PORT"
import uvicorn
uvicorn.run("cuga.backend.server.main:app", host="127.0.0.1", port=$CUGA_PORT, log_level="warning")
PY
.venv/bin/python "$RUN/events_up_server.py" > "$RUN/cuga.log" 2>&1 & echo $! > "$RUN/cuga.pid"

echo "== waiting for CUGA (first boot is slow) =="
for i in $(seq 1 90); do
  curl -s --max-time 3 "http://localhost:$CUGA_PORT/api/events/status" >/dev/null 2>&1 && break
  kill -0 "$(cat "$RUN/cuga.pid")" 2>/dev/null || { echo "CUGA died — see $RUN/cuga.log"; tail -15 "$RUN/cuga.log"; exit 1; }
  sleep 4
done
sleep 3
echo ""
echo "READY:"
echo "  CUGA        http://localhost:$CUGA_PORT   (Studio: /manage → Studio)"
echo "  registry    http://localhost:$REGISTRY_PORT/applications"
echo "  AP tunnel   $(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN/ap_tunnel.log" | tail -1)   → for channel webhooks"
echo "  CUGA tunnel $(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN/cuga_tunnel.log" | tail -1)  → for OAuth callbacks (set EVENTS_PUBLIC_URL)"
echo ""
echo "logs in $RUN/*.log   ·   stop with: scripts/events_up.sh --stop"
