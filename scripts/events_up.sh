#!/usr/bin/env bash
# events_up.sh — INFRA PROVISIONER for the event-driven platform, one command.
# Starts the CUGA tunnel, then boots the TWO services:
#   1. CUGA        (`cuga start demo`)  :7860 — the agent + the UI. Knows nothing about events.
#   2. eventing    (`python -m cuga.backend.events.service`) :8100 — triggers, scheduler, channels,
#                   concierge. Executes by calling CUGA's POST /run over HTTP.
# There is no "combined" mode any more: the eventing layer is always its own service.
# (The AP tunnel is owned by ap_up.sh, which bakes it into AP as AP_FRONTEND_URL.)
# Does NOT start Activepieces (your long-lived container) or create external accounts.
#   scripts/events_up.sh          # start
#   scripts/events_up.sh --stop   # stop
#   scripts/events_up.sh --status # show what's running + URLs
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
REGISTRY_PORT="${EVENTS_REGISTRY_PORT:-8001}"
CUGA_PORT="${CUGA_PORT:-7860}"
EVENTS_PORT="${EVENTS_SERVICE_PORT:-8100}"
AP_PORT="$(grep -E '^AP_BASE_URL=' .env 2>/dev/null | sed -E 's|.*:([0-9]+).*|\1|' || echo 8081)"
CFG="src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml"
RUN=/tmp/events_up

# --- the eventing service (service #2) -------------------------------------
# Started AFTER CUGA so its first roster read (GET /run/agents) succeeds. EVENTS_CUGA_PORT is
# deliberately NOT exported: inside the service that variable means "where /invoke lives", and
# service.py repoints it at ITSELF. CUGA_URL is how it finds CUGA.
start_events_service() {
  pkill -f "cuga.backend.events.service" 2>/dev/null || true
  echo "== 2/2 eventing service :$EVENTS_PORT  (→ CUGA at http://localhost:$CUGA_PORT) =="
  CUGA_URL="http://localhost:$CUGA_PORT" EVENTS_SERVICE_PORT="$EVENTS_PORT" \
    nohup .venv/bin/python -m cuga.backend.events.service > "$RUN/events.log" 2>&1 &
  echo $! > "$RUN/events.pid"
  for i in $(seq 1 40); do
    curl -s --max-time 3 "http://localhost:$EVENTS_PORT/health" >/dev/null 2>&1 && return 0
    kill -0 "$(cat "$RUN/events.pid")" 2>/dev/null || {
      echo "eventing service died — see $RUN/events.log"; tail -15 "$RUN/events.log"; exit 1; }
    sleep 2
  done
  echo "eventing service did not come up in 80s — see $RUN/events.log"; exit 1
}

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
  pkill -f "cuga.backend.events.service" 2>/dev/null || true
  pkill -f "uvicorn cuga.backend.tools_env.registry" 2>/dev/null || true
  pkill -f "uvicorn cuga.backend.server.main" 2>/dev/null || true
  pkill -f "cloudflared tunnel" 2>/dev/null || true    # kills BOTH tunnel agents (AP + CUGA)
  pkill -f "ngrok http" 2>/dev/null || true            # ngrok agent (when EVENTS_NGROK_DOMAIN is set)
  echo "stopped."
}
[ "${1:-}" = "--stop" ] && { stop; exit 0; }

if [ "${1:-}" = "--status" ]; then
  echo "registry :$REGISTRY_PORT  $(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:$REGISTRY_PORT/applications || echo down)"
  echo "cuga     :$CUGA_PORT      $(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:$CUGA_PORT/health || echo down)"
  echo "eventing :$EVENTS_PORT      $(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:$EVENTS_PORT/health || echo down)"
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
  export MCP_SERVERS_FILE="$REPO/$CFG" CUGA_SUPERVISOR_ROSTER="${CUGA_SUPERVISOR_ROSTER:-supervisor_agents.yaml}"
  # CUGA IS THE DOOR: /run and /stream forward slash verbs (and open arming dialogues) to the
  # eventing service. Without EVENTS_API_URL that forward is disabled and "/automate …" is
  # handed to the plain agent, which tries to IMPLEMENT the schedule.
  export EVENTS_API_URL="${EVENTS_API_URL:-http://localhost:$EVENTS_PORT}"
  nohup .venv/bin/cuga start demo > "$RUN/cuga.log" 2>&1 & echo $! > "$RUN/cuga.pid"
  for i in $(seq 1 30); do
    curl -s --max-time 3 "http://localhost:$CUGA_PORT/health" >/dev/null 2>&1 && break
    kill -0 "$(cat "$RUN/cuga.pid")" 2>/dev/null || { echo "CUGA died — see $RUN/cuga.log"; tail -15 "$RUN/cuga.log"; exit 1; }
    sleep 2
  done
  start_events_service
  echo "reloaded. CUGA :$CUGA_PORT · events :$EVENTS_PORT · tunnels unchanged"
  print_public_url
  exit 0
fi

# --no-tunnel: boot CUGA with NO public tunnel (and tolerate AP being down). This is the zero-AP
# path — web + Telegram-direct (long-poll) + Discord-direct (Gateway) all work with no tunnel and no
# Activepieces. Slack (needs a public webhook URL) and AP-backed triggers are simply unavailable.
NO_TUNNEL=""; [ "${1:-}" = "--no-tunnel" ] && NO_TUNNEL=1
# Tell the SERVER (hence its capability report) that no tunnel is being served, so it stops claiming
# "public URL set" just because EVENTS_PUBLIC_URL sits in .env. Not in .env → survives dotenv reload.
[ -n "$NO_TUNNEL" ] && export EVENTS_NO_TUNNEL=1

