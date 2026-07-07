# Makefile — one-liners for the event-driven CUGA + Activepieces runtime.
# `make` or `make help` lists everything. Scripts live in scripts/; this just wraps them.

.DEFAULT_GOAL := help
SHELL := /bin/bash

PY        := .venv/bin/python
DOCKER    := $(shell command -v podman || command -v docker)
DB        := events.db .events.db
VOLS      := ap_pgdata ap_redis
CUGA_PORT := 8100

# env-check: required must be present+non-empty; optional just reported.
REQUIRED := LLM_PROVIDER LLM_MODEL AGENT_SETTING_CONFIG \
            WATSONX_APIKEY WATSONX_URL WATSONX_PROJECT_ID \
            AP_BASE_URL AP_EMAIL AP_PASSWORD \
            EVENTS_DB EVENTS_ENABLED GATEWAY_TOKEN
OPTIONAL := EVENTS_PUBLIC_URL TELEGRAM_BOT_TOKEN SLACK_BOT_TOKEN \
            DISCORD_BOT_TOKEN BOX_DEV_TOKEN GITHUB_TOKEN

.PHONY: help env-check doctor ap cuga up start stop restart reload nuke fresh status public-url tunnels tunnels-up tunnels-down logs channels channels-status test test-all test-live sync

help: ## Show this help
	@echo "CUGA event-runtime — make targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

## ---- start / stop ---------------------------------------------------------
ap: ## Start Activepieces (app + postgres + redis + tunnel)
	scripts/ap_up.sh

cuga: ## Start CUGA server + MCP registry + tunnels
	scripts/events_up.sh

up: ap cuga ## Start both (AP first, then CUGA)

start: up ## Alias for `up`

stop: ## Stop everything (AP + CUGA + tunnels), keep data
	-scripts/ap_up.sh --stop
	-scripts/events_up.sh --stop

restart: stop up ## Stop then start both (NB: new tunnel URLs — re-point EVENTS_PUBLIC_URL after)

reload: ## Bounce ONLY the CUGA server (pick up .env/code) — keeps AP + tunnels, URLs unchanged
	scripts/events_up.sh --reload

nuke: stop ## Stop everything AND wipe all data (AP volumes + events.db)
	-$(DOCKER) volume rm $(VOLS)
	-rm -f $(DB)
	@echo "💥 nuked: $(VOLS) + $(DB) (ea-postgres / cuga-agent-apps NOT touched)"

fresh: ## FULL from-scratch cycle: nuke → up (fresh AP+CUGA) → arm channels → print public URL
	@echo "== 1/4 env-check =="; $(MAKE) --no-print-directory env-check
	@echo "== 2/4 nuke =="; $(MAKE) --no-print-directory nuke
	@echo "== 3/4 up (fresh AP + CUGA) =="; $(MAKE) --no-print-directory up
	@echo "== 4/4 arm channels =="; $(MAKE) --no-print-directory channels
	@echo; echo "✅ fresh stack up. Verify with: make status · make test-live"
	@scripts/events_up.sh --public-url

## ---- inspect --------------------------------------------------------------
status: ## Show what's running + tunnel URLs
	@scripts/events_up.sh --status
	@echo "--- containers ---"
	@$(DOCKER) ps --filter name=activepieces --filter name=ap-postgres --filter name=ap-redis \
	  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true

public-url: ## Print the current public URL + the exact Slack/Gmail strings to update
	@scripts/events_up.sh --public-url

tunnels: ## Status of both public tunnel agents (AP cloudflared + CUGA ngrok/cloudflared)
	@scripts/tunnels.sh --status

tunnels-up: ## (Re)start any DOWN tunnel agent (ngrok CUGA is safe; AP needs `make ap`)
	@scripts/tunnels.sh --up

tunnels-down: ## Stop both tunnel agents (cloudflared + ngrok)
	@scripts/tunnels.sh --down

logs: ## Tail the runtime logs
	@tail -n 40 -F /tmp/events_up/*.log

channels: ## Connect + arm every inbound chat channel that has a token in .env (needs the stack up)
	scripts/arm_channels.sh

channels-status: ## Show inbound-channel state without changing anything
	@scripts/arm_channels.sh --status

## ---- tests / env ----------------------------------------------------------
test: ## Offline events suite — the fast green gate (~60, no stack/creds)
	$(PY) -m pytest tests/events -q

test-all: ## All OFFLINE tests (events + unit; no live stack). NB: some tests/unit are pre-existing product failures.
	$(PY) -m pytest tests/events tests/unit -q

test-live: ## Live e2e — needs the stack up (make up) + creds (make doctor)
	EVENTS_SERVER_URL=http://localhost:$(CUGA_PORT) $(PY) tests/events/live_integrations_e2e.py

doctor: ## Live credential doctor — hit each service with its real .env cred
	$(PY) tests/events/preflight.py

sync: ## uv sync the venv
	uv sync --python 3.12

env-check: ## Check .env has the required keys (offline, no network)
	@test -f .env || { echo "✗ no .env — run: cp .env.example .env"; exit 1; }
	@miss=0; \
	get() { grep -E "^$$1=" .env | tail -1 | cut -d= -f2- | sed -e 's/[[:space:]]*#.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' -e 's/^"//' -e 's/"$$//'; }; \
	echo "required:"; \
	for k in $(REQUIRED); do v=$$(get $$k); \
	  if [ -z "$$v" ]; then echo "  ✗ $$k — missing/empty"; miss=1; else echo "  ✓ $$k"; fi; done; \
	echo "optional:"; \
	for k in $(OPTIONAL); do v=$$(get $$k); \
	  if [ -z "$$v" ]; then echo "  · $$k — not set"; else echo "  ✓ $$k"; fi; done; \
	if [ $$miss -eq 0 ]; then echo ".env looks good ✅"; \
	  else echo ".env is missing required keys ✗ (see above)"; exit 1; fi
