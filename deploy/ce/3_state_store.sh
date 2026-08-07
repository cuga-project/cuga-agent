#!/usr/bin/env bash
# ============================================================
# Provision DURABLE STATE for the eventing service — idempotent.
#
# WHY. The Code Engine container filesystem is ephemeral. The platform can replace the instance at
# any time (new revision, node drain, reschedule) and the new pod starts with an EMPTY disk — with
# no restart recorded, so `ibmcloud ce app get` still shows "Restarts: 0". Every armed flow is gone.
# Observed 2026-08-05: a cron armed from Slack at 11:12 vanished when a new pod started at 11:24.
#
# WHAT THIS CREATES (all reusable, all deletable — see TEARDOWN at the bottom):
#   1. a COS bucket                     $BUCKET        (in the $COS_INSTANCE service instance)
#   2. HMAC service credentials         $CREDS_NAME    (so Code Engine can mount the bucket)
#   3. a Code Engine secret             $SECRET_NAME   (holds those credentials)
#   4. a Code Engine persistent store   $STORE_NAME    (binds the bucket to the project)
#
# It creates nothing that already exists, and prints a plan before touching anything.
#
#   ./3_state_store.sh              # show the plan, then ask
#   YES=1 ./3_state_store.sh        # non-interactive
#
# THEN: EVENTS_STATE_STORE=$STORE_NAME ./2_deploy.sh
#
# NOTE ON THE DESIGN. The store is COS-backed (object storage). SQLite must NOT run on it — no
# POSIX locking, whole-object rewrites, corruption. So the live DB stays on local disk and the
# service snapshots to the mount (src/cuga/backend/events/db_persist.py). This is correct for the
# single writer we deploy (min=max=1). Multiple replicas need Postgres — see events_docs/.
# ============================================================
set -euo pipefail
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "${SCRIPT_DIR}/config.sh"

STORE_NAME="${EVENTS_STATE_STORE:-cuga-events-state}"
BUCKET="${EVENTS_STATE_BUCKET:-cuga-events-state-$(ibmcloud target --output json | python3 -c 'import sys,json;print(json.load(sys.stdin)["account"]["guid"][:8])' 2>/dev/null || echo local)}"
COS_INSTANCE="${EVENTS_STATE_COS_INSTANCE:-cuga-cos}"
BUCKET_REGION="${EVENTS_STATE_BUCKET_REGION:-us-east}"
CREDS_NAME="${EVENTS_STATE_CREDS:-cuga-events-state-hmac}"
SECRET_NAME="${EVENTS_STATE_SECRET:-cuga-events-cos-access}"

echo "=============================================================="
echo " Durable state for the eventing service"
echo "=============================================================="
echo "  COS instance      : $COS_INSTANCE"
echo "  bucket            : $BUCKET   ($BUCKET_REGION)"
echo "  service creds     : $CREDS_NAME   (HMAC)"
echo "  Code Engine secret: $SECRET_NAME"
echo "  data store        : $STORE_NAME"
echo

if [[ "${YES:-0}" != "1" ]]; then
  read -r -p "Create anything above that does not already exist? [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || { echo "aborted — nothing created"; exit 1; }
fi

# ---- 1. COS instance must exist (we do NOT create service instances here) ----
COS_GUID=$(ibmcloud resource service-instance "$COS_INSTANCE" --output json 2>/dev/null \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d[0]["guid"] if isinstance(d,list) and d else "")' 2>/dev/null || true)
if [[ -z "$COS_GUID" ]]; then
  echo "✗ COS instance '$COS_INSTANCE' not found."
  echo "  Pick an existing one with EVENTS_STATE_COS_INSTANCE=<name>, or create one:"
  echo "    ibmcloud resource service-instance-create $COS_INSTANCE cloud-object-storage standard global"
  exit 1
fi
echo "✓ COS instance '$COS_INSTANCE' ($COS_GUID)"

# ---- 2. bucket ----
# NB: `cos bucket-head` reports "not found" for a bucket that demonstrably exists (it resolves the
# wrong regional endpoint), which made this step non-idempotent and failed the second run with
# "bucket name is not available". Listing is authoritative.
if ibmcloud cos buckets --ibm-service-instance-id "$COS_GUID" 2>/dev/null \
     | awk '{print $1}' | grep -qx "$BUCKET"; then
  echo "✓ bucket '$BUCKET' already exists"
