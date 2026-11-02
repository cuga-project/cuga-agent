#!/bin/sh
set -eu

/usr/local/bin/cuga-dam-prepare-workspace
export PYTHONPATH="/opt/cuga/dam-integration${PYTHONPATH:+:$PYTHONPATH}"
exec /opt/cuga/.venv/bin/python -m deployment.dam.cuga_dam.entrypoint "$@"
