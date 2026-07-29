#!/usr/bin/env bash
# tunnels.sh — status / up / down for the two PUBLIC tunnels. Both are agent processes running on
# THIS machine; if one dies, its public URL goes offline.
#
#   • AP tunnel   (cloudflared → :8081) — its URL is baked into the AP container as AP_FRONTEND_URL at
#     AP start, so it CANNOT be hot-swapped: a URL change needs `make ap` (recreates AP). Used by
#     Telegram/GitHub/Gmail webhooks.
#   • CUGA tunnel (ngrok or cloudflared → :7860) — feeds EVENTS_PUBLIC_URL (Slack Request URL + OAuth).
#     ngrok (EVENTS_NGROK_DOMAIN set) = a STABLE domain you can restart freely; cloudflared = random.
#
#   scripts/tunnels.sh            # status of both
#   scripts/tunnels.sh --up       # (re)start any DOWN tunnel agent
#   scripts/tunnels.sh --down     # stop both tunnel agents
#   scripts/tunnels.sh --restart  # down then up
set -uo pipefail
cd "$(dirname "$0")/.."
RUN=/tmp/events_up; mkdir -p "$RUN"
AP_PORT=8081; CUGA_PORT=7860
DOCKER="$(command -v podman || command -v docker || true)"
env_val() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- \
  | sed -e 's/ *#.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//'; }
NGROK_DOMAIN="$(env_val EVENTS_NGROK_DOMAIN)"

ap_url()   { grep -ao 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN/ap_tunnel.log" 2>/dev/null | tail -1; }
cuga_url() { [ -n "$NGROK_DOMAIN" ] && { echo "https://$NGROK_DOMAIN"; return; }
             grep -ao 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN/cuga_tunnel.log" 2>/dev/null | tail -1; }
reach()    { curl -s -o /dev/null -w '%{http_code}' --max-time 6 "$1" 2>/dev/null || echo 000; }
up_or()    { pgrep -f "$1" >/dev/null 2>&1 && echo "up  " || echo "DOWN"; }
ap_pat="cloudflared tunnel --url http://localhost:$AP_PORT"
cuga_pat="$([ -n "$NGROK_DOMAIN" ] && echo "ngrok http $CUGA_PORT" || echo "cloudflared tunnel --url http://localhost:$CUGA_PORT")"

status() {
  local au cu fe
  au="$(ap_url)"; cu="$(cuga_url)"
  echo "AP tunnel   (cloudflared → :$AP_PORT)   agent: $(up_or "$ap_pat")"
  echo "   url     : ${au:-none}"
  [ -n "$au" ] && echo "   reachable: $(reach "$au/api/v1/flags")   (200 = ok)"
  fe="$([ -n "$DOCKER" ] && $DOCKER exec activepieces printenv AP_FRONTEND_URL 2>/dev/null || true)"
  if [ -n "$fe" ] && [ -n "$au" ] && [ "$fe" != "$au" ]; then
    echo "   ⚠ AP is baked to $fe but the live tunnel is $au → run 'make ap' to re-bake"
  fi
  echo "CUGA tunnel ($([ -n "$NGROK_DOMAIN" ] && echo "ngrok STATIC" || echo "cloudflared") → :$CUGA_PORT)   agent: $(up_or "$cuga_pat")"
  echo "   url     : ${cu:-none}$([ -n "$NGROK_DOMAIN" ] && echo '   (stable)')"
  [ -n "$cu" ] && echo "   reachable: $(reach "$cu/api/events/status")   (200 = ok)"
}

down() {
  echo "stopping tunnel agents (AP + CUGA)…"
  pkill -f "cloudflared tunnel" 2>/dev/null || true
  pkill -f "ngrok http" 2>/dev/null || true
  echo "stopped."
}

up() {
  # CUGA tunnel — safe to (re)start standalone. ngrok keeps the same URL (server stays valid);
  # cloudflared gets a NEW url, so remind to reload.
  if pgrep -f "$cuga_pat" >/dev/null 2>&1; then
    echo "CUGA tunnel already up: $(cuga_url)"
  elif [ -n "$NGROK_DOMAIN" ]; then
    command -v ngrok >/dev/null || { echo "MISSING ngrok — brew install ngrok"; exit 1; }
    ngrok http "$CUGA_PORT" --domain="$NGROK_DOMAIN" --log=stdout > "$RUN/cuga_tunnel.log" 2>&1 & echo $! > "$RUN/cuga_tunnel.pid"
    sleep 3; echo "started ngrok → https://$NGROK_DOMAIN (stable; EVENTS_PUBLIC_URL still valid)"
  else
    cloudflared tunnel --url "http://localhost:$CUGA_PORT" --no-autoupdate > "$RUN/cuga_tunnel.log" 2>&1 & echo $! > "$RUN/cuga_tunnel.pid"
    sleep 4; echo "started cloudflared → $(cuga_url) (NEW url — run 'make reload' to sync EVENTS_PUBLIC_URL)"
  fi
  # AP tunnel — cannot be hot-restarted (URL is baked into AP); tell the user the right lever.
  if ! pgrep -f "$ap_pat" >/dev/null 2>&1; then
    echo "⚠ AP tunnel is DOWN — its URL is baked into AP; restore with 'make ap' (then 'make channels')."
  fi
}

case "${1:---status}" in
  --status|status|"") status ;;
  --up|up)            up ;;
  --down|down)        down ;;
  --restart|restart)  down; sleep 1; up ;;
  *) echo "usage: scripts/tunnels.sh [--status|--up|--down|--restart]"; exit 1 ;;
esac
