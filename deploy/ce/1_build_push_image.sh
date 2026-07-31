#!/bin/bash
# ============================================================
# Step 1 — Build the CUGA-events image with a Code Engine buildrun (cloud-side; no
# local docker needed) and push it to icr.io/<namespace>/cuga-events:latest.
#
# A clean, staged build context EXCLUDES venvs, .git, node_modules, the 54MB
# frontend build artifact, and — critically — deploy/ce/.env.ce, so no secret ever
# lands in the image or the upload.
# ============================================================
set -euo pipefail
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "${SCRIPT_DIR}/config.sh"
admin_guard "${1:-}"
require_login
ce_target

if ! ibmcloud ce secret get -n "$REGISTRY_SECRET_NAME" >/dev/null 2>&1; then
  echo "Registry secret '$REGISTRY_SECRET_NAME' not found in '$CE_PROJECT_NAME'."
  echo "Create it once:  ibmcloud ce registry create --name $REGISTRY_SECRET_NAME \\"
  echo "                   --server $REGISTRY_HOST --username iamapikey --password <IBM_CLOUD_API_KEY>"
  exit 1
fi

# Stage a lean build context. NOTE the anchored excludes and — above all —
# deploy/ce/.env.ce, which must NEVER be uploaded or baked into the image.
STAGE="$(mktemp -d -t cuga-build-XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
echo "Staging build context (excluding venvs, .git, node_modules, dist artifact, secrets) -> $STAGE ..."
rsync -a \
  --exclude '.git/' \
  --exclude '.venv*' --exclude 'venv/' \
  --exclude 'node_modules/' \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude '.pytest_cache/' \
  --exclude 'src/frontend_workspaces/' \
  --exclude 'src/cuga/logging/trajectory_data/' \
  --exclude '*.egg-info/' \
  --exclude '*.js.map' \
  --exclude '*.db' --exclude 'events.db' \
  --exclude '/output/' --exclude '/results/' \
  --exclude 'deploy/ce/.env.ce' \
  --exclude 'deploy/ce/.ce_urls.env' \
  --exclude '.DS_Store' \
  "$APP_ROOT/" "$STAGE/"

# Belt-and-suspenders: prove the secret file did not make it into the context.
if [ -f "$STAGE/deploy/ce/.env.ce" ]; then
  echo "ABORT: .env.ce leaked into the build context. Refusing to upload secrets."; exit 1
fi
CTX_SIZE=$(du -sh "$STAGE" 2>/dev/null | cut -f1)
echo "Build context size: ${CTX_SIZE:-?}"

uuid=$(uuidgen | tr '[:upper:]' '[:lower:]' | awk -F- '{print $1}')
BUILD_NAME="cuga-events-build-${uuid}"

echo "Submitting buildrun '$BUILD_NAME' -> $IMAGE_REF (uv sync is heavy; ~10-20 min) ..."
ibmcloud ce buildrun submit \
  --name "$BUILD_NAME" \
  --source "$STAGE" \
  --strategy dockerfile \
  --dockerfile deploy/ce/Dockerfile.events \
  --image "$IMAGE_REF" \
  --registry-secret "$REGISTRY_SECRET_NAME" \
  --size xlarge \
  --timeout 2400

ibmcloud ce buildrun logs -f -n "$BUILD_NAME"

STATUS=$(ibmcloud ce buildrun get -n "$BUILD_NAME" -o json 2>/dev/null \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('status',{}).get('reason','') if isinstance(d.get('status'),dict) else d.get('status',''))" 2>/dev/null || echo "")
echo ""
echo "Buildrun status: ${STATUS:-see logs above}"
echo "Image: $IMAGE_REF"
echo "Next:  CUGA_CE_ADMIN=1 ./2_deploy_app.sh"
