# Makefile — one-liners for the event-driven CUGA + Activepieces runtime.
# `make` or `make help` lists everything. Scripts live in scripts/; this just wraps them.

.DEFAULT_GOAL := help
SHELL := /bin/bash

PY        := .venv/bin/python
DOCKER    := $(shell command -v podman || command -v docker)
IMAGE     ?= cuga-compliance-poc:dev
DB        := events.db .events.db
VOLS      := ap_pgdata ap_redis
CUGA_PORT := 7860

# env-check: required must be present+non-empty; optional just reported.
REQUIRED := LLM_PROVIDER LLM_MODEL AGENT_SETTING_CONFIG \
            WATSONX_APIKEY WATSONX_URL WATSONX_PROJECT_ID \
            AP_BASE_URL AP_EMAIL AP_PASSWORD \
            EVENTS_DB EVENTS_ENABLED GATEWAY_TOKEN
OPTIONAL := EVENTS_PUBLIC_URL TELEGRAM_BOT_TOKEN SLACK_BOT_TOKEN \
            DISCORD_BOT_TOKEN BOX_DEV_TOKEN GITHUB_TOKEN

.PHONY: help env-check preflight doctor ap cuga up start stop restart reload nuke fresh status public-url flows tunnels tunnels-up tunnels-down logs channels channels-status test test-all bench test-live test-suite-now test-suite-flows test-matrix test-fire test-report report api-spec sync compliance-poc compliance-poc-stop compliance-poc-status compliance-poc-image compliance-poc-image-multiarch compliance-poc-image-run

help: ## Show this help
	@echo "CUGA event-runtime — make targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

## ---- start / stop ---------------------------------------------------------
ap: ## Start Activepieces (app + postgres + redis + tunnel)
	scripts/ap_up.sh

ap-pieces: ## Ensure AP has the integration pieces installed (fixes fresh-DB "piece_metadata_not_found")
	@$(PY) scripts/ap_pieces.py

cuga: ## Provision infra (MCP registry + tunnels) then boot `cuga start … --events`
	scripts/events_up.sh

up: preflight ap cuga ## Full dev stack: Activepieces + infra + `cuga start … --events` (one server)
	@echo "✓ stack up.   → NEXT: make status"

start: up ## Alias for `up`. Bare server (no AP/tunnels): `cuga start demo --events`

compliance-poc: ## Start the local Activepieces + Evolve + CUGA memory-compliance PoC
	scripts/compliance_poc.sh

compliance-poc-stop: ## Stop CUGA and Evolve processes started by compliance-poc
	scripts/compliance_poc.sh --stop

compliance-poc-status: ## Report local compliance PoC service status
	scripts/compliance_poc.sh --status

compliance-poc-image: ## Build the single-image UBI9 init compliance PoC
	$(DOCKER) build -f Dockerfile.compliance-poc -t $(IMAGE) .

compliance-poc-image-multiarch: ## Build and push amd64+arm64 PoC images as one manifest
	$(DOCKER) buildx build --platform linux/amd64,linux/arm64 \
	  -f Dockerfile.compliance-poc -t $(IMAGE) --push .

compliance-poc-image-run: ## Run the single-image PoC locally with persistent data
	$(DOCKER) run --rm --name cuga-compliance-poc --privileged --cgroupns=host \
	  -p 7860:7860 -p 8081:8081 -p 8201:8201 \
	  -v cuga-compliance-poc-data:/data --env-file .env $(IMAGE)

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
	@echo "   data is gone — pieces + AP connections + tunnels will be rebuilt on next start."
	@echo "   → NEXT: make fresh   # nuke-safe full cycle (or 'make up' to just start the stack)"

reset-flows: ## Wipe ONLY CUGA's flow DB (events.db) + bounce CUGA — keeps AP connections/pieces/tunnels (no reconnect)
	-scripts/events_up.sh --stop
	-rm -f $(DB)
	@$(MAKE) --no-print-directory cuga
	@echo "✅ flows reset: $(DB) wiped, CUGA restarted. AP connections + pieces + tunnels untouched — no reconnect needed."

fresh: ## FULL from-scratch cycle: nuke → up (fresh AP+CUGA) → arm channels → print public URL
	@echo "== 1/4 env-check =="; $(MAKE) --no-print-directory env-check
	@echo "== 2/4 nuke =="; $(MAKE) --no-print-directory nuke
	@echo "== 3/4 up (fresh AP + CUGA) =="; $(MAKE) --no-print-directory up
	@echo "== 4/4 arm channels =="; $(MAKE) --no-print-directory channels
	@scripts/events_up.sh --public-url
	@echo; echo "✅ Fresh stack up. Now do these IN ORDER:"; \
	  echo "   1. make status              # 2 servers 200 · 3 containers Up · tunnel URLs"; \
	  echo "   2. make doctor              # creds green — paste a FRESH BOX_DEV_TOKEN, then: make reload"; \
	  echo "   3. make test                # offline gate — all green"; \
	  echo "   4. CONNECT integrations     # open http://localhost:7860/studio → Integrations → Connect"; \
	  echo "                               #   gmail · github · box · google_calendar · pinterest  (youtube/rss = ready)"; \
	  echo "   5. make test-live           # 4 channels + 4 flow modes"; \
	  echo "   6. make test-exhaustive     # full arm+FIRE matrix → results/exhaustive_<ts>.html"; \
	  echo; echo "   → NEXT: make status"

