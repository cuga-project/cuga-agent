# Two CUGAs over ACP

Stand up two CUGA processes that talk to each other over the
[i-am-bee ACP](https://github.com/i-am-bee/acp) protocol
(SDK version 1.0.3, pinned).
**CUGA-1 (consumer)** has no local tools. **CUGA-2 (provider)**
exposes an ACP inbound server at `/acp`. When you chat with CUGA-1,
it delegates every task across the wire to CUGA-2 and returns the
answer.

> **Protocol note:** ACP (Agent Communication Protocol) originated in
> the [i-am-bee](https://github.com/i-am-bee) project and has since
> merged into the [Agent2Agent (A2A)](https://google.github.io/A2A/)
> specification. CUGA's ACP support targets `acp-sdk==1.0.3` and the
> wire format described by that release. The A2A protocol (separate from
> ACP) is also supported natively; see `docs/examples/a2a_two_cuga/`.

## Architecture

```text
       ┌──────────────────────────┐                  ┌──────────────────────────┐
 user  │  CUGA-1 (consumer)       │   ACP 1.0.3      │  CUGA-2 (provider)       │
 ──→   │  http://localhost:7860/  │  ─async-poll──→  │  http://localhost:8002/  │
 chat  │  no local tools          │  /acp            │  digital_sales toolset   │
       │  supervisor: 1 ext agent │                  │  ACP inbound enabled     │
       └──────────────────────────┘                  └──────────────────────────┘
                       ▲                                          ▲
                       │ shared                                   │
                       └─── http://localhost:8001 (registry) ─────┘
```

## Prerequisites

- Python 3.11 or newer (the `acp-sdk` optional extra requires ≥ 3.11).
- An OpenAI key (or another LLM configured) — same as any CUGA run.
- `uv` available on `PATH`.

### Install the ACP extra

```bash
pip install "cuga[acp]"
# — or, inside the repository —
uv sync --extra acp
```

The `[acp]` extra pulls in `acp-sdk>=1.0.3,<2` and pins `uvicorn<0.36`
to resolve a compatibility issue with `uvicorn==0.36.0`.
Base CUGA (Python 3.10+) remains installable without this extra.

## Token setup

The consumer reads the provider's bearer token from an environment
variable at **invocation time** (not at YAML-load time), so credential
rotation does not require a restart.

Set the variable before starting the consumer — use a placeholder
value when no auth is required on the provider:

```bash
# If the provider runs with auth_required=false (see below), any
# non-empty value works, or omit the variable entirely.
export PROVIDER_ACP_TOKEN="<your-token-here>"
```

> **Do not store real tokens in configuration files or commit them to
> version control.**  `token_env_var` names the environment variable;
> the token value is never written to disk.

## Run

Open **three** terminals:

### Terminal 1 — registry

```bash
cuga start registry
```

This serves the `digital_sales` OpenAPI tool catalog on
`http://localhost:8001`.

### Terminal 2 — provider CUGA (port 8002)

```bash
# Enable ACP inbound on the provider.
export DYNACONF_ACP__ENABLED=true
export DYNACONF_ACP__AGENT_NAME="cuga"
export DYNACONF_ACP__AGENT_DESCRIPTION="CUGA provider exposed over ACP."
export DYNACONF_ACP__AUTH_REQUIRED=false          # set true and add tokens in production
export DYNACONF_SUPERVISOR__ENABLED=true
export DYNACONF_SUPERVISOR__CONFIG_PATH="docs/examples/acp_two_cuga/provider.supervisor.yaml"
export DYNACONF_SERVER_PORTS__DEMO=8002

uv run --no-sync uvicorn cuga.backend.server.main:app \
  --host 127.0.0.1 \
  --port 8002
```

Verify the ACP surface is live:

```bash
# Agent listing
curl -s http://localhost:8002/acp/agents | python3 -m json.tool

# Synchronous run
curl -s -X POST http://localhost:8002/acp/runs \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_name": "cuga",
    "input": [{"role": "user", "parts": [{"content_type": "text/plain", "content": "hello"}]}],
    "mode": "sync"
  }' | python3 -m json.tool
```

Expected (abbreviated):

```json
{
  "run": {
    "agent_name": "cuga",
    "status": "completed",
    "output": [
      {
        "role": "agent",
        "parts": [{"content_type": "text/plain", "content": "Hello! How can I help you?"}]
      }
    ]
  }
}
```

### Terminal 3 — consumer CUGA (port 7860)

```bash
export PROVIDER_ACP_TOKEN=""    # provider runs with auth_required=false in this example
export DYNACONF_SUPERVISOR__ENABLED=true
export DYNACONF_SUPERVISOR__CONFIG_PATH="docs/examples/acp_two_cuga/consumer.supervisor.yaml"
export DYNACONF_SERVER_PORTS__DEMO=7860

uv run --no-sync uvicorn cuga.backend.server.main:app \
  --host 127.0.0.1 \
  --port 7860
```

Now open **<http://localhost:7860/>** in a browser and chat:

> *List my top accounts by revenue*

The consumer has no tools of its own, so it routes the question through
its sole external agent (`provider`) over ACP. The provider's supervisor
runs `digital_sales` against the registry and the answer flows back to
the consumer chat UI.

## URLs

- **Chat UI (consumer):** http://localhost:7860/
- **Provider ACP agents:** http://localhost:8002/acp/agents
- **Provider ACP runs:** http://localhost:8002/acp/runs
- **Registry:** http://localhost:8001/

## How it works

- **`provider.supervisor.yaml`** (in `docs/examples/a2a_two_cuga/`) —
  declares one internal agent (`digital_sales`) that pulls tools from
  the registry. The provider CUGA is launched with
  `DYNACONF_ACP__ENABLED=true` and the supervisor config pointing at
  that YAML, so inbound ACP requests are routed through the supervisor.

- **`consumer.supervisor.yaml`** — declares one external agent
  (`provider`) with `acp_protocol.enabled=true` and
  `endpoint=http://localhost:8002/acp`. CUGA's supervisor fetches
  the provider's `AgentManifest` at startup, surfaces it as a
  delegation tool, and uses `delegate_task_via_acp()` to submit each
  delegation as an async ACP run and poll it to completion.

- **Token flow** — `auth.token_env_var: PROVIDER_ACP_TOKEN` tells the
  outbound wrapper which environment variable holds the bearer token.
  The value is resolved at call time and injected as
  `Authorization: Bearer <token>`. It is never logged or stored.

## Known limitations

| Limitation | Notes |
|---|---|
| **Text-only input/output** | ACP input parts must be `text/plain` with inline `content`. URL-backed parts (`content_url`) and non-text MIME types are rejected. |
| **No outbound HITL** | When the consumer delegates to a provider, interactive approval prompts (`awaiting` runs) are unsupported. The outbound wrapper returns a failure result rather than stalling the supervisor. In-process HITL on the provider side (direct-agent mode) works normally. |
| **Memory store only** | This release uses an in-memory ACP session store. Sessions are lost on provider restart. A persistent store is planned for a future release. |
| **Single worker** | The in-memory store is not shared across multiple Uvicorn workers. Start the provider with a single worker (`--workers 1`, the default). |

## Troubleshooting

- **`ImportError: No module named 'acp_sdk'`** — you need
  `cuga[acp]` installed (`uv sync --extra acp`). Check that Python
  is 3.11 or newer: `python --version`.
- **`GET /acp/agents` returns 404** — `DYNACONF_ACP__ENABLED` was not
  exported before starting the provider. Re-run with the export.
- **Consumer can't reach provider** — check the startup order: registry
  first, then provider, then consumer. The consumer supervisor fetches
  the provider's manifest at startup; if the provider isn't up yet, the
  consumer logs a warning and falls back to the YAML description.
- **Authentication errors (401)** — if `AUTH_REQUIRED=true` on the
  provider, `PROVIDER_ACP_TOKEN` must be set on the consumer side and
  match the token accepted by CUGA's `require_chat_access` policy.
