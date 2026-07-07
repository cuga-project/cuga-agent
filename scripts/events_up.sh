#!/usr/bin/env bash
# events_up.sh — bring up the runtime services for the event-driven platform, one command.
# Starts: MCP registry (cuga-apps config) + the CUGA cloudflared tunnel + CUGA server.
# (The AP tunnel is owned by ap_up.sh, which bakes it into AP as AP_FRONTEND_URL.)
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

# --- public-URL helpers ----------------------------------------------------
# The CUGA quick-tunnel URL IS EVENTS_PUBLIC_URL — the base for Slack's Request URL and every OAuth
# callback (Gmail/Box). It's random per run, so we (a) feed the live one into the server so the
# server always matches reality, and (b) print a clear "update these consoles" panel.
cuga_tunnel_url() { grep -ao 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN/cuga_tunnel.log" 2>/dev/null | tail -1; }
env_val() { grep -E "^$1=" "$REPO/.env" 2>/dev/null | tail -1 | cut -d= -f2- \
  | sed -e 's/ *#.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//'; }

# Export EVENTS_PUBLIC_URL from the live CUGA tunnel so the server matches it without editing .env —
# UNLESS .env pins a non-trycloudflare URL (a stable/named tunnel), which we respect as-is.
export_public_url() {
  local pin url; pin="$(env_val EVENTS_PUBLIC_URL)"; url="$(cuga_tunnel_url)"
  if [ -n "$pin" ] && ! printf '%s' "$pin" | grep -q trycloudflare; then
    export EVENTS_PUBLIC_URL="$pin"; return          # pinned stable URL — leave it alone
  fi
  [ -n "$url" ] && export EVENTS_PUBLIC_URL="$url"
}

# The "find the URL + update these" step the setup was missing.
print_public_url() {
  local url; url="${EVENTS_PUBLIC_URL:-$(cuga_tunnel_url)}"
  echo ""
  echo "──────────── PUBLIC URL  (EVENTS_PUBLIC_URL) ────────────"
  echo "  $url"
  echo "  ↑ the server is already using this — no need to edit .env."
  echo ""
  echo "  When this URL changes (restart / new tunnel), update these EXTERNAL consoles:"
  echo "    • Slack  → Event Subscriptions Request URL : $url/api/events/slack/events"
  echo "    • Gmail  → OAuth redirect URI (+ re-consent): $url/api/events/connect/gmail/callback"
  echo "  Nothing to do for: Box (direct poll), Discord (Gateway); Telegram → just 'make channels'."
  echo "─────────────────────────────────────────────────────────"
}

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
  echo "AP tunnel:   $(grep -ao 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN"/ap_tunnel.log 2>/dev/null | tail -1 || echo none)"
  echo "CUGA tunnel: $(grep -ao 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN"/cuga_tunnel.log 2>/dev/null | tail -1 || echo none)"
  exit 0
fi

# --public-url: print the current public URL + the exact strings to paste into Slack/Gmail.
if [ "${1:-}" = "--public-url" ]; then export_public_url; print_public_url; exit 0; fi

# --reload: bounce ONLY the CUGA server to pick up .env/code changes, KEEPING the registry and the
# two cloudflared tunnels alive — so the tunnel URLs (hence EVENTS_PUBLIC_URL and every external
# webhook/redirect wired to them) do NOT change. This is the cheap inner loop; use it instead of a
# full restart whenever you didn't touch AP or the tunnels.
if [ "${1:-}" = "--reload" ]; then
  [ -f "$RUN/events_up_server.py" ] || { echo "no prior run to reload — start it first: scripts/events_up.sh"; exit 1; }
  echo "reloading CUGA server (keeping registry + tunnels)…"
  [ -f "$RUN/cuga.pid" ] && kill "$(cat "$RUN/cuga.pid")" 2>/dev/null || true
  pkill -f "events_up_server.py" 2>/dev/null || true
  sleep 2
  export_public_url   # keep the server's EVENTS_PUBLIC_URL matched to the (unchanged) live tunnel
  .venv/bin/python "$RUN/events_up_server.py" > "$RUN/cuga.log" 2>&1 & echo $! > "$RUN/cuga.pid"
  for i in $(seq 1 30); do
    curl -s --max-time 3 "http://localhost:$CUGA_PORT/api/events/status" >/dev/null 2>&1 && break
    kill -0 "$(cat "$RUN/cuga.pid")" 2>/dev/null || { echo "CUGA died — see $RUN/cuga.log"; tail -15 "$RUN/cuga.log"; exit 1; }
    sleep 2
  done
  echo "reloaded. CUGA :$CUGA_PORT  ·  tunnels unchanged  ·  EVENTS_PUBLIC_URL still valid"
  print_public_url
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

echo "== CUGA tunnel (cloudflared) =="
# ONLY the CUGA (:8100) tunnel here. The AP (:8081) tunnel is owned by ap_up.sh, which bakes its URL
# into the AP container as AP_FRONTEND_URL — starting a second one here just clobbers ap_tunnel.log
# and leaves AP pointing at a different (often dead) URL, breaking Telegram/Slack-AP setWebhook.
cloudflared tunnel --url "http://localhost:$CUGA_PORT" --no-autoupdate > "$RUN/cuga_tunnel.log" 2>&1 & echo $! > "$RUN/cuga_tunnel.pid"

# capture the fresh CUGA tunnel URL and feed it to the server as EVENTS_PUBLIC_URL, so the server
# always matches the live tunnel (no stale-.env "one step behind" after a restart).
echo "== resolving CUGA tunnel URL =="
for i in $(seq 1 20); do [ -n "$(cuga_tunnel_url)" ] && break; sleep 2; done
export_public_url
echo "   EVENTS_PUBLIC_URL = ${EVENTS_PUBLIC_URL:-<none — tunnel not up yet>}"

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
echo "  AP tunnel   $(grep -ao 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN/ap_tunnel.log" | tail -1)   → for channel webhooks"
print_public_url
echo ""
echo "next: 'make channels' to arm inbound chat channels · logs in $RUN/*.log · stop: scripts/events_up.sh --stop"
