#!/usr/bin/env bash
# events_up.sh — INFRA PROVISIONER for the event-driven platform, one command.
# Starts the CUGA tunnel, then execs the ONE entry point — `cuga start demo --events` — which
# itself boots the MCP registry + the CUGA server. There is no second server launcher: `make up`
# and typing the CLI by hand run the SAME command, the SAME execution path.
# (The AP tunnel is owned by ap_up.sh, which bakes it into AP as AP_FRONTEND_URL.)
# Does NOT start Activepieces (your long-lived container) or create external accounts.
#   scripts/events_up.sh          # start
#   scripts/events_up.sh --stop   # stop
#   scripts/events_up.sh --status # show what's running + URLs
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
REGISTRY_PORT="${EVENTS_REGISTRY_PORT:-8001}"
CUGA_PORT="${EVENTS_CUGA_PORT:-7860}"
AP_PORT="$(grep -E '^AP_BASE_URL=' .env 2>/dev/null | sed -E 's|.*:([0-9]+).*|\1|' || echo 8081)"
CFG="src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml"
RUN=/tmp/events_up

# --- public-URL helpers ----------------------------------------------------
# EVENTS_PUBLIC_URL is the CUGA public base (Slack Request URL + OAuth callbacks). Two modes:
#   • EVENTS_NGROK_DOMAIN set → a STABLE ngrok reserved domain (never changes). This is the fix for
#     the flapping quick-tunnel. We start ngrok (not cloudflared) for :7860 and pin the URL.
#   • unset → a cloudflared quick-tunnel (random per run); we auto-detect + feed the live one in.
env_val() { grep -E "^$1=" "$REPO/.env" 2>/dev/null | tail -1 | cut -d= -f2- \
  | sed -e 's/ *#.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//'; }
NGROK_DOMAIN="$(env_val EVENTS_NGROK_DOMAIN)"

cuga_tunnel_url() {
  [ -n "$NGROK_DOMAIN" ] && { echo "https://$NGROK_DOMAIN"; return; }   # stable ngrok URL is known
  grep -ao 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN/cuga_tunnel.log" 2>/dev/null | tail -1;
}

# Export EVENTS_PUBLIC_URL so the server matches the live tunnel without editing .env. With a stable
# ngrok/pinned URL we just use it; otherwise the live cloudflared URL wins over a stale .env value.
export_public_url() {
  if [ -n "$NGROK_DOMAIN" ]; then export EVENTS_PUBLIC_URL="https://$NGROK_DOMAIN"; return; fi
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
  if [ -n "$NGROK_DOMAIN" ]; then
    echo "  ✓ STABLE (ngrok) — this never changes. Set these EXTERNAL consoles ONCE:"
  else
    echo "  ⚠ quick tunnel — this URL CHANGES on restart (set EVENTS_NGROK_DOMAIN for a stable one)."
    echo "    Each time it changes, re-point these EXTERNAL consoles:"
  fi
  echo "    • Slack  → Event Subscriptions Request URL : $url/api/events/slack/events"
  echo "    • Gmail  → OAuth redirect URI (+ re-consent): $url/api/events/connect/gmail/callback"
  echo "  Nothing to do for: Box (direct poll), Discord (Gateway); Telegram → just 'make channels'."
  echo "─────────────────────────────────────────────────────────"
}