mkdir -p "$RUN"
echo "== prereqs =="
# Require the tunnel tool we ACTUALLY use: ngrok when a reserved domain is set, else cloudflared.
_need="uv"
if [ -z "$NO_TUNNEL" ]; then
  if [ -n "$NGROK_DOMAIN" ]; then _need="uv ngrok"; else _need="uv cloudflared"; fi
fi
for c in $_need; do command -v $c >/dev/null || { echo "MISSING: $c (see events_docs/SETUP.md)"; exit 1; }; done
[ -d .venv ] || { echo "no .venv — running uv sync (minutes)…"; uv sync --python 3.12; }
if [ -n "$NO_TUNNEL" ]; then
  echo "== NO-AP mode: skipping tunnel; AP not required =="
  echo "   channels that work here: web · Telegram (long-poll) · Discord (Gateway).  Slack needs a tunnel → use 'make up'."
else
  curl -s -o /dev/null --max-time 4 "http://localhost:$AP_PORT/api/v1/flags" || \
    echo "WARN: Activepieces not reachable on :$AP_PORT — start your AP container (SETUP.md §2)."
fi

# (No separate registry launch — `cuga start demo` boots the registry itself; the
#  exported MCP_SERVERS_FILE below makes it serve the cuga-apps MCP config.)

# ONLY the CUGA (:7860) tunnel here. The AP (:8081) tunnel is owned by ap_up.sh, which bakes its URL
# into the AP container as AP_FRONTEND_URL — starting a second one here just clobbers ap_tunnel.log
# and leaves AP pointing at a different (often dead) URL, breaking Telegram/Slack-AP setWebhook.
if [ -n "$NO_TUNNEL" ]; then
  echo "== CUGA tunnel: SKIPPED (--no-tunnel) — outbound channels need none =="
elif [ -n "$NGROK_DOMAIN" ]; then
  echo "== CUGA tunnel (ngrok STATIC: $NGROK_DOMAIN) =="
  command -v ngrok >/dev/null || { echo "MISSING: ngrok — brew install ngrok, then verify email + reserve a domain (dashboard.ngrok.com)"; exit 1; }
  ngrok http "$EVENTS_PORT" --domain="$NGROK_DOMAIN" --log=stdout > "$RUN/cuga_tunnel.log" 2>&1 & echo $! > "$RUN/cuga_tunnel.pid"
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
  cloudflared tunnel --url "http://localhost:$EVENTS_PORT" --no-autoupdate > "$RUN/cuga_tunnel.log" 2>&1 & echo $! > "$RUN/cuga_tunnel.pid"
fi

# capture the fresh CUGA tunnel URL and feed it to the server as EVENTS_PUBLIC_URL, so the server
# always matches the live tunnel (no stale-.env "one step behind" after a restart).
if [ -z "$NO_TUNNEL" ]; then
  echo "== resolving CUGA tunnel URL =="
  for i in $(seq 1 20); do [ -n "$(cuga_tunnel_url)" ] && break; sleep 2; done
  export_public_url
  echo "   EVENTS_PUBLIC_URL = ${EVENTS_PUBLIC_URL:-<none — tunnel not up yet>}"
fi

echo "== 1/2 CUGA server :$CUGA_PORT  (registry :$REGISTRY_PORT boots inside it) =="
# Plain CUGA — no events. CUGA_SUPERVISOR_ROSTER preloads it AS the supervisor, which is what the
# eventing service targets over /run, and also puts the tool registry in FILE mode.
export MCP_SERVERS_FILE="$REPO/$CFG" CUGA_SUPERVISOR_ROSTER="${CUGA_SUPERVISOR_ROSTER:-supervisor_agents.yaml}"
  # CUGA IS THE DOOR: /run and /stream forward slash verbs (and open arming dialogues) to the
  # eventing service. Without EVENTS_API_URL that forward is disabled and "/automate …" is
  # handed to the plain agent, which tries to IMPLEMENT the schedule.
  export EVENTS_API_URL="${EVENTS_API_URL:-http://localhost:$EVENTS_PORT}"
nohup .venv/bin/cuga start demo > "$RUN/cuga.log" 2>&1 & echo $! > "$RUN/cuga.pid"

echo "== waiting for CUGA (first boot is slow) =="
for i in $(seq 1 90); do
  curl -s --max-time 3 "http://localhost:$CUGA_PORT/health" >/dev/null 2>&1 && break
  kill -0 "$(cat "$RUN/cuga.pid")" 2>/dev/null || { echo "CUGA died — see $RUN/cuga.log"; tail -15 "$RUN/cuga.log"; exit 1; }
  sleep 4
done
sleep 3

start_events_service

echo ""
echo "READY:"
echo "  CUGA        http://localhost:$CUGA_PORT   (Studio: /manage → Studio)"
echo "  eventing    http://localhost:$EVENTS_PORT/health   (triggers · scheduler · channels)"
echo "  registry    http://localhost:$REGISTRY_PORT/applications"
echo "  AP tunnel   $(grep -ao 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN/ap_tunnel.log" | tail -1)   → for channel webhooks"
print_public_url
echo ""
echo "next: 'make channels' to arm inbound chat channels · logs in $RUN/*.log · stop: scripts/events_up.sh --stop"