## ---- inspect --------------------------------------------------------------
status: ## Show what's running + tunnel URLs + every channel & integration
	@scripts/events_up.sh --status
	@echo "--- containers ---"
	@$(DOCKER) ps --filter name=activepieces --filter name=ap-postgres --filter name=ap-redis \
	  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true
	@echo "--- channels (inbound chat) ---"
	@scripts/arm_channels.sh --status 2>/dev/null || echo "  (CUGA not reachable — is the stack up?)"
	@echo "--- AP pieces (integration Connect needs these installed) ---"
	@$(PY) scripts/ap_pieces.py --status 2>/dev/null | sed 's/^/  /' \
	  || echo "  (AP not reachable — run: make ap-pieces)"
	@echo "--- integrations (watch/act) ---"
	@curl -s --max-time 5 localhost:$(CUGA_PORT)/api/events/integrations 2>/dev/null \
	  | CUGA_PORT=$(CUGA_PORT) python3 -c "import sys,json,os;\
rows=json.load(sys.stdin).get('integrations',[]);\
port=os.environ.get('CUGA_PORT','7860');\
mark=lambda i: '✓ connected' if i.get('connected') else ('· ready' if not i.get('needs_connection') else '✗ connect needed');\
[print(f\"  {i['name']:<17} {mark(i):<15} {i.get('auth','?'):<6} {i.get('backend','?')}\") for i in rows];\
need=[i['name'] for i in rows if i.get('needs_connection') and not i.get('connected')];\
print() or print(f\"  ⚠ {len(need)} need connecting: {', '.join(need)}\") if need else None;\
print(f\"    → Open the CUGA Studio and click Connect:  http://localhost:{port}/studio  → Integrations tab\") if need else None;\
print(f\"      (each opens a browser OAuth consent; youtube/rss need nothing — they show 'ready')\") if need else None" 2>/dev/null \
	  || echo "  (CUGA not reachable — is the stack up?)"
	@echo "   → NEXT (setup): make doctor"

public-url: ## Print the current public URL + the exact Slack/Gmail strings to update
	@scripts/events_up.sh --public-url

flows: ## Open the Flows console (list · pause/resume · delete · rich flow view)
	@echo "Flows console → http://localhost:$(CUGA_PORT)/api/events/flows/console"
	@open "http://localhost:$(CUGA_PORT)/api/events/flows/console" 2>/dev/null || true

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
	@echo "   → NEXT: make status   (then: make doctor → make test → CONNECT integrations in the Studio)"

channels-status: ## Show inbound-channel state without changing anything
	@scripts/arm_channels.sh --status

## ---- tests / env ----------------------------------------------------------
test: ## Offline events suite — the fast green gate (~60, no stack/creds)
	$(PY) -m pytest tests/events -q

test-all: ## All OFFLINE tests (events + unit; no live stack). NB: some tests/unit are pre-existing product failures.
	$(PY) -m pytest tests/events tests/unit -q

bench: ## NL→Flow benchmark scorecard (offline, deterministic; gates CORRECT_AT_ARM==100%)
	$(PY) -m pytest tests/events/test_nl_to_flow_bench.py tests/events/test_flowspec_bench.py -q -s

test-live: ## Live e2e — 4 channels + 4 flow modes. Needs the stack up (make up) + creds (make doctor)
	EVENTS_SERVER_URL=http://localhost:$(CUGA_PORT) $(PY) tests/events/live_e2e.py $(ARGS)
	@echo "   → NEXT: make test-exhaustive   (the full arm+FIRE matrix → HTML report)"

test-suite-now: ## Live suite — every seeded agent, invoked directly (asserts on meta.mcp)
	EVENTS_SERVER_URL=http://localhost:$(CUGA_PORT) $(PY) tests/events/live_suite.py --only now $(ARGS)

test-suite-flows: ## Live suite — cron + poll + push (English sentence → the right AP flow)
	EVENTS_SERVER_URL=http://localhost:$(CUGA_PORT) $(PY) tests/events/live_suite.py --only flows $(ARGS)

