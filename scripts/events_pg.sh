#!/usr/bin/env bash
# ============================================================
# The LOCAL events database — PostgreSQL, the same engine we deploy.
#
# WHY LOCAL IS POSTGRES NOW. Local dev used to be a SQLite file (survives everything) while Code
# Engine ran SQLite on an ephemeral disk. Two different durability stories, and the fragile one was
# the only one nobody exercised — so a pod replacement on 2026-08-05 silently deleted a cron armed
# from Slack twelve minutes earlier, and no local test could ever have caught it. Same engine
# everywhere means local testing means something.
#
#   scripts/events_pg.sh up      # start (idempotent) and print the DSN
#   scripts/events_pg.sh stop    # stop, keep the data
#   scripts/events_pg.sh reset   # DESTROY the data and recreate
#   scripts/events_pg.sh dsn     # print the DSN only
#
# The password here is a LOCAL DEV credential and is deliberately in the open — this container
# listens on localhost only and holds nothing but test flows. The deployed database uses a real
# secret from the Code Engine secret store; never reuse this one.
# ============================================================
set -euo pipefail

CONTAINER="${PG_CONTAINER:-cuga-events-pg}"
PORT="${PG_PORT:-5433}"
PGUSER="${PG_USER:-cuga}"
PGPASS="${PG_PASSWORD:-cuga_dev_pw}"
PGDB="${PG_DB:-cuga_events}"
IMAGE="${PG_IMAGE:-docker.io/library/postgres:16-alpine}"
DSN="postgresql://${PGUSER}:${PGPASS}@localhost:${PORT}/${PGDB}"

# podman is the stack's runtime (AP uses it too); fall back to docker if that is what is present.
RT="$(command -v podman || command -v docker || true)"
[[ -n "$RT" ]] || { echo "✗ no container runtime — brew install podman && podman machine start"; exit 1; }

_running() { "$RT" ps    --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; }
_exists()  { "$RT" ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; }

_wait_ready() {
  for _ in $(seq 1 40); do
    "$RT" exec "$CONTAINER" pg_isready -U "$PGUSER" -d "$PGDB" >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "✗ postgres did not become ready in 40s — $RT logs $CONTAINER"; return 1
}

up() {
  if _running; then
    echo "✓ events postgres already running (${CONTAINER}, :${PORT})"
  else
    if _exists; then
      echo "→ starting existing container ${CONTAINER} ..."
      "$RT" start "$CONTAINER" >/dev/null
    else
      echo "→ creating ${CONTAINER} (postgres 16, :${PORT}) ..."
      "$RT" run -d --name "$CONTAINER" \
        -e POSTGRES_USER="$PGUSER" -e POSTGRES_PASSWORD="$PGPASS" -e POSTGRES_DB="$PGDB" \
        -p "${PORT}:5432" "$IMAGE" >/dev/null
    fi
    _wait_ready
    echo "✓ events postgres ready"
  fi
  echo
  echo "  EVENTS_DB=${DSN}"
  echo
  echo "  Put that line in .env so both services use it (the eventing service reads EVENTS_DB)."
}

case "${1:-up}" in
  up)    up ;;
  stop)  _running && { "$RT" stop "$CONTAINER" >/dev/null; echo "✓ stopped (data kept)"; } || echo "· not running" ;;
  reset)
    echo "!! destroying the local events database — every locally armed flow is lost"
    "$RT" rm -f "$CONTAINER" >/dev/null 2>&1 || true
    up ;;
  dsn)   echo "$DSN" ;;
  *)     echo "usage: $0 {up|stop|reset|dsn}"; exit 2 ;;
esac
