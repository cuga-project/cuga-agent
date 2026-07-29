#!/usr/bin/env bash
# ap_up.sh — bring up Activepieces RELIABLY: postgres + redis + the app, on a shared network,
# with persistent volumes, stable secrets, and a public tunnel (channel webhooks need an HTTPS
# URL, which AP builds from AP_FRONTEND_URL). Also signs up the AP admin from .env.
#
#   scripts/ap_up.sh                                  # tunnel + pg + redis + AP + admin sign-up
#   AP_TUNNEL_URL=https://x.example scripts/ap_up.sh   # reuse an existing tunnel URL
#   scripts/ap_up.sh --stop                           # stop AP + pg + redis + tunnel
#
# The activepieces:latest image is NOT all-in-one — it REQUIRES external postgres + redis and a
# full env (POSTGRES host/port/db/user/pass, REDIS host/port, encryption key, jwt, worker token).
# Secrets are generated once into .ap.env (gitignored) and reused, so the volume's encrypted data
# keeps decrypting across recreates.
set -euo pipefail
cd "$(dirname "$0")/.."
AP_PORT="${AP_PORT:-8081}"
NET=ap-net
D="$(command -v podman || command -v docker || true)"
[ -n "$D" ] || { echo "✗ no container runtime (podman/docker) — see: make preflight"; exit 1; }
RUN=/tmp/events_up; mkdir -p "$RUN"

if [ "${1:-}" = "--stop" ]; then
  $D rm -f activepieces ap-postgres ap-redis 2>/dev/null || true
  pkill -f "cloudflared tunnel --url http://localhost:$AP_PORT" 2>/dev/null || true
  echo "AP (app+pg+redis) + tunnel stopped."; exit 0
fi

# stable secrets (must persist or the volume's encrypted connections break).
# NB: do NOT set AP_WORKER_TOKEN — the AP entrypoint mints it as a JWT signed with AP_JWT_SECRET
# (payload {type:'WORKER'}) and the app validates it as such. A raw random string fails that JWT
# check → the worker crash-loops on "Socket.IO Authentication error" → every piece-trigger publish
# hangs at engineWatcher#listen (30s) and channel arms fail. Leaving it unset = a valid token.
if [ ! -f .ap.env ]; then
  { echo "AP_ENCRYPTION_KEY=$(openssl rand -hex 16)"
    echo "AP_JWT_SECRET=$(openssl rand -hex 32)"; } > .ap.env
  grep -q '^\.ap\.env$' .gitignore 2>/dev/null || echo ".ap.env" >> .gitignore
  echo "generated .ap.env"
fi
# drop any legacy AP_WORKER_TOKEN a prior version of this script may have written
if grep -q '^AP_WORKER_TOKEN=' .ap.env 2>/dev/null; then
  grep -v '^AP_WORKER_TOKEN=' .ap.env > .ap.env.tmp && mv .ap.env.tmp .ap.env
  echo "removed stale AP_WORKER_TOKEN from .ap.env (entrypoint mints a valid JWT)"
fi
set -a; . ./.ap.env; set +a

