#!/usr/bin/env bash
# Compare Langfuse callback propagation tests on current branch vs main.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Create .venv first (see project README)."
  exit 1
fi

echo "=== Langfuse callback propagation regression (current branch) ==="
"$PY" -m pytest tests/unit/test_langfuse_tracing.py -v --tb=line

echo ""
echo "=== Optional: run on main to see failures (module + nested paths missing) ==="
echo "  git stash -u && git checkout main && $PY -m pytest tests/unit/test_langfuse_tracing.py -v || true"
echo "  git checkout - && git stash pop"
