#!/bin/sh
# DAM chat launcher: CUGA's ACP server over stdio. stdout carries JSON-RPC only.
mkdir -p "$CUGA_DBS_DIR" "$CUGA_LOGGING_DIR" 2>/dev/null
exec /opt/venv/bin/cuga-acp "$@"