# tunnel (reuse AP_TUNNEL_URL or start cloudflared)
TUN="${AP_TUNNEL_URL:-}"
if [ -z "$TUN" ]; then
  command -v cloudflared >/dev/null || { echo "✗ cloudflared missing — brew install cloudflared (or: make preflight)"; exit 1; }
  pkill -f "cloudflared tunnel --url http://localhost:$AP_PORT" 2>/dev/null || true
  cloudflared tunnel --url "http://localhost:$AP_PORT" --no-autoupdate > "$RUN/ap_tunnel.log" 2>&1 &
  # NB: the `|| true` is load-bearing. Under `set -euo pipefail`, on the first iteration cloudflared
  # hasn't written the URL yet, so grep matches nothing and exits 1; pipefail propagates that to the
  # `TUN=$(...)` assignment, and set -e then kills the whole script before the URL ever appears.
  for i in $(seq 1 20); do TUN=$(grep -ao 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN/ap_tunnel.log" 2>/dev/null | tail -1 || true); [ -n "$TUN" ] && break; sleep 2; done
fi
[ -n "$TUN" ] || { echo "no tunnel URL"; exit 1; }
echo "AP_FRONTEND_URL = $TUN"

$D network create $NET >/dev/null 2>&1 || true
$D rm -f activepieces ap-postgres ap-redis >/dev/null 2>&1 || true
echo "== postgres =="; $D run -d --name ap-postgres --network $NET -v ap_pgdata:/var/lib/postgresql/data \
  -e POSTGRES_DB=activepieces -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres postgres:14 >/dev/null
echo "== redis =="; $D run -d --name ap-redis --network $NET -v ap_redis:/data redis:7 >/dev/null
sleep 12
echo "== activepieces =="; $D run -d --name activepieces --network $NET -p "$AP_PORT:80" \
  -e AP_FRONTEND_URL="$TUN" -e AP_QUEUE_MODE=REDIS -e AP_EXECUTION_MODE=UNSANDBOXED \
  -e AP_POSTGRES_HOST=ap-postgres -e AP_POSTGRES_PORT=5432 -e AP_POSTGRES_DATABASE=activepieces \
  -e AP_POSTGRES_USERNAME=postgres -e AP_POSTGRES_PASSWORD=postgres \
  -e AP_REDIS_HOST=ap-redis -e AP_REDIS_PORT=6379 \
  -e AP_ENCRYPTION_KEY="$AP_ENCRYPTION_KEY" -e AP_JWT_SECRET="$AP_JWT_SECRET" \
  activepieces/activepieces:latest >/dev/null

echo "waiting for AP (first-boot migrations)…"
for i in $(seq 1 40); do curl -s -o /dev/null --max-time 4 "http://localhost:$AP_PORT/api/v1/flags" && break; sleep 5; done

# HEALTH GATE: the AP worker must actually connect to the app, or every piece-trigger publish
# (channel/integration arm) will silently hang for 30s at engineWatcher#listen. The classic cause
# is an invalid AP_WORKER_TOKEN (see note above). Fail loudly HERE instead of at arm time.
echo "checking AP worker health…"
sleep 8
WORKER_LINE=$($D exec activepieces sh -c "npx pm2 jlist 2>/dev/null" 2>/dev/null \
  | node -e "try{const a=JSON.parse(require('fs').readFileSync(0));const w=a.find(x=>x.name==='activepieces-worker')||{};const m=w.pm2_env||{};process.stdout.write((m.restart_time||0)+' '+(m.status||'?'))}catch(e){process.stdout.write('NA ?')}" 2>/dev/null || true)
WORKER_LINE=${WORKER_LINE:-NA ?}
RESTARTS=${WORKER_LINE%% *}; WSTATUS=${WORKER_LINE##* }
if [ "$RESTARTS" != "NA" ] && [ "${RESTARTS:-0}" -gt 5 ] 2>/dev/null; then
  echo "  ✗ AP worker is crash-looping (restarts=$RESTARTS, status=$WSTATUS)."
  echo "    Almost always an invalid AP_WORKER_TOKEN — it MUST be a JWT signed with AP_JWT_SECRET,"
  echo "    or (recommended) left UNSET so the entrypoint mints one. Check: podman exec activepieces \\"
  echo "      sh -c 'tail /root/.pm2/logs/activepieces-worker-*.log' | grep -i 'Authentication error'"
  echo "    Channel/integration arming WILL hang until this is fixed."
else
  echo "  ✓ AP worker healthy (restarts=${RESTARTS:-0}, status=${WSTATUS:-online})"
fi

# sign up admin from .env (idempotent)
EMAIL=$(grep '^AP_EMAIL=' .env 2>/dev/null|cut -d= -f2-|sed 's/ *#.*//'|tr -d ' ')
PW=$(grep '^AP_PASSWORD=' .env 2>/dev/null|cut -d= -f2-|sed 's/ *#.*//'|tr -d ' ')
[ -n "$EMAIL" ] && curl -s -o /dev/null -w "  admin sign-up: HTTP %{http_code}\n" --max-time 20 \
  -X POST "http://localhost:$AP_PORT/api/v1/authentication/sign-up" -H 'content-type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\",\"firstName\":\"Admin\",\"lastName\":\"User\",\"trackEvents\":false,\"newsLetter\":false}" || true

# Ensure the pieces the events layer needs are present before we hand back. On a FRESH DB the AP
# cloud-sync (PIECES_SYNC_MODE=OFFICIAL_AUTO) takes ~3-4 min to deliver the full catalog; until it
# does, an integration Connect 404s with `piece_metadata_not_found`. ap_pieces.py WAITS for the sync
# to deliver our pieces (and installs them directly, pinned, only if it stalls). This step therefore
# blocks for up to a few minutes on a first/fresh boot — that's expected, not a hang.
echo "ensuring integration pieces are installed (fresh DB: waiting for AP catalog sync, up to ~4 min)…"
PYBIN="$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)"
"$PYBIN" scripts/ap_pieces.py \
  || echo "  ⚠ piece ensure incomplete — run 'make ap-pieces' once the network is up (Connect 404s until then)."

echo ""; echo "AP READY: http://localhost:$AP_PORT  (public: $TUN)"
echo "  the tunnel URL is EPHEMERAL — re-run this script if cloudflared restarts."
