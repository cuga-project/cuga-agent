#!/bin/sh
set -e

MODE="${CUGA_DEMO_MODE:-default}"

case "$MODE" in
  default)
    exec uv run cuga start manager --host 0.0.0.0
    ;;
  crm)
    exec uv run cuga start demo_crm --host 0.0.0.0 --cuga-workspace /app/cuga_workspace
    ;;
  digital_sales)
    exec uv run cuga start demo --host 0.0.0.0
    ;;
  *)
    echo "Unknown CUGA_DEMO_MODE=$MODE. Use: default, crm, or digital_sales"
    exit 1
    ;;
esac
