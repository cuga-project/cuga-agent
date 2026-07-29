#!/usr/bin/env bash
# arm_channels.sh — connect + arm every INBOUND chat channel that has a token in .env.
#
# Turns "token in .env" into a live inbound path:
#   · Telegram → AP backend: (idempotently) create the bot connection, then arm the AP webhook flow.
#   · Slack    → DIRECT backend: arm returns the Request URL to paste into your Slack app.
#   · Discord  → DIRECT backend: the Gateway bot connects on server boot; arm just confirms.
#   · Web      → built-in, always on; nothing to do.
# Idempotent — safe to re-run (e.g. after a restart changed the tunnel URLs). Needs CUGA up.
#
#   scripts/arm_channels.sh            # arm all configured channels
#   scripts/arm_channels.sh --status   # show inbound-channel state without changing anything
set -euo pipefail
cd "$(dirname "$0")/.."
CUGA="${EVENTS_CUGA_URL:-http://localhost:7860}"
SCOPE="${EVENTS_SCOPE:-default/default/admin}"

# NB: trailing `|| true` — grep exits 1 when the key is ABSENT, which under `set -o pipefail` would
# abort the whole script. val() is used for OPTIONAL keys (missing tokens, unset backend flags), so a
# no-match must yield an empty string, not a failure.
val() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/ *#.*//' | tr -d ' ' || true; }

curl -s -o /dev/null --max-time 4 "$CUGA/api/events/status" \
  || { echo "✗ CUGA not reachable at $CUGA — start it first: make cuga (or make up)"; exit 1; }

if [ "${1:-}" = "--status" ]; then
  echo "inbound channels (configured in Studio view):"
  curl -s --max-time 5 "$CUGA/api/events/channels" \
    | python3 -c "import sys,json;[print(f\"  {c['name']:<9} {c.get('status','?'):<14} {c.get('backend','?')}\") for c in json.load(sys.stdin).get('channels',[])]" 2>/dev/null \
    || echo "  (could not read /api/events/channels)"
  exit 0
fi

# POST helper → pretty one-line result via python
post() { curl -s --max-time 60 -X POST "$CUGA$1" -H 'content-type: application/json' -d "$2"; }
show() { python3 -c "import sys,json
try: d=json.load(sys.stdin)
except Exception: print('  ✗ (non-JSON reply)'); sys.exit()
if not d.get('ok'): print('  ✗', str(d.get('error'))[:200]); sys.exit()
$1"; }

echo "== arming inbound channels (scope: $SCOPE) =="
echo "web       ✓ built-in (always on)"

TG=$(val TELEGRAM_BOT_TOKEN)
TG_BACKEND=$(val EVENTS_TELEGRAM_BACKEND); TG_BACKEND=${TG_BACKEND:-direct}
if [ -n "$TG" ]; then
  if [ "$TG_BACKEND" = "ap" ]; then
    # AP backend: ensure the bot connection exists (auto-created on startup too), then arm the webhook flow
    post "/api/events/connect/telegram/token" "{\"scope\":\"$SCOPE\",\"token\":\"$TG\"}" >/dev/null || true
    echo -n "telegram  "; post "/api/events/admin/channels/telegram/arm" "{\"scope\":\"$SCOPE\"}" \
      | show "print('✓ AP flow armed & enabled:', d.get('ap_flow_id'))"
  else
    # DIRECT backend (default): the long-poll loop connects on server start — no AP, no public URL.
    echo -n "telegram  "; post "/api/events/admin/channels/telegram/arm" "{\"scope\":\"$SCOPE\"}" \
      | show "print('✓ direct long-poll —', str(d.get('note',''))[:100])"
  fi
else
  echo "telegram  · no TELEGRAM_BOT_TOKEN (skip)"
fi

SL=$(val SLACK_BOT_TOKEN)
if [ -n "$SL" ]; then
  echo -n "slack     "; post "/api/events/admin/channels/slack/arm" "{\"scope\":\"$SCOPE\"}" \
    | show "print('✓ direct — set Slack Event Subscriptions Request URL to:'); print('           ', d.get('events_url')); print('            signature verification:', d.get('signature_verification'))"
else
  echo "slack     · no SLACK_BOT_TOKEN (skip)"
fi

DS=$(val DISCORD_BOT_TOKEN)
if [ -n "$DS" ]; then
  echo -n "discord   "; post "/api/events/admin/channels/discord/arm" "{\"scope\":\"$SCOPE\"}" \
    | show "print('✓ direct Gateway —', str(d.get('note',''))[:110])"
else
  echo "discord   · no DISCORD_BOT_TOKEN (skip)"
fi

echo
echo "Done. Integrations (GitHub/Box/Gmail) are armed per-trigger from chat, not here — see events_docs/setup/."
