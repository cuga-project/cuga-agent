# ACP Support — Developer Implementation Plan

> **Audience:** Developers or implementation agents with no prior ACP or CUGA context.
>
> **Design source:** Read `plans/acp-support-plan.md` before starting. This document turns that design into small, ordered implementation tasks.
>
> **Protocol:** “ACP” means the i-am-bee **Agent Communication Protocol**, not the Agent Client Protocol used by editors.

## 1. Fixed decisions

Do not revisit these choices during implementation unless a task's explicit compatibility gate fails.

1. Use the official `acp-sdk`; do not create local ACP wire models or hand-written ACP HTTP endpoints.
2. Target `acp-sdk>=1.0.3,<2` and the ACP API represented by OpenAPI 0.2.0.
3. Keep CUGA's existing Python `>=3.10,<3.15` support. ACP is an optional extra available only on Python 3.11+.
4. Mount the SDK-created FastAPI application below `/acp` by default.
5. Support one inbound agent per CUGA process in the first release.
6. Support inline, plain `text/plain` messages only. Reject base64, URL-backed, and non-text parts.
7. Use the SDK `MemoryStore` only in the first release. Redis and PostgreSQL are follow-up work.
8. When `acp.auth_required=true`, apply CUGA's existing chat-access dependency to every ACP endpoint. This inherits CUGA's deployment posture: it requires JWT/role checks when global authentication is enabled and permits access when global authentication is disabled. When `acp.auth_required=false`, ACP explicitly adds no dependency.
9. Disable the SDK playground CORS middleware. CUGA's parent CORS configuration remains authoritative.
10. Keep SDK resource forwarding enabled because the SDK stores session history through its resource layer; those resource routes follow the same global authentication policy.
11. Outbound delegation uses the SDK client in asynchronous mode plus status polling. This permits timeout, awaiting-state detection, and remote cancellation.
12. Outbound ACP does not pass CUGA variables in the first release.
13. Outbound configuration is supported in supervisor YAML. Manage UI/stored-sub-agent authoring is deferred to a separate PR; existing stored configurations must continue working.
14. Existing A2A behavior and wire output must not change.
15. Do not edit CI workflow YAML merely to add test file paths. Tests belong under existing collected directories and must carry registered pytest markers.

## 2. Repository orientation

Read these files before making changes:

- `AGENTS.md` — test markers, CI directory discovery, and lazy-import rules.
- `CONTRIBUTING.md` — branch, commit, PR, DCO, formatting, and testing rules.
- `plans/acp-support-plan.md` — architecture and protocol rationale.
- `pyproject.toml` — Python range, optional dependencies, pytest markers, and dependency policy.
- `.github/actions/setup-cuga/action.yml` — CI uses Python 3.12 and installs all extras.
- `src/cuga/backend/server/a2a/router.py` — current protocol-facing runner contract.
- `src/cuga/backend/server/a2a/runner.py` — supervisor runner and mount helper.
- `src/cuga/backend/server/a2a/simple_runner.py` — direct CUGA event-stream runner and HITL handling.
- `src/cuga/backend/server/main.py` — FastAPI lifespan, application construction, and A2A mount.
- `src/cuga/supervisor_utils/supervisor_config.py` — supervisor YAML and stored-agent conversion.
- `src/cuga/backend/cuga_graph/nodes/cuga_supervisor/a2a_protocol.py` — current external client pattern.
- `src/cuga/backend/cuga_graph/nodes/cuga_supervisor/delegation.py` — external dispatch.
- `src/cuga/backend/cuga_graph/nodes/cuga_supervisor/nodes/prepare_agents_and_prompt.py` — discovery and prompt construction.
- `tests/unit/a2a/` and `tests/integration/a2a/` — regression patterns.

## 3. Target file layout

Create these files:

```text
src/cuga/backend/server/agent_protocol/
├── __init__.py
├── events.py
├── protocol.py
├── simple_runner.py
└── supervisor_runner.py

src/cuga/backend/server/acp/
├── __init__.py
├── agent.py
├── app.py
├── dependencies.py
└── runner.py

src/cuga/backend/cuga_graph/nodes/cuga_supervisor/
└── acp_protocol.py

tests/unit/acp/
├── __init__.py
├── test_agent.py
├── test_app.py
├── test_outbound_protocol.py
├── test_sdk_compatibility.py
└── test_supervisor_config.py

tests/integration/acp/
├── __init__.py
├── conftest.py
├── test_cuga_to_cuga.py
└── test_lifecycle.py

docs/examples/acp_two_cuga/
├── README.md
└── consumer.supervisor.yaml
```

