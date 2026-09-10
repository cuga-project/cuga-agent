#!/usr/bin/env bash
# ============================================================
# Provision the events DATABASE — IBM Cloud Databases for PostgreSQL. Idempotent.
#
# WHY POSTGRES AND NOT THE COS SNAPSHOT (3_state_store.sh).
# Local dev and the deployment must run the SAME storage engine, or local testing proves nothing
# about production. That is not theoretical: on 2026-08-05 a cron armed from Slack vanished twelve
# minutes later when Code Engine replaced the instance, and no local test could have caught it
# because locally the events DB was a SQLite file that survives everything.
#
# With Postgres, durability is a property of the database. No mount, no snapshot loop, no restore
# step, no "did the background task run" question — a new pod connects and the flows are there.
# It also unblocks multi-replica later, which single-writer SQLite never can.
#
# WHAT THIS CREATES (billable — see TEARDOWN):
#   1. a Databases for PostgreSQL instance   $PG_INSTANCE
#   2. service credentials                   $PG_CREDS
#   3. the DSN written into the Code Engine secret $SECRET_NAME as EVENTS_DB
#      (a DSN carries a password, so it must be a secret, never a literal --env)
#
#   ./4_postgres.sh              # plan, then ask
#   YES=1 ./4_postgres.sh        # non-interactive
#
# THEN: ./2_deploy.sh    (it detects EVENTS_DB in the secret and skips the COS path entirely)
# ============================================================
set -euo pipefail
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "${SCRIPT_DIR}/config.sh"

PG_INSTANCE="${EVENTS_PG_INSTANCE:-cuga-events-pg}"
PG_PLAN="${EVENTS_PG_PLAN:-standard}"
PG_REGION="${EVENTS_PG_REGION:-us-east}"
PG_CREDS="${EVENTS_PG_CREDS:-cuga-events-pg-creds}"
PG_DBNAME="${EVENTS_PG_DBNAME:-ibmclouddb}"          # the DB Databases-for-PostgreSQL creates
# Smallest allocation the service ACCEPTS. 4096 is rejected at the broker with
# "group.memory requires a minimum of 8192 megabytes" — this is the floor, not a preference, and it
# is the dominant cost line. Raise disk if the runs log grows.
PG_RAM_MB="${EVENTS_PG_RAM_MB:-8192}"
PG_DISK_MB="${EVENTS_PG_DISK_MB:-10240}"

echo "=============================================================="
echo " Events database — IBM Cloud Databases for PostgreSQL"
echo "=============================================================="
echo "  instance    : $PG_INSTANCE   (plan $PG_PLAN, $PG_REGION)"
echo "  allocation  : ${PG_RAM_MB}MB RAM / ${PG_DISK_MB}MB disk"
echo "  credentials : $PG_CREDS"
echo "  DSN lands in: Code Engine secret '$SECRET_NAME' as EVENTS_DB"
echo
echo "  NOTE: this is a BILLABLE managed database. Provisioning takes ~10-20 minutes."
echo

if [[ "${YES:-0}" != "1" ]]; then
  read -r -p "Create anything above that does not already exist? [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || { echo "aborted — nothing created"; exit 1; }
fi

# ---- 1. the instance ----
if ibmcloud resource service-instance "$PG_INSTANCE" >/dev/null 2>&1; then
  echo "✓ instance '$PG_INSTANCE' already exists"
else
  echo "→ creating '$PG_INSTANCE' (this takes a while) ..."
  # --service-endpoints is REQUIRED (the CLI refuses to default it for this service).
  #   public-and-private : both endpoints exist. Code Engine reaches it over the public one today;
  #                        keeping the private endpoint means we can move to VPE later WITHOUT
  #                        reprovisioning (endpoint type is fixed at creation).
  # The connection is TLS-verified either way (the composed DSN carries sslmode=verify-full) and is
  # credential-guarded, but a public endpoint is still reachable from the internet — restrict it
  # with an allowlist once the CE egress IPs are known:
  #   ibmcloud cdb deployment-whitelist-add "$PG_INSTANCE" --ip-address <cidr>
  ibmcloud resource service-instance-create "$PG_INSTANCE" \
    databases-for-postgresql "$PG_PLAN" "$PG_REGION" \
    --service-endpoints "${EVENTS_PG_ENDPOINTS:-public-and-private}" \
    -p "{\"members_memory_allocation_mb\":${PG_RAM_MB},\"members_disk_allocation_mb\":${PG_DISK_MB}}"
fi

echo "→ waiting for the instance to become active ..."
for _ in $(seq 1 60); do
  state=$(ibmcloud resource service-instance "$PG_INSTANCE" --output json 2>/dev/null \
          | python3 -c 'import sys,json;d=json.load(sys.stdin);print((d[0] if isinstance(d,list) and d else {}).get("state",""))' 2>/dev/null || true)
  [[ "$state" == "active" ]] && { echo "✓ active"; break; }
  echo "   state=$state ..."; sleep 20
done
[[ "$state" == "active" ]] || { echo "✗ instance did not become active — check the console"; exit 1; }

# ---- 2. credentials ----
# As with COS, this account REDACTS credentials on read, so the create response is the only place
# the connection string is visible. Always create fresh rather than trying to read an existing key.
if ibmcloud resource service-key "$PG_CREDS" >/dev/null 2>&1; then
  echo "→ replacing existing credentials '$PG_CREDS' (values are unreadable after creation) ..."
  ibmcloud resource service-key-delete "$PG_CREDS" --force >/dev/null 2>&1 || true
