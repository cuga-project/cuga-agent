#!/bin/bash
# Launch the docs explorer. Requires: fastapi, uvicorn (pip install fastapi uvicorn).
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
echo "Starting docs explorer at http://localhost:$PORT  (Ctrl-C to stop)"
exec python3 -m uvicorn app:app --port "$PORT" --reload