Move direct-runner behavior from `src/cuga/backend/server/a2a/simple_runner.py` into the neutral package. Keep compatibility re-exports in the old module so downstream imports do not break.

## 4. Pull request sequence

Implement this work as four focused PRs. Each PR must be independently testable and use a Conventional Commit PR title.

| PR | Branch suggestion | PR title | Scope |
|---|---|---|---|
| 1 | `feature/acp-sdk-foundation` | `feat(acp): add SDK compatibility foundation` | Dependency gate, SDK compatibility, neutral runner extraction |
| 2 | `feature/acp-inbound-server` | `feat(acp): expose CUGA through ACP` | Inbound adapter, child app, settings, mount, lifecycle tests |
| 3 | `feature/acp-outbound-delegation` | `feat(supervisor): add ACP agent delegation` | SDK client wrapper, YAML loading, discovery, dispatch |
| 4 | `docs/acp-integration-guide` | `docs(acp): document ACP integration` | User docs and two-CUGA example |

If the team prefers one branch, use `feature/add-acp-support`, but keep commits aligned with the four scopes above.

---

# PR 1 — SDK compatibility foundation

## Task 1.1 — Add a failing SDK compatibility test

**Files**

- Create `tests/unit/acp/__init__.py`.
- Create `tests/unit/acp/test_sdk_compatibility.py`.

**Test first**

Add `pytestmark = pytest.mark.unit`. Test that these imports succeed when the ACP extra is installed:

```python
from acp_sdk.client import Client
from acp_sdk.models import Message, Run, RunStatus
from acp_sdk.server import MemoryStore, create_app
```

Also instantiate `MemoryStore(limit=10, ttl=timedelta(seconds=60))` and call `create_app()` with a minimal decorated echo agent. Assert the result is a FastAPI application.

Do not use `pytest.importorskip` in the dedicated ACP test suite. CI installs all extras on Python 3.12, so a missing SDK must fail loudly.

**Run**

```bash
uv run pytest tests/unit/acp/test_sdk_compatibility.py -m unit -q
```

**Expected before dependency change:** collection fails because `acp_sdk` is unavailable.

## Task 1.2 — Add the optional dependency and resolve Uvicorn compatibility

**Files**

- Modify `pyproject.toml` under `[project.optional-dependencies]`.
- Regenerate the root `uv.lock`.

**Change**

Add this entry to `[project.optional-dependencies]`:

```toml
acp = ["acp-sdk>=1.0.3,<2; python_version >= '3.11'"]
```

Run:

```bash
uv lock
uv sync --extra acp --dev
uv run python -c "from acp_sdk.client import Client; from acp_sdk.server import MemoryStore, create_app"
```

`acp-sdk==1.0.3` references `uvicorn.config.LoopSetupType`, which is absent in some newer Uvicorn versions. Resolve this in the following order:

1. Check whether a newer `acp-sdk<2` release fixes the import and raise the lower bound to that release if so.
2. Otherwise add the narrowest Uvicorn upper bound proven compatible with both ACP and CUGA.
3. Do not monkey-patch Uvicorn and do not copy SDK source into CUGA.

Record the chosen compatible versions in a comment next to the dependency constraint and in `plans/acp-support-plan.md`.

**Acceptance**

- Python 3.12 installs the `acp` extra and passes Task 1.1.
- A Python 3.10 dependency-resolution check can install CUGA without ACP.
- The normal project lock remains a single root `uv.lock`.

## Task 1.3 — Define neutral event and runner interfaces

**Files**

- Create `src/cuga/backend/server/agent_protocol/__init__.py`.
- Create `src/cuga/backend/server/agent_protocol/events.py`.
- Create `src/cuga/backend/server/agent_protocol/protocol.py`.
- Create `tests/unit/test_agent_protocol_contract.py`.

**Symbols**

Define:

```python
@dataclass(slots=True)
class AgentStreamEvent:
    name: str
    data: Mapping[str, Any] | str | None = None
    final: bool = False

class AgentRunner(Protocol):
    def run(
        self,
        message: str,
        context_id: str | None = None,
        approval: dict[str, Any] | None = None,
    ) -> AsyncIterator[AgentStreamEvent]: ...
```

