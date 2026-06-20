#!/usr/bin/env bash
# Consumer CUGA — listens on :7860 (the standard CUGA chat-UI port) and
# delegates EVERY task to the provider on :8002 via A2A. Has no local
# tools.
#
# Prereqs:
#   1. Registry running on :8001:           cuga start registry
#   2. Provider CUGA running on :8002:      docs/examples/a2a_two_cuga/run_provider.sh
#
# Once running, open the chat UI:
#   http://localhost:7860/

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Load .env (MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL, …). Bash doesn't
# do this automatically; the Python startup also reads .env, but the saved
# DB config can later wipe MODEL_NAME from os.environ — see _preflight.py.
if [[ -f .env ]]; then
  set -a; source .env; set +a
fi

# Patch the saved cuga.db row so force_env doesn't pop MODEL_NAME at
# startup. Idempotent — safe to run on every launch.
uv run --no-sync python docs/examples/a2a_two_cuga/_preflight.py

CONSUMER_PORT="${CONSUMER_PORT:-7860}"
REGISTRY_PORT="${REGISTRY_PORT:-8001}"

# The consumer is just a regular CUGA backend whose supervisor is wired
# to a single external A2A agent. We do NOT enable the A2A inbound
# endpoint here — the consumer is purely a client.
#
# Both flags are required: ENABLED=true flips DynamicAgentGraph into
# supervisor mode; CONFIG_PATH selects the YAML that declares the
# external A2A agent.
export DYNACONF_A2A__ENABLED=false
export DYNACONF_SUPERVISOR__ENABLED=true
export DYNACONF_SUPERVISOR__CONFIG_PATH="docs/examples/a2a_two_cuga/consumer.supervisor.yaml"
export DYNACONF_SERVER_PORTS__DEMO="${CONSUMER_PORT}"
export DYNACONF_SERVER_PORTS__REGISTRY="${REGISTRY_PORT}"

echo "Consumer CUGA starting on http://localhost:${CONSUMER_PORT}"
echo "  Open this in a browser to chat:  http://localhost:${CONSUMER_PORT}/"
echo "  Delegating to provider at:       http://localhost:8002"
echo
exec uv run --no-sync uvicorn cuga.backend.server.main:app \
  --host 127.0.0.1 \
  --port "${CONSUMER_PORT}"
