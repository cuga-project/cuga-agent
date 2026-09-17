# ACP (Agent Communication Protocol) Support Plan

## Overview

Add inbound and outbound support for the **Agent Communication Protocol maintained by i-am-bee**. This is not the unrelated Agent Client Protocol used by editors.

1. **Inbound** — expose CUGA as an ACP agent through the official ACP REST API.
2. **Outbound** — let a CUGA supervisor discover and delegate to external ACP agents.

Use the official [`acp-sdk`](https://pypi.org/project/acp-sdk/) instead of recreating ACP wire models, routing, streaming, run state, sessions, resume, cancellation, or client behavior.

The target for the initial implementation is:

- `acp-sdk>=1.0.3,<2`
- The API implemented by that SDK, based on ACP OpenAPI 0.2.0
- Python 3.11 or newer for the optional ACP feature
- Text-only CUGA input and output (`text/plain`) in the first release

ACP has been merged into A2A under the Linux Foundation. This feature is therefore a compatibility integration for existing ACP peers. Keep it opt-in and isolated from CUGA's existing A2A implementation.

## Verified SDK capabilities and constraints

`acp-sdk` 1.0.3 is the official Python package from the i-am-bee ACP repository. It provides:

- Pydantic wire models under `acp_sdk.models`
- An asynchronous client under `acp_sdk.client.Client`
- An agent abstraction and decorator under `acp_sdk.server`
- A FastAPI application factory, `acp_sdk.server.create_app`
- `sync`, `async`, and `stream` run modes
- Agent discovery, run lookup, event history, sessions, resume, and cancellation
- In-memory, PostgreSQL, and Redis stores

Important constraints:

- The package declares `requires-python = ">=3.11,<4.0"`.
- CUGA currently declares Python `>=3.10,<3.15`; ACP must therefore be an optional extra with a Python marker rather than an unconditional dependency.
- An isolated import check found that `acp-sdk==1.0.3` can fail with newer Uvicorn versions because it references `uvicorn.config.LoopSetupType`. The implementation must establish a compatible dependency range or upstream-fixed ACP release before adding the dependency.
- `create_app()` returns a complete FastAPI application, not an `APIRouter`. Its lifespan initializes execution resources. Mounting it as a sub-application must be validated because mounted-app lifespan behavior may not initialize the ACP executor automatically.

## Scope

### Included

- Official ACP models, client, server lifecycle, streaming, resume, cancellation, and storage behavior through the SDK
- One configured inbound CUGA agent
- Text input/output
- In-memory run storage by default
- Optional Redis or PostgreSQL store only if the SDK configuration can be exposed without adding custom persistence code
- Bearer/JWT protection through CUGA's existing chat-access dependency
- External ACP sub-agent configuration in supervisor YAML and stored supervisor configuration
- Unit and in-process integration tests

### Not included in the first release

- Reimplementation or vendoring of ACP wire models
- Remote `content_url` fetching
- Binary/base64 or multimodal input
- Custom ACP extensions for CUGA variables
- Durable run migration between CUGA versions
- Guaranteed high availability with the default in-memory store
- Changes to existing A2A wire behavior

## Protocol contract

The SDK owns the exact request, response, error, and event schemas. CUGA must not introduce alternate camelCase models or agent-scoped run URLs.

Expected endpoints relative to the configured ACP base path are:

- `GET /ping`
- `GET /agents`
- `GET /agents/{name}`
- `POST /runs`
- `GET /runs/{run_id}`
- `POST /runs/{run_id}` to resume an awaiting run
- `POST /runs/{run_id}/cancel`
- `GET /runs/{run_id}/events` for JSON event history
- `GET /sessions/{session_id}`
- SDK resource routes when resource forwarding is enabled

Streaming happens on `POST /runs` and resume `POST /runs/{run_id}` when the request mode is `stream`. `GET /runs/{run_id}/events` returns stored JSON events; it is not the live SSE endpoint.

ACP uses snake_case fields and the statuses `created`, `in-progress`, `awaiting`, `cancelling`, `cancelled`, `completed`, and `failed`.

## Architecture

### Inbound

Create a small CUGA adapter implementing the SDK's agent interface. The adapter:

1. Accepts `list[acp_sdk.models.Message]` and an SDK `Context`.
2. Validates that every consumed part is inline, plain `text/plain` content.
3. Joins accepted user text deterministically while preserving message and part order.
4. Maps the ACP session identifier to the CUGA thread identifier.
5. Calls a protocol-neutral CUGA runner.
6. Yields SDK-native `Message`, `MessagePart`, strings, or `AwaitRequest` values.
7. Accepts `AwaitResume` values when the SDK resumes the generator and maps them to CUGA's HITL approval shape.

The SDK executor then owns run IDs, status transitions, event generation, mode handling, history, cancellation signals, and persistence.

Do not import `A2AStreamEvent` from the A2A package. Extract the minimal event DTO and runner protocol into a neutral server module, then make the A2A and ACP adapters consume it. Existing A2A behavior must remain unchanged and covered by existing tests.

### SDK app integration

Build the ACP child application with `acp_sdk.server.create_app()` and mount it under a configurable prefix, default `/acp`. External clients use that prefix as their ACP base URL.

Before committing to this mount strategy, complete a compatibility spike that proves:

- The selected ACP/FastAPI/Uvicorn versions import together on supported Python versions.
- The mounted SDK app's lifespan initializes and shuts down exactly once under CUGA's parent lifespan.
- Async and sync runs work through the mounted app.
- CUGA's authentication dependency executes for all ACP routes.
- CUGA's existing CORS middleware remains authoritative; disable the SDK playground CORS middleware by default.
- OpenAPI route conflicts and operation IDs do not affect the parent app.

If normal mounting does not run the child lifespan, explicitly enter the child lifespan from CUGA's parent lifespan. Do not copy SDK routes as the first response. If lifecycle integration still cannot be made reliable, fall back to using SDK models and client with a thin CUGA-owned router and record the reason in this plan before implementation continues.

### Storage

Use the SDK `MemoryStore` initially, with configurable capacity and TTL. Document that:

- Runs and sessions disappear on process restart.
- State is not shared across multiple workers.
- A deployment using multiple workers must configure an SDK Redis or PostgreSQL store before ACP async/resume/cancellation can be considered reliable.

Expose only SDK-supported store settings. Never build a second CUGA run registry around the SDK.

### Outbound

Wrap `acp_sdk.client.Client`; do not make raw ACP HTTP calls.

The outbound adapter performs discovery with `Client.agent()`, sends text with `Client.run_sync()` by default, and extracts text from the returned run output. Keep the wrapper small so it can:

- Construct auth headers without logging secrets.
- Configure timeout, TLS verification, redirects, and base URL.
- Normalize SDK exceptions to CUGA's delegation result shape.
- Cancel a remote run if local execution is cancelled and a run ID is available.
- Explicitly reject an `awaiting` result in the first release unless an end-to-end supervisor resume design is added.

Do not send CUGA variables as an undocumented ACP field. The initial ACP delegation tool accepts only `task`. Variables remain available for internal and A2A agents but are not exposed for ACP until a namespaced interoperability extension is designed.

## Configuration

### Dependency

Add an optional dependency in `pyproject.toml` equivalent to:

```toml
acp = ["acp-sdk>=1.0.3,<2; python_version >= '3.11'"]
```

The exact ACP and Uvicorn constraints must come from the compatibility spike. Installation documentation must use `cuga[acp]`. If ACP is enabled without the extra, startup must fail with a concise actionable error. Python 3.10 installations remain valid when ACP is disabled.

### Inbound settings

Add an opt-in `[acp]` block to `src/cuga/settings.toml`:

```toml
[acp]
enabled = false
path_prefix = "/acp"
agent_name = "cuga"
agent_description = "CUGA agent exposed over ACP."
supervisor_config_path = ""
auto_approve = false
store = "memory"
store_limit = 1000
store_ttl_seconds = 3600
auth_required = true
enable_playground_cors = false
```

Requirements:

- `agent_name` follows the ACP RFC 1123 DNS-label constraint.
- `path_prefix` is normalized and cannot shadow existing CUGA routes.
- `auth_required=true` applies CUGA's existing chat-access dependency to ACP endpoints.
- If unauthenticated ACP is permitted, it must be an explicit deployment choice.
- The default store is documented as single-process only.

### External sub-agent YAML

Support one protocol block per external agent:

```yaml
agents:
  - name: remote-acp
    description: Remote ACP agent
    acp_protocol:
      enabled: true
      endpoint: https://agent.example.com/acp
      agent_name: remote-agent
      timeout: 30
      verify_tls: true
      auth:
        type: bearer
        token_env_var: REMOTE_ACP_TOKEN
```

Validation rules:

- Exactly one of `a2a_protocol` and `acp_protocol` may be enabled.
- `endpoint` must be HTTP(S), and redirects are disabled by default.
- `agent_name` must satisfy the ACP naming constraint.
- Timeouts must be positive and bounded.
- Secrets are loaded from environment or CUGA's secret system; plaintext tokens in YAML are not documented or logged.
- If user-managed endpoint configuration is allowed, apply the repository's SSRF policy and reject disallowed destinations.

## Sub-Task 1 — SDK compatibility and dependency gate

**Status:** `[x] complete`

**Resolved dependency versions:** `acp-sdk>=1.0.3,<2` with `uvicorn<0.36` (both Python 3.11+ only).
`acp-sdk==1.0.3` references `uvicorn.config.LoopSetupType`, removed in uvicorn 0.36.0.
No newer `acp-sdk<2` release fixes this; the narrowest compatible cap is `uvicorn<0.36`.
Compatible combination verified: `acp-sdk==1.0.3`, `uvicorn==0.35.0`.

**Intent:** Prove the official package can be safely integrated before writing adapters.

**Expected outcomes:**

- A tested ACP/Uvicorn version combination imports on Python 3.11, 3.12, 3.13, and 3.14 where the repository CI supports those versions.
- `acp-sdk` is added as an optional `acp` extra with a Python 3.11+ marker.
- The lock file is updated.
- Enabling ACP without the package yields an actionable startup error; disabled ACP performs no ACP imports.
- A small compatibility test imports `acp_sdk.models`, `acp_sdk.client.Client`, and `acp_sdk.server.create_app`.

**Files:**

- `pyproject.toml`
- `uv.lock`
- `tests/unit/acp/test_sdk_compatibility.py`

## Sub-Task 2 — Protocol-neutral runner contract

**Status:** `[ ] pending`

**Intent:** Share CUGA execution with A2A and ACP without making ACP depend on A2A-named types.

**Expected outcomes:**

- A neutral event DTO and async runner protocol live under `src/cuga/backend/server/agent_protocol/` or another protocol-neutral package.
- Existing A2A runners and adapters use or structurally satisfy that contract without wire changes.
- The direct runner accepts a configurable caller identity instead of hard-coding `a2a_user`.
- The direct runner preserves CUGA thread continuity and HITL behavior.
- Existing A2A tests continue to pass unchanged except for import-path adjustments required by the extraction.

**Files:**

- New neutral runner/event module
- `src/cuga/backend/server/a2a/runner.py`
- `src/cuga/backend/server/a2a/simple_runner.py`
- Relevant existing A2A tests

## Sub-Task 3 — CUGA ACP agent adapter

**Status:** `[ ] pending`

**Intent:** Adapt SDK-native ACP messages and context to the neutral CUGA runner.

**Expected outcomes:**

- `src/cuga/backend/server/acp/agent.py` implements the SDK `AgentManifest` contract or uses its `agent` decorator.
- The manifest advertises only `text/plain` input and output.
- Input extraction handles multiple messages and parts in stable order.
- Non-text, base64, and URL-backed parts return an SDK-compatible `invalid_input` failure; CUGA does not fetch URLs.
- Progress and answer events yield SDK-native values so the SDK creates standard message and run events.
- Errors sent to clients contain no raw exception text.
- HITL yields an SDK `AwaitRequest`; resumed `AwaitResume` data is correlated to the pending CUGA action.
- Empty CUGA streams end predictably rather than hanging.

**Files:**

- `src/cuga/backend/server/acp/__init__.py`
- `src/cuga/backend/server/acp/agent.py`
- `src/cuga/backend/server/acp/runner.py`

## Sub-Task 4 — SDK app factory, lifecycle, storage, and authentication

**Status:** `[ ] pending`

**Intent:** Mount an SDK-created ACP application safely inside CUGA.

**Expected outcomes:**

- `src/cuga/backend/server/acp/app.py` exports a factory that creates the ACP child app from settings, the selected runner, store, and auth dependency.
- The factory uses `acp_sdk.server.create_app()` rather than recreating routes.
- The child lifespan is integrated with CUGA startup/shutdown and verified by a real sync run.
- The SDK's playground CORS is disabled by default.
- Memory store capacity and TTL are configurable.
- Redis/PostgreSQL configuration is either implemented using SDK stores or explicitly deferred; no partially functional configuration is exposed.
- All run routes are protected when `auth_required=true`; discovery visibility is documented and tested.
- SDK exception handlers produce ACP error envelopes without exposing sensitive exception text.

**Compatibility gate:** Do not proceed to the production mount until a test proves the selected SDK version works with this repository's FastAPI, Starlette, and Uvicorn dependency set.

## Sub-Task 5 — Settings and application mount

**Status:** `[ ] pending`

**Intent:** Make inbound ACP opt-in and lazy-loaded.

**Expected outcomes:**

- Add the `[acp]` settings block described above.
- `src/cuga/backend/server/main.py` imports ACP code only inside the `settings.acp.enabled` branch.
- The mounted path defaults to `/acp`.
- The real `event_stream` callable is passed for direct-agent mode; an empty supervisor path must not silently select a placeholder when direct execution is available.
- Startup validates the agent name, path, package availability, store configuration, and authentication posture.
- Disabled ACP adds no routes and no ACP import cost.

**Files:**

- `src/cuga/settings.toml`
- `src/cuga/backend/server/main.py`
- ACP app/runner factory files

## Sub-Task 6 — Outbound SDK client

**Status:** `[ ] pending`

**Intent:** Delegate supervisor tasks through the official ACP client.

**Expected outcomes:**

- `src/cuga/backend/cuga_graph/nodes/cuga_supervisor/acp_protocol.py` wraps `acp_sdk.client.Client`.
- The primary helper accepts endpoint, agent name, task, auth configuration, timeout, and TLS settings.
- It performs agent discovery and a synchronous text run, then returns `{"result": str, "status": str, "variables": {}}`.
- Completed, failed, cancelled, and awaiting statuses are handled explicitly.
- ACP errors, malformed output, timeout, and transport failures are normalized without leaking credentials or response internals.
- Redirects are off by default and TLS verification is on by default.
- Local cancellation closes the client and attempts remote cancellation when possible.
- No raw `httpx` ACP protocol implementation is introduced.

## Sub-Task 7 — Supervisor configuration and dispatch

**Status:** `[ ] pending`

**Intent:** Make ACP agents loadable, discoverable to the supervisor, and dispatchable through all supported configuration paths.

**Expected outcomes:**

- `build_agents_from_list()` recognizes enabled `acp_protocol` entries as external agents.
- Configuration validation rejects agents with both A2A and ACP enabled.
- `prepare_agents_and_prompt.py` fetches the ACP manifest and uses its description/content types in prompt metadata.
- `delegation.py` dispatches ACP configurations to the ACP client wrapper and records the delegation.
- Stored sub-agent conversion supports ACP if the Manage UI/API exposes external protocols; otherwise this limitation is explicit and tested.
- Variable parameters are omitted from ACP tool signatures in the initial release.
- An example ACP supervisor YAML is added under `docs/examples/`.

**Files:**

- `src/cuga/supervisor_utils/supervisor_config.py`
- `src/cuga/backend/cuga_graph/nodes/cuga_supervisor/nodes/prepare_agents_and_prompt.py`
- `src/cuga/backend/cuga_graph/nodes/cuga_supervisor/delegation.py`
- `src/cuga/backend/cuga_graph/nodes/cuga_supervisor/acp_protocol.py`
- Stored-agent API/UI files if ACP is exposed there
- `docs/examples/acp_two_cuga/`

## Sub-Task 8 — Unit tests

**Status:** `[ ] pending`

All new or changed tests must carry the appropriate registered pytest marker. Fast isolated tests use `@pytest.mark.unit`.

Cover:

- SDK import and supported-version compatibility.
- Disabled-feature lazy imports.
- Manifest name validation and exact `text/plain` content types.
- Ordered extraction of multiple text messages and parts.
- Rejection of unsupported MIME types, base64 content, and content URLs.
- Event conversion for progress, completion, failure, empty stream, and HITL await/resume.
- Sanitized exception behavior.
- Store selection and invalid store configuration.
- YAML recognition of ACP agents and rejection of ambiguous A2A+ACP config.
- Outbound result extraction and every terminal status.
- Auth headers, environment-backed secrets, timeout, TLS, redirects, and log redaction.
- Delegation recording and prompt construction for ACP agents.

Do not use `pytest.importorskip` for CUGA-owned ACP modules. Tests requiring the optional SDK may use a clear skip condition when the `acp` extra is not installed, while the dedicated ACP CI invocation must install that extra so these tests cannot silently disappear.

## Sub-Task 9 — In-process integration and contract tests

**Status:** `[ ] pending`

Use `httpx.ASGITransport` against the mounted SDK application. Mark these tests `@pytest.mark.anyio` and `@pytest.mark.unit` because they use no external service.

Cover:

- `/ping`, agent listing, manifest lookup, unknown agent, and ACP name constraints.
- `sync`, `async`, and `stream` create modes.
- Correct SSE event types for stream mode.
- JSON history from `GET /runs/{run_id}/events`.
- Run status lookup and unknown/expired run behavior.
- Awaiting run, resume, wrong-state resume, and duplicate resume.
- Cancellation while active, cancellation after terminal state, and cancellation races.
- Session-to-CUGA-thread continuity.
- Concurrent run isolation.
- Client disconnect and application shutdown cleanup.
- Memory-store expiry behavior.
- Authentication enabled/disabled behavior.
- Validation and internal errors do not reflect submitted secrets or raw exception text.
- The ACP router is absent when disabled.
- Existing CUGA and A2A routes remain unchanged.
- Full in-process CUGA-to-CUGA outbound delegation through the official ACP client.

Validate representative responses and events with `acp_sdk.models`, not hand-written shape assertions alone. Keep a small set of golden payloads tied to the selected ACP version to detect dependency drift.

## Sub-Task 10 — Documentation and operational guidance

**Status:** `[ ] pending`

Document:

- Installation with the `acp` extra and the Python 3.11+ requirement for ACP only.
- The ACP base URL, default `/acp`.
- Inbound settings and external-agent YAML.
- Supported text-only behavior and rejected content forms.
- Authentication expectations.
- Memory-store restart and multi-worker limitations.
- Redis/PostgreSQL setup if enabled.
- ACP's compatibility/legacy status following its merger into A2A.
- How to disable ACP without importing the SDK.

## Implementation order

1. SDK compatibility and dependency gate.
2. Protocol-neutral runner extraction, with existing A2A regression tests.
3. ACP agent adapter unit tests and implementation.
4. SDK child-app lifecycle/auth/store spike, followed by the app factory.
5. Settings and lazy mount.
6. Outbound SDK wrapper.
7. Supervisor loader, discovery, prompt, dispatch, and stored-config integration.
8. End-to-end contract, lifecycle, concurrency, and security tests.
9. Documentation and example configuration.

Tests should be written alongside each implementation step rather than deferred to the end.

## Validation commands

Run at minimum:

```bash
uv sync --extra acp
uv run python -c "from acp_sdk.client import Client; from acp_sdk.server import create_app"
uv run ruff check src/cuga/backend/server/acp/ src/cuga/backend/server/agent_protocol/ tests/unit/acp/ tests/integration/acp/
uv run ruff format --check src/cuga/backend/server/acp/ src/cuga/backend/server/agent_protocol/ tests/unit/acp/ tests/integration/acp/
uv run pytest tests/unit/acp/ tests/integration/acp/ -m unit
uv run pytest tests/unit/a2a/ tests/integration/a2a/ -m unit
```

Also run the existing supervisor configuration and delegation test directories affected by the ACP changes.

## Completion criteria

ACP support is complete only when:

- The official SDK owns all ACP wire models and standard lifecycle behavior.
- All documented SDK endpoints and modes used by CUGA match the selected ACP version.
- Inbound sync, async, stream, resume, cancellation, event history, and session continuity pass in-process tests.
- Outbound CUGA-to-CUGA ACP delegation passes through `acp_sdk.client.Client`.
- Authentication and error sanitization tests pass.
- Python 3.10 can still install and use CUGA without the ACP extra.
- ACP-enabled supported Python environments pass the SDK compatibility test.
- Existing A2A tests remain green.