Export both symbols from the package `__init__.py`.

**Tests**

- Construct each valid event shape.
- Verify a fake async runner structurally satisfies the protocol when checked by static typing or by a runtime-checkable protocol if that is already the repository convention.
- Mark every test `@pytest.mark.unit`.

## Task 1.4 — Extract the direct runner without changing A2A behavior

**Files**

- Create `src/cuga/backend/server/agent_protocol/simple_runner.py`.
- Create `src/cuga/backend/server/agent_protocol/supervisor_runner.py`.
- Modify `src/cuga/backend/server/a2a/simple_runner.py`.
- Modify `src/cuga/backend/server/a2a/runner.py`.
- Update affected A2A tests only where import paths require it.

**Change**

1. Move `SimpleA2ARunner` behavior into a neutral `SimpleAgentRunner`.
2. Give `SimpleAgentRunner.__init__()` this exact signature: `__init__(app_state_ref, event_stream_func, auto_approve=False, caller_user_id="agent_protocol_user")`.
3. Replace the hard-coded `a2a_user` passed to CUGA's `event_stream` with `caller_user_id`.
4. Move `SupervisorA2ARunner` behavior into `SupervisorAgentRunner`; parameterize its protocol name for log/error labels and its app-state cache attribute so ACP and A2A do not share a supervisor accidentally.
5. Return `AgentStreamEvent` instances from both neutral runners.
6. Preserve `_MAX_AUTO_RESUMES`, frame decoding, pending-action detection, and approval parsing behavior.
7. In `a2a/simple_runner.py`, preserve `SimpleA2ARunner` as a thin subclass whose constructor keeps the existing three-argument API and passes `caller_user_id="a2a_user"` to `SimpleAgentRunner`.
8. In `a2a/runner.py`, preserve `SupervisorA2ARunner` as a thin compatibility subclass configured with protocol name `A2A` and cache attribute `a2a_supervisor`; set `A2AStreamEvent = AgentStreamEvent` as a compatibility alias.
9. Do not change A2A JSON or SSE output.

**Tests**

Add or update tests to prove:

- `SimpleAgentRunner` passes its configured caller ID.
- The A2A compatibility wrapper still passes `a2a_user`.
- Existing A2A HITL, stream, and router tests remain green.

**Run**

```bash
uv run pytest tests/unit/test_agent_protocol_contract.py tests/unit/test_a2a_simple_runner_hitl.py tests/integration/a2a/test_simple_runner.py -m unit
uv run pytest tests/unit/a2a tests/integration/a2a -m unit
```

## Task 1.5 — Verify PR 1

```bash
uv run ruff check src/cuga/backend/server/agent_protocol tests/unit/acp/test_sdk_compatibility.py tests/unit/test_agent_protocol_contract.py
uv run ruff format --check src/cuga/backend/server/agent_protocol tests/unit/acp/test_sdk_compatibility.py tests/unit/test_agent_protocol_contract.py
uv run pytest tests/unit/acp/test_sdk_compatibility.py tests/unit/test_agent_protocol_contract.py -m unit
uv run pytest tests/unit/a2a tests/integration/a2a -m unit
```

**PR 1 done when**

- ACP SDK imports reliably with the locked dependency set.
- CUGA remains installable on Python 3.10 without ACP.
- A2A behavior is unchanged.
- The neutral runner contract exists for PR 2.

---

# PR 2 — Inbound ACP server

## Task 2.1 — Add ACP settings

**Files**

- Modify `src/cuga/settings.toml` immediately after `[a2a]`.
- Create `src/cuga/backend/server/acp/settings.py`.
- Create `tests/unit/acp/test_app.py`.

**Settings**

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

**Validation behavior**

Implement `normalize_acp_settings(acp_settings) -> ACPSettings` in `acp/settings.py`, where `ACPSettings` is a local dataclass containing the fields above. It may describe CUGA configuration, but it must not duplicate ACP wire models.

- Reject invalid ACP agent names at startup.
- Normalize the prefix to one leading slash and no trailing slash.
- Reject `/`, an empty prefix, or any prefix in the fixed reserved set `{ "/api", "/a2a", "/docs", "/health", "/openapi.json", "/redoc", "/run", "/stream" }`.
- Reject stores other than `memory` in this release.
- Require positive `store_limit` and `store_ttl_seconds`.