stop() {
  echo "stopping events services…"
  for p in "$RUN"/*.pid; do [ -f "$p" ] && kill "$(cat "$p")" 2>/dev/null && rm -f "$p" || true; done
  # the CLI spawns the registry + server as children; killing its pid can orphan them
  pkill -f "cuga start demo" 2>/dev/null || true
  pkill -f "uvicorn cuga.backend.tools_env.registry" 2>/dev/null || true
  pkill -f "uvicorn cuga.backend.server.main" 2>/dev/null || true
  pkill -f "cloudflared tunnel" 2>/dev/null || true    # kills BOTH tunnel agents (AP + CUGA)
  pkill -f "ngrok http" 2>/dev/null || true            # ngrok agent (when EVENTS_NGROK_DOMAIN is set)
  echo "stopped."
}
[ "${1:-}" = "--stop" ] && { stop; exit 0; }

if [ "${1:-}" = "--status" ]; then
  echo "registry :$REGISTRY_PORT  $(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:$REGISTRY_PORT/applications || echo down)"
  echo "cuga     :$CUGA_PORT      $(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:$CUGA_PORT/api/events/status || echo down)"
  echo "AP tunnel:   $(grep -ao 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN"/ap_tunnel.log 2>/dev/null | tail -1 || echo none)"
  _ct="$(cuga_tunnel_url)"; echo "CUGA tunnel: ${_ct:-none}$([ -n "$NGROK_DOMAIN" ] && echo '  (ngrok static)')"
  exit 0
fi

# --public-url: print the current public URL + the exact strings to paste into Slack/Gmail.
if [ "${1:-}" = "--public-url" ]; then export_public_url; print_public_url; exit 0; fi

# --reload: bounce the CUGA server (registry + demo, both children of the CLI) to pick up
# .env/code changes, KEEPING the tunnels alive — so the tunnel URLs (hence EVENTS_PUBLIC_URL and
# every external webhook/redirect wired to them) do NOT change. This is the cheap inner loop; use
# it instead of a full restart whenever you didn't touch AP or the tunnels.
if [ "${1:-}" = "--reload" ]; then
  [ -f "$RUN/cuga.pid" ] || { echo "no prior run to reload — start it first: scripts/events_up.sh"; exit 1; }
  echo "reloading CUGA server (keeping tunnels)…"
  [ -f "$RUN/cuga.pid" ] && kill "$(cat "$RUN/cuga.pid")" 2>/dev/null || true
  pkill -f "cuga start demo" 2>/dev/null || true
  pkill -f "uvicorn cuga.backend.tools_env.registry" 2>/dev/null || true
  pkill -f "uvicorn cuga.backend.server.main" 2>/dev/null || true
  sleep 2
  export_public_url   # keep the server's EVENTS_PUBLIC_URL matched to the (unchanged) live tunnel
  export MCP_SERVERS_FILE="$REPO/$CFG" EVENTS_CUGA_PORT="$CUGA_PORT"
  nohup .venv/bin/cuga start demo --events > "$RUN/cuga.log" 2>&1 & echo $! > "$RUN/cuga.pid"
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

# (No separate registry launch — `cuga start demo --events` boots the registry itself; the
#  exported MCP_SERVERS_FILE below makes it serve the cuga-apps MCP config.)

# ONLY the CUGA (:7860) tunnel here. The AP (:8081) tunnel is owned by ap_up.sh, which bakes its URL
# into the AP container as AP_FRONTEND_URL — starting a second one here just clobbers ap_tunnel.log
# and leaves AP pointing at a different (often dead) URL, breaking Telegram/Slack-AP setWebhook.
if [ -n "$NGROK_DOMAIN" ]; then
  echo "== CUGA tunnel (ngrok STATIC: $NGROK_DOMAIN) =="
  command -v ngrok >/dev/null || { echo "MISSING: ngrok — brew install ngrok, then verify email + reserve a domain (dashboard.ngrok.com)"; exit 1; }
  ngrok http "$CUGA_PORT" --domain="$NGROK_DOMAIN" --log=stdout > "$RUN/cuga_tunnel.log" 2>&1 & echo $! > "$RUN/cuga_tunnel.pid"
  for i in $(seq 1 10); do
    grep -q 'started tunnel\|msg="join connexions"\|url=' "$RUN/cuga_tunnel.log" 2>/dev/null && break
    if grep -q 'ERR_NGROK' "$RUN/cuga_tunnel.log" 2>/dev/null; then
      _errcode="$(grep -ao 'ERR_NGROK_[0-9]*' "$RUN/cuga_tunnel.log" | tail -1)"
      echo "  ✗ ngrok could not start ($_errcode):"
      grep -o 'err="[^"]*"' "$RUN/cuga_tunnel.log" | tail -1
      if [ "$_errcode" = "ERR_NGROK_334" ]; then
        # The reserved domain is already served by a stale ngrok agent (a previous run that outlived
        # its make process). Point the user at the one command that frees it.
        echo "    → CAUSE: a previous ngrok is still holding '$NGROK_DOMAIN'."
        echo "    → FIX:   pkill -f 'ngrok http'   (frees the domain), then re-run: make up"
        echo "             (or 'make stop' to clear the whole stack first, then 'make up')"
      else
        echo "    Common: verify your email at dashboard.ngrok.com, and reserve the domain '$NGROK_DOMAIN'."
      fi
      exit 1
    fi
    sleep 1
  done
else
  echo "== CUGA tunnel (cloudflared quick — flaps; set EVENTS_NGROK_DOMAIN for a stable URL) =="
  cloudflared tunnel --url "http://localhost:$CUGA_PORT" --no-autoupdate > "$RUN/cuga_tunnel.log" 2>&1 & echo $! > "$RUN/cuga_tunnel.pid"
fi

# capture the fresh CUGA tunnel URL and feed it to the server as EVENTS_PUBLIC_URL, so the server
# always matches the live tunnel (no stale-.env "one step behind" after a restart).
echo "== resolving CUGA tunnel URL =="
for i in $(seq 1 20); do [ -n "$(cuga_tunnel_url)" ] && break; sleep 2; done
export_public_url
echo "   EVENTS_PUBLIC_URL = ${EVENTS_PUBLIC_URL:-<none — tunnel not up yet>}"

echo "== CUGA server :$CUGA_PORT  (registry :$REGISTRY_PORT boots inside it) =="
# THE one entry point — the literal same command a user types. The CLI starts the registry and
# the server as children; everything events-specific rides on --events + the env below.
export MCP_SERVERS_FILE="$REPO/$CFG" EVENTS_CUGA_PORT="$CUGA_PORT"
nohup .venv/bin/cuga start demo --events > "$RUN/cuga.log" 2>&1 & echo $! > "$RUN/cuga.pid"

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
