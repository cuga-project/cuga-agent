#!/bin/sh
set -eu

/usr/local/bin/cuga-dam-prepare-workspace
exec /opt/cuga/.venv/bin/cuga-acp "$@"