**Tests**

Test valid defaults and each rejection. Mark tests `@pytest.mark.unit`.

## Task 2.2 — Implement exact ACP input extraction

**Files**

- Create `src/cuga/backend/server/acp/__init__.py` without eager SDK imports.
- Create `src/cuga/backend/server/acp/agent.py`.
- Create `tests/unit/acp/test_agent.py`.

**Symbols**

Implement private helper:

```python
def _extract_text_input(messages: list[Message]) -> str:
    ...
```

Rules:

1. Accept only messages whose role is `user`.
2. Accept only parts with `content_type == "text/plain"`.
3. Require `content_encoding` to be `plain` or absent.
4. Require inline `content`; reject `content_url`.
5. Preserve message and part order.
6. Join parts within a message with `""`, matching the SDK's `Message.__str__()` behavior.
7. Join separate messages with `"\n"`.
8. Reject an empty final string.
9. Raise `ACPError(Error(code=ErrorCode.INVALID_INPUT, message=<constant safe message>))` for invalid input.
10. Never include rejected content or URLs in the error message.

**Tests**

Cover one part, multiple parts, multiple messages, wrong role, non-text MIME type, base64, URL, missing content, empty input, and non-reflection of submitted secrets.

## Task 2.3 — Implement the SDK agent adapter

**Files**

- Modify `src/cuga/backend/server/acp/agent.py`.
- Modify `tests/unit/acp/test_agent.py`.

**Symbol**

Implement:

```python
class CugaACPAgent(acp_sdk.server.AgentManifest):
    def __init__(self, *, runner: AgentRunner, name: str, description: str): ...

    @property
    def name(self) -> AgentName: ...

    @property
    def description(self) -> str: ...

    @property
    def input_content_types(self) -> list[str]: ...

    @property
    def output_content_types(self) -> list[str]: ...

    async def run(
        self,
        input: list[Message],
        context: Context,
    ) -> AsyncGenerator[RunYield, RunYieldResume]: ...
```

**Thread identity**

Use `str(context.session.id)` as the CUGA `context_id` for every initial and resumed call. The SDK always creates or resolves a session before invoking the agent, so do not use the ACP run ID.

**Event mapping**

| Neutral event | ACP adapter behavior |
|---|---|
| Non-terminal progress | Ignore in the initial release; do not expose internal reasoning text |
| Terminal name `final_answer`, `task_complete`, `completed`, or `done`, or any event with `final=true` that is neither HITL nor an error | Yield one `Message(role="agent", parts=[MessagePart(content_type="text/plain", content=<safe text>)])`, then return |
| `error`, `failed`, `failure`, or `exception` | Yield `Error(code=SERVER_ERROR, message="CUGA agent execution failed")`, then return |
| HITL name containing `approval`, `input_required`, `user_input`, `interrupt`, or `hitl` | Yield `MessageAwaitRequest` containing the prompt; receive `MessageAwaitResume`; call the same runner again with the same context ID, resumed text, and parsed approval |
| Stream ends without terminal output | Yield an empty-safe completion message `"Agent completed processing"`, then return |

For HITL, retain `action_id` in local generator state. Parse resumed text with the same approval vocabulary used by the neutral simple runner. Pass `{"action_id": action_id, "confirmed": bool}` when a confirmation decision is recognized; otherwise pass `None` so the runner's existing logic can re-prompt. Limit ACP resume cycles to the same neutral runner maximum.

Never propagate a raw exception from CUGA into the SDK executor: SDK 1.0.3 serializes `str(exception)` into failed runs. Log the full exception and yield the constant sanitized `Error` above.

**Tests**

Use a scripted neutral runner and a fake context with a UUID session. Cover the complete mapping table, stable session ID, approval and denial resumes, repeated ambiguous resume, cycle limit, and exception sanitization.

## Task 2.4 — Build the ACP child application

**Files**

- Create `src/cuga/backend/server/acp/dependencies.py`.
- Create `src/cuga/backend/server/acp/runner.py`.
- Create `src/cuga/backend/server/acp/app.py`.
- Modify `tests/unit/acp/test_app.py`.

**Symbols**

Implement:

```python
def build_acp_app_for_settings(
    acp_settings: Any,
    app_state: Any,
    *,
    event_stream_func: Any,
) -> FastAPI:
    ...
```

