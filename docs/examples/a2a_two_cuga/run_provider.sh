#!/usr/bin/env bash
# Provider CUGA — listens on :8002 and exposes the digital_sales toolset
# over A2A. The A2A inbound endpoint routes through the supervisor defined
# in provider.supervisor.yaml.
#
# Prereq: a registry must be running on :8001. Start one in another
# terminal with:  cuga start registry
#
# Once running:
#   curl http://localhost:8002/.well-known/agent.json | jq .name
#   # → "cuga-provider"

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Load .env and patch the saved cuga.db config — same shape as the
# consumer script. See _preflight.py for the rationale.
if [[ -f .env ]]; then
  set -a; source .env; set +a
fi
uv run --no-sync python docs/examples/a2a_two_cuga/_preflight.py

PROVIDER_PORT="${PROVIDER_PORT:-8002}"
REGISTRY_PORT="${REGISTRY_PORT:-8001}"

export DYNACONF_A2A__ENABLED=true
export DYNACONF_A2A__AGENT_NAME="cuga-provider"
export DYNACONF_A2A__AGENT_DESCRIPTION="CUGA exposing digital_sales tools over A2A."
export DYNACONF_A2A__AGENT_URL="http://localhost:${PROVIDER_PORT}"
export DYNACONF_A2A__SUPERVISOR_CONFIG_PATH="docs/examples/a2a_two_cuga/provider.supervisor.yaml"
export DYNACONF_A2A__SKILL_IDS='["digital_sales"]'

# Also enable the supervisor for the LOCAL chat flow on this same process.
# Even if you only ever hit the provider via /a2a, the LLM-mode toggle and
# graph-build path expect supervisor.enabled to be true alongside config_path.
export DYNACONF_SUPERVISOR__ENABLED=true
export DYNACONF_SUPERVISOR__CONFIG_PATH="docs/examples/a2a_two_cuga/provider.supervisor.yaml"
export DYNACONF_SERVER_PORTS__DEMO="${PROVIDER_PORT}"
export DYNACONF_SERVER_PORTS__REGISTRY="${REGISTRY_PORT}"

echo "Provider CUGA starting on http://localhost:${PROVIDER_PORT}"
echo "  AgentCard:    http://localhost:${PROVIDER_PORT}/.well-known/agent.json"
echo "  A2A endpoint: http://localhost:${PROVIDER_PORT}/a2a"
echo
exec uv run --no-sync uvicorn cuga.backend.server.main:app \
  --host 127.0.0.1 \
  --port "${PROVIDER_PORT}"
