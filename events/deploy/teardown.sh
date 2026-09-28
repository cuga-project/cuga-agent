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

# Delete every app the split deploy creates — and the retired combined one, so an old
# deployment is cleaned up too. Tearing down only $APP_NAME left cuga-core and
# cuga-events-svc running (and billing, and processing events) while reporting success.
_deleted=0
for _app in "$CORE_APP" "$EVENTS_APP" "$APP_NAME"; do
  [ -n "$_app" ] || continue
  if ibmcloud ce app get -n "$_app" >/dev/null 2>&1; then
    echo "Deleting app '$_app' ..."
    ibmcloud ce app delete --name "$_app" --force --wait --ignore-not-found
    _deleted=$((_deleted + 1))
  else
    echo "App '$_app' not found (already gone)."
  fi
done
if [ "$_deleted" -eq 0 ]; then
  echo "Nothing was deleted — no app named $CORE_APP, $EVENTS_APP or $APP_NAME exists in this project."
fi

if [[ "${WIPE_SECRET:-}" == "1" ]] && ibmcloud ce secret get -n "$SECRET_NAME" >/dev/null 2>&1; then
  echo "Deleting secret '$SECRET_NAME' ..."
  ibmcloud ce secret delete --name "$SECRET_NAME" --force
fi

rm -f "$URLS_ENV_FILE"
echo "Done."