Behavior:

1. Import `acp_sdk` inside this function or inside ACP modules imported only by the enabled branch.
2. If `supervisor_config_path` is set, instantiate `SupervisorAgentRunner` with protocol name `ACP` and cache attribute `acp_supervisor`.
3. Otherwise instantiate `SimpleAgentRunner` with `caller_user_id="acp_user"` and the supplied `event_stream_func`.
4. Never mount a placeholder when the real event stream is available.
5. Create `CugaACPAgent` from validated settings.
6. Create `MemoryStore(limit=store_limit, ttl=timedelta(seconds=store_ttl_seconds))`.
7. Call `acp_sdk.server.create_app()` with the agent, store, `enable_playground_cors=False`, and authentication dependencies.
8. Leave SDK resource forwarding enabled because sessions store message history as resources.
9. Document and test that ACP HITL resume is supported by direct-agent mode; supervisor mode exposes only the terminal behavior supported by `CugaSupervisor.invoke()` and must not advertise a stronger guarantee.

**Authentication dependency**

Create a dependency wrapper that calls CUGA's `require_chat_access` when `auth_required=true`. Pass `Depends(wrapper)` through the SDK app's global `dependencies` argument so `/ping`, discovery, runs, sessions, and resources all share one policy. Pass an empty dependency list when false. This intentionally inherits the globally configured CUGA authentication behavior rather than creating a second token validator.

**Tests**

- Correct runner selection.
- Memory-store settings.
- All endpoints require authentication when enabled.
- All endpoints are reachable without credentials when disabled.
- Missing SDK produces an actionable message mentioning `cuga[acp]`.

## Task 2.5 — Prove and integrate child lifespan

**Files**

- Create `tests/integration/acp/conftest.py`.
- Create `tests/integration/acp/test_lifecycle.py`.
- Modify `src/cuga/backend/server/main.py` only after the spike passes.

**Spike procedure**

1. Build a parent FastAPI app with its own lifespan.
2. Mount the SDK app under `/acp`.
3. Use an ASGI lifespan manager plus `httpx.ASGITransport`.
4. Submit an ACP synchronous run.
5. Confirm the run executes instead of failing because the SDK executor was never initialized.
6. Repeat startup/shutdown and assert no leaked task/client warnings.

**Required implementation**

Starlette-mounted sub-applications do not reliably receive lifespan events. Integrate the ACP child's lifespan explicitly into CUGA's existing `lifespan()` using an `AsyncExitStack`:

1. Define module state `_acp_app: FastAPI | None = None` without importing `acp_sdk`.
2. In the existing enabled mount block after the parent `app` is created, build the child, assign `_acp_app`, and call `app.mount(normalized_prefix, _acp_app)`.
3. In the parent `lifespan()`, after startup prerequisites are available and before its `yield`, enter `_acp_app.router.lifespan_context(_acp_app)` through the stack when `_acp_app` is not `None`.
4. Exit the stack after the parent `yield`, during shutdown.
5. Instrument the test agent's startup-sensitive execution and prove the child context is entered exactly once per parent lifespan.

Keep ACP imports inside the enabled mount block or helper calls so disabled startup remains lazy. Do not build or mount routes during startup; the child app must be mounted before the ASGI lifespan begins.

If the installed Starlette version demonstrably propagates mounted lifespan, document the test evidence and do not double-enter it. The acceptance criterion is exactly one startup and shutdown.

## Task 2.6 — Mount ACP in CUGA

**Files**

- Modify `src/cuga/backend/server/main.py`.
- Modify `tests/integration/acp/test_lifecycle.py`.

**Change**

- Mount the initialized child application at normalized `settings.acp.path_prefix`.
- Pass the real `event_stream` function to `build_acp_app_for_settings()`.
- Do not add top-level ACP or graph imports.
- Leave the A2A mount unchanged.

**Tests**

- ACP disabled: `/acp/ping` is `404`, `acp_sdk` is not imported during a subprocess import of CUGA server startup, and existing routes still work.
- ACP enabled: `/acp/ping` and `/acp/agents` work.
- A2A endpoints remain unchanged when both protocols are enabled.

## Task 2.7 — Test the complete inbound lifecycle

**Files**

- Expand `tests/integration/acp/test_lifecycle.py`.

**Required cases**