else
  echo "→ creating bucket '$BUCKET' ..."
  ibmcloud cos bucket-create --bucket "$BUCKET" --ibm-service-instance-id "$COS_GUID" \
    --region "$BUCKET_REGION" --class smart
  echo "✓ bucket created"
fi

# ---- 3+4. HMAC service credentials → Code Engine secret ----
# CAPTURE THE KEYS AT CREATION. This account redacts credentials on read: both
# `ibmcloud resource service-key <name> --output json` and the resource-controller REST API return
# {"credentials": {"REDACTED": "REDACTED_EXPLICIT"}}. The CREATE response is not redacted, so it is
# the only chance to read the HMAC pair — which is why this block always creates a fresh key rather
# than reusing an existing one it cannot read. The keys are piped straight into the Code Engine
# secret and never echoed or written to disk.
if ibmcloud ce secret get --name "$SECRET_NAME" >/dev/null 2>&1 \
   && ibmcloud resource service-key "$CREDS_NAME" >/dev/null 2>&1; then
  echo "✓ credentials '$CREDS_NAME' + secret '$SECRET_NAME' already in place (keys unreadable by"
  echo "  design — delete both to rotate: ibmcloud ce secret delete --name $SECRET_NAME --force &&"
  echo "  ibmcloud resource service-key-delete $CREDS_NAME --force)"
else
  ibmcloud resource service-key-delete "$CREDS_NAME" --force >/dev/null 2>&1 || true
  ibmcloud ce secret delete --name "$SECRET_NAME" --force  >/dev/null 2>&1 || true
  echo "→ creating HMAC service credentials '$CREDS_NAME' (capturing keys at creation) ..."
  KEYS=$(ibmcloud resource service-key-create "$CREDS_NAME" Writer \
           --instance-name "$COS_INSTANCE" --parameters '{"HMAC":true}' --output json 2>/dev/null \
         | python3 -c '
import sys, json
d = json.load(sys.stdin)
d = d[0] if isinstance(d, list) and d else d
h = (d.get("credentials") or {}).get("cos_hmac_keys") or {}
ak, sk = h.get("access_key_id", ""), h.get("secret_access_key", "")
if not (ak and sk):
    sys.exit("no cos_hmac_keys in the create response")
print(ak); print(sk)')
  [[ -n "$KEYS" ]] || { echo "✗ could not read HMAC keys from the create response"; exit 1; }
  AK=$(printf '%s\n' "$KEYS" | sed -n 1p)
  SK=$(printf '%s\n' "$KEYS" | sed -n 2p)
  unset KEYS
  echo "✓ credentials created (access key ${#AK} chars — value not shown)"
  ibmcloud ce secret create --name "$SECRET_NAME" --format hmac \
    --access-key-id "$AK" --secret-access-key "$SK" >/dev/null
  unset AK SK
  echo "✓ Code Engine secret '$SECRET_NAME'"
fi

# ---- 5. persistent data store ----
if ibmcloud ce pds get --name "$STORE_NAME" >/dev/null 2>&1; then
  echo "✓ data store '$STORE_NAME' already exists"
else
  echo "→ creating persistent data store '$STORE_NAME' ..."
  ibmcloud ce pds create --name "$STORE_NAME" --cos-access-secret "$SECRET_NAME" \
    --cos-bucket-name "$BUCKET" --cos-bucket-location "$BUCKET_REGION"
  echo "✓ data store created"
fi

echo
echo "=============================================================="
echo " Done. Deploy with durable state:"
echo
echo "   EVENTS_STATE_STORE=$STORE_NAME ./2_deploy.sh"
echo
echo " Verify after deploy (should report durable:true):"
echo "   curl -s -H \"X-Gateway-Token: \$GATEWAY_TOKEN\" <events-url>/api/events/status | jq .durability"
echo
echo " TEARDOWN (removes everything this script created):"
echo "   ibmcloud ce pds delete --name $STORE_NAME --force"
echo "   ibmcloud ce secret delete --name $SECRET_NAME --force"
echo "   ibmcloud resource service-key-delete $CREDS_NAME --force"
echo "   ibmcloud cos bucket-delete --bucket $BUCKET --force   # deletes the snapshots too"
echo "=============================================================="
