#!/bin/sh
set -e

MODE="${CUGA_DEMO_MODE:-default}"

# Use the cuga binary directly from the venv to avoid uv reinstalling workspace
# packages (which invalidates the bytecode cache and dramatically slows subprocess startup).
CUGA="/app/.venv/bin/cuga"

case "$MODE" in
  default)
    exec "$CUGA" start manager --host 0.0.0.0
    ;;
  crm)
    exec "$CUGA" start demo_crm --host 0.0.0.0 --cuga-workspace /app/cuga_workspace
    ;;
  digital_sales)
    exec "$CUGA" start demo --host 0.0.0.0 --digital-sales
    ;;
  health)
    exec "$CUGA" start demo_health --host 0.0.0.0
    ;;
  docs|demo_docs)
    exec "$CUGA" start demo_docs --host 0.0.0.0
    ;;
  knowledge|demo_knowledge)
    exec "$CUGA" start demo_knowledge --host 0.0.0.0
    ;;
  *)
    echo "Unknown CUGA_DEMO_MODE=$MODE. Use: default, crm, digital_sales, health, docs (or demo_docs), knowledge (or demo_knowledge)"
    exit 1
    ;;
esac