1. Agent listing and manifest lookup.
2. Unknown agent.
3. Synchronous run returning text output.
4. Asynchronous run returning `202`, then polling to completion.
5. Stream run returning standard `run.created`, `run.in-progress`, message, and `run.completed` SSE events.
6. Event-history endpoint returning JSON, not SSE.
7. Session ID reused as the CUGA thread ID across two runs.
8. In direct-agent mode, HITL reaches `awaiting`, resumes through `POST /runs/{run_id}`, and completes on the same thread.
9. Cancellation of an active run reaches `cancelled`.
10. Cancellation after completion receives the SDK-defined rejection.
11. Unknown and expired run IDs.
12. Two concurrent runs do not mix events or outputs.
13. Memory-store TTL expiry.
14. Invalid inputs return ACP errors without reflecting input values.
15. Runner exceptions create failed runs with a constant sanitized message.

Validate bodies with `acp_sdk.models` and event discriminators rather than loose dictionary checks.

## Task 2.8 — Verify PR 2

```bash
uv run ruff check src/cuga/backend/server/acp src/cuga/backend/server/agent_protocol tests/unit/acp tests/integration/acp
uv run ruff format --check src/cuga/backend/server/acp src/cuga/backend/server/agent_protocol tests/unit/acp tests/integration/acp
uv run pytest tests/unit/acp tests/integration/acp -m unit
uv run pytest tests/unit/a2a tests/integration/a2a -m unit
uv run python scripts/checks/no_exc_in_responses.py
```

**PR 2 done when**

- All inbound ACP modes work through `/acp`.
- Resume, cancellation, sessions, and event history are SDK-owned and tested.
- Authentication and sanitization are enforced.
- Disabled ACP remains lazy and route-free.

---

# PR 3 — Outbound ACP supervisor delegation

## Task 3.1 — Define and validate ACP YAML configuration

**Files**

- Modify `src/cuga/supervisor_utils/supervisor_config.py`.
- Create or extend tests in `src/cuga/sdk_core/tests/test_supervisor_yaml_config.py`.
- Create `tests/unit/acp/test_supervisor_config.py` only for helper behavior not naturally covered by the colocated SDK test.

**Accepted shape**

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

**Loader behavior**

1. An enabled `acp_protocol` makes the entry external.
2. Exactly one enabled protocol block is allowed.
3. Require `endpoint` and `agent_name`.
4. Accept only `http` or `https` endpoint schemes.
5. Require `timeout > 0` and cap it at 600 seconds.
6. Default `verify_tls=true`.
7. Resolve bearer tokens from `token_env_var` at invocation time, not while loading YAML, so rotation does not require rebuilding the supervisor.
8. Never log token values.
9. Leave stored `kind: "a2a"` conversion unchanged. `kind: "acp"` UI/stored support is out of scope for this PR.

**Tests**

Cover valid ACP, missing fields, invalid URL scheme, timeout bounds, dual protocol, disabled ACP block, and preservation of existing A2A/internal loading.

## Task 3.2 — Implement the outbound SDK wrapper tests

**Files**

- Create `tests/unit/acp/test_outbound_protocol.py`.

**Test double**

Patch `acp_sdk.client.Client`, or inject a client factory into the wrapper. Do not perform network calls.

**Required cases**

- Manifest discovery succeeds.
- Agent name mismatch or missing agent fails clearly.
- Async run is polled from `created`/`in-progress` to `completed`.
- Text is extracted from all plain-text output parts in order.
- Failed and cancelled runs return normalized statuses.
- Awaiting runs return a failed/unsupported result rather than polling forever.
- Timeout triggers remote cancellation.
- Coroutine cancellation triggers remote cancellation and re-raises `CancelledError`.
- Authentication header comes from the configured environment variable.
- Missing token, malformed output, ACP error, and transport error are sanitized.
- TLS verification defaults true; redirects default false.

Mark all tests `@pytest.mark.unit`.

## Task 3.3 — Implement the outbound SDK wrapper

**Files**

- Create `src/cuga/backend/cuga_graph/nodes/cuga_supervisor/acp_protocol.py`.

**Public symbol**

```python
async def delegate_task_via_acp(
    *,
    endpoint: str,
    agent_name: str,
    task: str,
    auth: Mapping[str, Any] | None = None,
    timeout: float = 30.0,
    verify_tls: bool = True,
    poll_interval: float = 0.25,
    client_factory: Callable[..., Client] = Client,
) -> dict[str, Any]:
    ...
```

