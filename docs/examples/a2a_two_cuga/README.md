# Two CUGAs over A2A

Stand up two CUGA processes that talk to each other over the A2A v0.3
protocol. **CUGA-1 (consumer)** has no local tools. **CUGA-2 (provider)**
fronts the `digital_sales` toolset and exposes it over A2A. When you
chat with CUGA-1, it delegates every task across the wire to CUGA-2 and
returns the answer.

## Architecture

```text
       ┌──────────────────────────┐                  ┌──────────────────────────┐
 user  │  CUGA-1 (consumer)       │   A2A v0.3       │  CUGA-2 (provider)       │
 ──→   │  http://localhost:7860/  │  ─JSON-RPC──→    │  http://localhost:8002/  │
 chat  │  no local tools          │  /a2a            │  digital_sales toolset   │
       │  supervisor: 1 ext agent │                  │  A2A inbound enabled     │
       └──────────────────────────┘                  └──────────────────────────┘
                       ▲                                          ▲
                       │ shared                                   │
                       └─── http://localhost:8001 (registry) ─────┘
```

## Prerequisites

- An OpenAI key (or another LLM configured) — same as any CUGA run.
- `uv` available on `PATH`.

## Run

Open three terminals:

**Terminal 1 — registry**
```bash
cuga start registry
```
This serves the digital_sales OpenAPI tool catalog on `http://localhost:8001`.

**Terminal 2 — provider CUGA (port 8002)**
```bash
./docs/examples/a2a_two_cuga/run_provider.sh
```
Once it's up, sanity-check the A2A surface:
```bash
curl -s http://localhost:8002/.well-known/agent.json | jq .name
# → "cuga-provider"

curl -s -X POST http://localhost:8002/a2a \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send",
       "params":{"message":{"role":"user",
                            "parts":[{"kind":"text","text":"list my territory accounts"}],
                            "messageId":"m1"}}}' \
  | jq .result.status.state
# → "completed"
```

**Terminal 3 — consumer CUGA (port 7860)**
```bash
./docs/examples/a2a_two_cuga/run_consumer.sh
```

Now open **<http://localhost:7860/>** in a browser and chat:

> *List my top accounts by revenue*

The consumer has no tools of its own, so it routes the question through
its sole external agent (`provider`) over A2A. The provider's supervisor
runs `digital_sales` against the registry and the answer flows back to
the consumer chat UI.

## URLs

- **Chat UI (consumer):** http://localhost:7860/
- **Provider AgentCard:** http://localhost:8002/.well-known/agent.json
- **Provider A2A endpoint:** http://localhost:8002/a2a
- **Registry:** http://localhost:8001/

## How it works

- **`provider.supervisor.yaml`** — declares one internal agent
  (`digital_sales`) that pulls tools from the registry's `digital_sales`
  app. The provider CUGA is launched with `DYNACONF_A2A__ENABLED=true`
  and `DYNACONF_A2A__SUPERVISOR_CONFIG_PATH` pointing at this YAML, so
  inbound A2A requests are routed through this supervisor.

- **`consumer.supervisor.yaml`** — declares one external agent
  (`provider`) with `a2a_protocol.transport=http` and
  `endpoint=http://localhost:8002`. CUGA's existing supervisor flow
  fetches the provider's AgentCard at startup, surfaces it as a tool to
  the consumer, and uses `delegate_task_via_a2a_sdk` to POST each
  delegation as JSON-RPC to the provider's `/a2a`.

- **No auth** — both CUGAs run with `auth_required=false`. The
  auth-token forwarding test in the integration suite is `xfail` until
  v1.

## Troubleshooting

- **`HAS_A2A_SDK` is False** — you're on a stale checkout. Run
  `git pull && uv sync`. The fix to support `a2a-sdk==1.0.2` is in this
  same commit.
- **Provider returns "endpoint reached, but … is not yet wired"** —
  `DYNACONF_A2A__SUPERVISOR_CONFIG_PATH` was not set. Re-run
  `run_provider.sh`; it sets that env var.
- **Consumer can't find provider** — check the order: registry first,
  then provider, then consumer. The consumer's supervisor fetches the
  provider's AgentCard at startup; if the provider isn't running the
  consumer logs a warning and falls back to its YAML description.
