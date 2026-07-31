#!/bin/bash
# ============================================================
# Tear down the CUGA-events Code Engine app (and optionally its secret).
#   CUGA_CE_ADMIN=1 ./teardown.sh            # delete the app
#   CUGA_CE_ADMIN=1 WIPE_SECRET=1 ./teardown.sh   # also delete the CE secret
# Leaves the registry image + the shared registry secret intact.
# ============================================================
set -euo pipefail
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "${SCRIPT_DIR}/config.sh"
admin_guard "${1:-}"
require_login
ce_target

if ibmcloud ce app get -n "$APP_NAME" >/dev/null 2>&1; then
  echo "Deleting app '$APP_NAME' ..."
  ibmcloud ce app delete --name "$APP_NAME" --force --wait --ignore-not-found
else
  echo "App '$APP_NAME' not found (already gone)."
fi

if [[ "${WIPE_SECRET:-}" == "1" ]] && ibmcloud ce secret get -n "$SECRET_NAME" >/dev/null 2>&1; then
  echo "Deleting secret '$SECRET_NAME' ..."
  ibmcloud ce secret delete --name "$SECRET_NAME" --force
fi

rm -f "$URLS_ENV_FILE"
echo "Done."