**Algorithm**

1. Validate configuration before creating the client.
2. Resolve bearer `token_env_var`; create an `Authorization: Bearer ...` header.
3. Instantiate `Client(base_url=endpoint, headers=headers, timeout=<bounded HTTP timeout>, verify=verify_tls, follow_redirects=False)`.
4. Call `client.agent(name=agent_name)` and ensure the peer advertises `text/plain` input and output.
5. Call `client.run_async(task, agent=agent_name)`.
6. Save `run_id` immediately.
7. Poll `client.run_status(run_id=run_id)` every `poll_interval` until terminal, awaiting, or total deadline.
8. On completion, concatenate all non-empty `text/plain` output parts in message order using newline separators between messages.
9. Return `{"result": text, "status": "success", "variables": {}}` for completed runs.
10. Return `{"result": "Remote ACP agent failed.", "status": "failed", "variables": {}}` for failed runs.
11. Return `{"result": "Remote ACP agent cancelled the run.", "status": "failed", "variables": {}}` for cancelled runs.
12. Return `{"result": "Remote ACP agent requires interactive input, which supervisor delegation does not support.", "status": "failed", "variables": {}}` for awaiting runs.
13. On total timeout, best-effort call `client.run_cancel(run_id=run_id)` and return `{"result": "Remote ACP agent timed out.", "status": "failed", "variables": {}}`.
14. On local coroutine cancellation, best-effort call `client.run_cancel(run_id=run_id)` and re-raise `CancelledError`.
15. Always close the SDK client using its async context manager.
16. Never include tokens, raw response bodies, or remote exception strings in caller-visible text.

## Task 3.4 — Add ACP manifest formatting and prompt preparation

**Files**

- Modify `src/cuga/backend/cuga_graph/nodes/cuga_supervisor/nodes/prepare_agents_and_prompt.py`.
- Add tests in the existing supervisor test directory.

**Change**

1. Add a small formatter for SDK `AgentManifest` using name, description, and input/output content types.
2. For an external ACP entry, fetch the manifest with the outbound helper/client.
3. On discovery failure, log only exception class and remote agent name; use configured description as fallback.
4. Build the delegation tool with `task: str` only for ACP.
5. Do not expose the A2A `variables` parameter for ACP.
6. Keep imports scoped so users without the ACP extra do not import it unless an ACP agent is configured.

**Tests**

- Manifest description appears in the supervisor prompt.
- Configured description is used when discovery fails.
- ACP tool schema has only `task`.
- A2A variable behavior is unchanged.

## Task 3.5 — Dispatch ACP delegations

**Files**

- Modify `src/cuga/backend/cuga_graph/nodes/cuga_supervisor/delegation.py`.
- Extend `src/cuga/backend/cuga_graph/nodes/cuga_supervisor/tests/test_delegation_recording.py`.

**Change**

Before the existing A2A branch inside external-agent handling:

1. Read `config.acp_protocol`.
2. If enabled, lazily import and call `delegate_task_via_acp()`.
3. Pass endpoint, agent name, auth, timeout, and TLS settings.
4. Record the delegation through `_record_delegation()`.
5. Return the normalized result text.
6. Never fall through from an invalid ACP configuration into the legacy A2A protocol.
7. Preserve the existing A2A path exactly.

**Tests**

- ACP helper receives expected values.
- ACP result is recorded.
- ACP failure is recorded consistently.
- Existing A2A and internal cases still pass.

## Task 3.6 — Add an in-process CUGA-to-CUGA test

**Files**

- Create `tests/integration/acp/test_cuga_to_cuga.py`.

**Test**

1. Build an inbound ACP child app around a scripted CUGA runner.
2. Use `httpx.ASGITransport` through an injected SDK client transport.
3. Call the real `delegate_task_via_acp()` wrapper.
4. Assert discovery, async creation, polling, output extraction, and normalized success.
5. Add variants for failed and awaiting runs.

Do not bind a port and do not call external services. Mark with `@pytest.mark.anyio` and `@pytest.mark.unit`.

## Task 3.7 — Verify PR 3