test-matrix: ## Live matrix — every trigger mode × every channel sink × every integration (~5 min)
	EVENTS_SERVER_URL=http://localhost:$(CUGA_PORT) $(PY) tests/events/live_matrix.py $(ARGS)

test-fire: ## Live FIRE — arm a 1-min schedule, WAIT for a real tick, read back the answer (~9 min)
	EVENTS_SERVER_URL=http://localhost:$(CUGA_PORT) $(PY) tests/events/live_fire.py $(ARGS)

test-report: ## Run EVERY harness in order → one timestamped, commit-stamped report, md + html (~40 min)
	$(PY) scripts/run_all_tests.py $(ARGS)

report: ## Open the latest HTML report (results/index.html) — does not run anything
	@test -f results/index.html || { echo "no report yet — run: make test-report"; exit 1; }
	@open results/index.html 2>/dev/null || echo "results/index.html"

api-spec: ## Regenerate events_docs/api/api_spec.html and SERVE it (Try-it needs http://, not file://)
	@$(PY) scripts/gen_api_spec.py
	@echo "API spec → http://localhost:8123/api_spec.html   (ctrl-c to stop)"
	@open "http://localhost:8123/api_spec.html" 2>/dev/null || true
	@$(PY) -m http.server 8123 --directory events_docs/api

slides: ## Regenerate events_docs/slides.html (the event-driven-agents deck) and open it
	@$(PY) scripts/gen_slides.py
	@open events_docs/slides.html 2>/dev/null || echo "→ events_docs/slides.html"

test-delegation: ## LIVE: the supervisor's routing accuracy over the real 27-agent roster (~10 min)
	$(PY) tests/events/live_delegation_bench.py


doctor: ## Live credential doctor — hit each service with its real .env cred
	-$(PY) tests/events/preflight.py
	@echo; echo "--- Activepieces pieces (integration Connect needs these) ---"; \
	  $(PY) scripts/ap_pieces.py --status 2>/dev/null || echo "  (AP down — start it with 'make ap')"
	@echo; echo "   → NEXT (setup): make test  (then CONNECT integrations in the Studio → make test-live)"

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

preflight: ## Check the TOOLS `make up` needs are installed & running — fails LOUD with fixes (run before make up)
	@ok=1; \
	if command -v podman >/dev/null 2>&1; then \
	  podman info >/dev/null 2>&1 || { echo "✗ podman installed but its VM isn't running — run: podman machine start"; ok=0; }; \
	elif command -v docker >/dev/null 2>&1; then \
	  docker info >/dev/null 2>&1 || { echo "✗ docker installed but not running — start Docker Desktop"; ok=0; }; \
	else echo "✗ no container runtime — brew install podman && podman machine init && podman machine start"; ok=0; fi; \
	command -v cloudflared >/dev/null 2>&1 || { echo "✗ cloudflared missing — brew install cloudflared"; ok=0; }; \
	command -v uv >/dev/null 2>&1 || { echo "✗ uv missing — brew install uv, then: uv sync --python 3.12"; ok=0; }; \
	test -x .venv/bin/python || { echo "✗ no .venv — run: uv sync --python 3.12"; ok=0; }; \
	dom=$$(grep -E '^EVENTS_NGROK_DOMAIN=' .env 2>/dev/null | tail -1 | cut -d= -f2- | sed -e 's/[[:space:]]*#.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//'); \
	if [ -n "$$dom" ]; then command -v ngrok >/dev/null 2>&1 || { echo "✗ ngrok missing but EVENTS_NGROK_DOMAIN=$$dom is set — brew install ngrok, verify email, reserve the domain (dashboard.ngrok.com)"; ok=0; }; \
	  else echo "· EVENTS_NGROK_DOMAIN unset — CUGA will use an EPHEMERAL cloudflared tunnel (URL changes each run; re-point Slack/OAuth). ngrok is recommended — see setup/NGROK.md"; fi; \
	command -v node >/dev/null 2>&1 || echo "· node missing — only needed to build the Studio UI (scripts/frontend_build.sh), NOT to run the stack"; \
	if [ $$ok = 1 ]; then echo "✓ tools present.   → NEXT: make up"; else echo; echo "Fix the ✗ item(s) above, then re-run \`make preflight\`. (Full list: events_docs/SETUP.md → Prerequisites.)"; exit 1; fi

test-exhaustive: ## EXHAUSTIVE live matrix — every agent/trigger/channel, arm+FIRE+answer-verified (~45-75 min)
	$(PY) tests/events/live_exhaustive.py $(ARGS)

test-new-pieces: ## Live sweep of the newer pieces — Calendar/Pinterest/YouTube/RSS/Discord (arm + synth-fire, ~3 min)
	EVENTS_SERVER_URL=http://localhost:7860 $(PY) tests/events/live_new_pieces.py $(ARGS)