fi
echo "→ creating service credentials '$PG_CREDS' ..."
# Two values come out of this response and BOTH are needed. The DSN ends in
# `sslmode=verify-full`, which means libpq must be able to verify the server against the
# service's own CA — shipped alongside as base64. Without it the client cannot connect at all
# (`root certificate file ... does not exist`), so the CA is not an optional extra.
# Emitted as two lines, DSN then CA, so neither is ever echoed.
PGOUT=$(ibmcloud resource service-key-create "$PG_CREDS" Administrator \
        --instance-name "$PG_INSTANCE" --output json 2>/dev/null \
      | python3 -c '
import sys, json
d = json.load(sys.stdin)
d = d[0] if isinstance(d, list) and d else d
c = d.get("credentials") or {}
# Databases for PostgreSQL exposes connection info under connection.postgres
pg = ((c.get("connection") or {}).get("postgres") or {})
comp = pg.get("composed") or []
if comp:
    dsn = comp[0]
else:
    # fall back to assembling it from the parts
    hosts = pg.get("hosts") or [{}]
    auth  = pg.get("authentication") or {}
    dsn = "postgresql://%s:%s@%s:%s/%s?sslmode=verify-full" % (
        auth.get("username",""), auth.get("password",""),
        hosts[0].get("hostname",""), hosts[0].get("port",""),
        (pg.get("database") or "ibmclouddb"))
# The CA travels as base64 of the PEM. Strip any whitespace: a wrapped value would break both
# the `key=val` env file and `base64 -d` in the container entrypoint.
ca = ((pg.get("certificate") or {}).get("certificate_base64") or "")
ca = "".join(ca.split())
print(dsn); print(ca)
')
DSN=$(printf '%s\n' "$PGOUT" | sed -n 1p)
CA_B64=$(printf '%s\n' "$PGOUT" | sed -n 2p)
unset PGOUT
[[ -n "$DSN" && "$DSN" == postgres* ]] || { echo "✗ could not read a DSN from the create response"; exit 1; }
echo "✓ credentials created (DSN ${#DSN} chars — value not shown)"
if [[ -n "$CA_B64" ]]; then
  echo "✓ database CA captured (${#CA_B64} chars — value not shown)"
else
  echo "⚠ no certificate_base64 in the create response — a verify-full DSN will not connect."
  echo "  Check the service credentials in the console before deploying."
fi

# ---- 3. persist the DSN + CA: Code Engine secret AND .env.ce ----
# BOTH, deliberately. The secret is what the running apps read; .env.ce is what a from-scratch
# deploy REBUILDS the secret from. Writing only the secret is how these two keys used to exist in
# exactly one place in the world — so `teardown.sh WIPE_SECRET=1`, or a deploy into a fresh
# project, destroyed them with nothing to restore from, and 2_deploy.sh then silently fell back
# to ephemeral SQLite and exited 0.
# `secret update` MERGES — it preserves the other keys (bot tokens, watsonx, GATEWAY_TOKEN), so
# do NOT recreate.
echo "→ writing EVENTS_DB into Code Engine secret '$SECRET_NAME' ..."
ibmcloud ce secret update --name "$SECRET_NAME" --from-literal "EVENTS_DB=${DSN}" >/dev/null
if [[ -n "$CA_B64" ]]; then
  ibmcloud ce secret update --name "$SECRET_NAME" --from-literal "EVENTS_DB_CA_B64=${CA_B64}" >/dev/null
fi
echo "✓ secret updated"

echo "→ persisting the same two keys into $ENV_CE_FILE (gitignored, 600) ..."
umask 177
touch "$ENV_CE_FILE"
python3 - "$ENV_CE_FILE" "$DSN" "$CA_B64" <<'PY'
# Rewrite in place, replacing these keys and leaving every other line untouched. Done in Python
# rather than sed because a DSN password and a base64 blob are both full of sed metacharacters.
import sys, pathlib
path, dsn, ca = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
new = {"EVENTS_DB": dsn, "DYNACONF_STORAGE__POSTGRES_URL": dsn}
if ca:
    new["EVENTS_DB_CA_B64"] = ca
lines, seen = [], set()
for line in path.read_text().splitlines():
    key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
    if key in new:
        if key in seen:
            continue
        lines.append(f"{key}={new[key]}"); seen.add(key)
    else:
        lines.append(line)
for key, val in new.items():
    if key not in seen:
        lines.append(f"{key}={val}")
path.write_text("\n".join(lines) + "\n")
PY
chmod 600 "$ENV_CE_FILE"
unset DSN CA_B64
echo "✓ $ENV_CE_FILE updated (make_env_ce.sh carries these forward from here)"

echo
echo "=============================================================="
echo " Done. Deploy — 2_deploy.sh detects EVENTS_DB in the secret:"
echo
echo "   CUGA_CE_ADMIN=1 ./2_deploy.sh"
echo
echo " Verify:"
echo "   curl -s -H \"X-Gateway-Token: \$GATEWAY_TOKEN\" <events-url>/api/events/status | jq .durability"
echo "   # expect backend=postgres; arm a flow, force a pod replace, it is still there"
echo
echo " Once green, the COS snapshot path is redundant — tear it down:"
echo "   ibmcloud ce pds delete --name cuga-events-state --force"
echo "   ibmcloud ce secret delete --name cuga-events-cos-access --force"
echo "   ibmcloud resource service-key-delete cuga-events-state-hmac --force"
echo
echo " TEARDOWN of the database itself (DESTROYS every armed flow):"
echo "   ibmcloud resource service-key-delete $PG_CREDS --force"
echo "   ibmcloud resource service-instance-delete $PG_INSTANCE --force"
echo "=============================================================="