```bash
uv run ruff check src/cuga/backend/cuga_graph/nodes/cuga_supervisor/acp_protocol.py src/cuga/supervisor_utils/supervisor_config.py tests/unit/acp tests/integration/acp
uv run ruff format --check src/cuga/backend/cuga_graph/nodes/cuga_supervisor/acp_protocol.py src/cuga/supervisor_utils/supervisor_config.py tests/unit/acp tests/integration/acp
uv run pytest tests/unit/acp tests/integration/acp -m unit
uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_supervisor/tests -m unit
uv run pytest src/cuga/sdk_core/tests/test_supervisor_yaml_config.py -m unit
```

**PR 3 done when**

- YAML-configured ACP agents are discovered and callable.
- Timeout, cancellation, awaiting, auth, and output extraction are deterministic.
- Existing internal and A2A delegation tests remain green.

---

# PR 4 — Documentation and examples

## Task 4.1 — Add the two-CUGA example

**Files**

- Create `docs/examples/acp_two_cuga/README.md`.
- Create `docs/examples/acp_two_cuga/consumer.supervisor.yaml`.

Document:

- Installing `cuga[acp]` on Python 3.11+.
- Starting a provider with inbound ACP enabled.
- Starting a consumer with the ACP YAML block.
- The `/acp` base URL.
- Required token environment variable setup without including a real token.
- A sample request and expected response.
- The text-only and outbound-no-HITL limitations.

## Task 4.2 — Update primary documentation

**Files**

- Update `README.md` or the repository's selected protocol documentation page.

Add:

- Inbound settings table.
- External-agent YAML schema.
- Memory-store restart and single-worker limitations.
- Authentication behavior.
- Python version and optional-extra requirement.
- Clarification that this is i-am-bee ACP compatibility and that ACP merged into A2A.
- Link to the official SDK and pinned protocol version.

## Task 4.3 — Final repository validation

```bash
uv sync --all-extras --dev --frozen
uv run ruff check
uv run ruff format --check
uv run pytest tests/unit tests/integration -m "not manual and not pgvector and not load and not e2e and not stability"
uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_supervisor/tests -m unit
uv run pytest src/cuga/sdk_core/tests/test_supervisor_yaml_config.py -m unit
uv run python scripts/checks/no_exc_in_responses.py
```

Run secret scanning according to `CONTRIBUTING.md` before committing.

---

## 5. Test and CI policy

- Every new or changed Python test must have `@pytest.mark.unit` or another registered type marker.
- In-process ASGI tests use both `@pytest.mark.anyio` and `@pytest.mark.unit`.
- Place ACP tests under `tests/unit/acp/` and `tests/integration/acp/`; the existing `unit-b` job discovers both directories.
- The existing setup action uses Python 3.12 and `uv sync --all-extras --dev --frozen`, so it installs the ACP extra. No workflow edit is needed for ACP test discovery.
- Do not add individual test paths to workflow YAML.
- Keep Python 3.10 compatibility covered by dependency resolution or a lightweight import test with ACP disabled; ACP-specific tests run on Python 3.11+.
- Do not use `pytest.importorskip` to hide missing CUGA modules.

## 6. Security checklist

Before each implementation PR is complete, verify:

- No raw exception string reaches an ACP response or supervisor result.
- No token value is logged or stored in a returned error.
- Outbound TLS verification defaults on.
- Outbound redirects default off.
- Only HTTP(S) outbound endpoints are accepted.
- The configured endpoint is checked by the repository's SSRF policy if untrusted users can edit it.
- URL-backed inbound message parts are rejected without fetching.
- Request validation does not echo attacker-controlled values.
- ACP endpoints use `require_chat_access` when configured as protected.
- Disabled ACP imports no SDK modules.

## 7. Definition of done

The feature is complete when all of the following are true:

- `cuga[acp]` installs on the repository's Python 3.12 CI environment.
- Base CUGA remains installable on Python 3.10 without ACP.
- The official SDK owns models, routes, events, stores, client transport, and lifecycle state transitions.
- Inbound sync, async, stream, history, session, direct-agent HITL resume, and cancellation tests pass.
- Outbound YAML discovery and delegation tests pass.
- A full in-process CUGA-to-CUGA ACP test passes.
- Authentication, secret handling, TLS, timeout, cancellation, and exception-sanitization tests pass.
- Existing A2A tests pass without wire changes.
- Documentation clearly states supported and deferred behavior.
- Each commit includes DCO signoff, and PR titles follow Conventional Commits.