#!/usr/bin/env sh
# Container entrypoint — materialise the managed database's CA, then run the given command.
#
# WHY THIS EXISTS
# ---------------
# IBM Cloud Databases hand out a DSN with `sslmode=verify-full` and ship the CA separately, as
# base64 in the service credentials. The events layer already carries it (`events/db.py::_ca_file`
# writes it to a temp file and appends `sslrootcert=`), but that only helps the events DB.
#
# CUGA's own config store — where `storage.mode=prod` puts agent configs, so that agents composed
# in the Studio survive an instance replace — goes through a different connector, which has no such
# handling. It connects during `cuga start demo`, BEFORE any application code runs, so nothing in
# Python can fix it in time. The container failed to start with:
#
#   root certificate file "/root/.postgresql/root.crt" does not exist or cannot be accessed
#
# That path is libpq's default lookup. Writing the CA there fixes every Postgres client in the
# image at once, without teaching CUGA core about an events environment variable.
#
# The tempting alternative is to downgrade the DSN to `sslmode=require`: still encrypted, but it
# stops verifying who is on the other end, so a man-in-the-middle reads every agent config and
# armed prompt. Not worth it to save six lines.
set -e

if [ -n "${EVENTS_DB_CA_B64:-}" ]; then
  CA_DIR="${HOME:-/root}/.postgresql"
  mkdir -p "$CA_DIR"
  if printf '%s' "$EVENTS_DB_CA_B64" | base64 -d > "$CA_DIR/root.crt" 2>/dev/null; then
    chmod 600 "$CA_DIR/root.crt"
    # Also exported explicitly: HOME can differ from the user libpq resolves, and a wrong guess
    # fails the same silent way.
    export PGSSLROOTCERT="$CA_DIR/root.crt"
    echo "entrypoint: database CA installed at $CA_DIR/root.crt"
  else
    echo "entrypoint: WARNING could not decode EVENTS_DB_CA_B64 — a verify-full DSN will fail" >&2
  fi
fi

exec "$@"
