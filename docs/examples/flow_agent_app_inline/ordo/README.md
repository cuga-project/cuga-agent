# Ordo / RO FlowAgent Example

This example shows how to run a CUGA `FlowAgent` against a real `ro mcp` workflow server instead of the in-process stub.

The current example runs the `hello-world.ro` workflow.

## Files

```text
ordo/
├── README.md
├── hello-world.ro
├── run_with_mcp.py
└── config/
    ├── ordo_config.yaml
    └── supervisor_ordo.yaml
```

### `hello-world.ro`

The actual RO workflow program.

It defines one external goal:

```yaml
- kind: goal
  name: "compose-greeting"
  result_into: greeting
```

That goal name must match a task ID in `ordo_config.yaml`.

### `config/ordo_config.yaml`

CUGA-side task configuration for the RO workflow.

It tells FlowAgent which CugaAgent should handle each RO `goal`.

Example:

```yaml
flow:
  name: "Hello World"
  id: "hello-world"
  ro_source_file: "../hello-world.ro"
  input_args:
    name: "world"

variables:
  name: "world"
  greeting: ""

tasks:
  - id: "compose-greeting"
    mode: task_agent
    output_mapping:
      greeting: greeting
    agent:
      name: "compose-greeting"
      system_instruction: >
        Write a short, friendly greeting for the provided name.
        Return only the greeting string — no explanation, no JSON wrapper.
      tools: []
```

Important mapping rules:

- `flow.id` should match the RO workflow ID / program name.
- `flow.ro_source_file` points to the `.ro` file to register with RO. Relative paths are resolved from `config/`.
- `flow.input_args` is optional. If present, it is passed to `register_workflow` as `json.input_args`. Runtime inputs override these defaults.
- Each RO `kind: goal` `name` must have a matching task `id`.
- `output_mapping` should map the CugaAgent output back to the RO state variable named by `result_into`.

### `config/supervisor_ordo.yaml`

Supervisor configuration used by:

```bash
cuga start flow_agent_inline ordo
```

It connects the `flow_agent_ordo` agent to the real `ro mcp` server:

```yaml
agents:
  - name: ordo_flow_agent
    type: flow_agent_ordo
    flow_config: "ordo_config.yaml"
    process_key: "hello-world"
    mcp_server:
      command: "ro"
      args: ["mcp"]
```

Important fields:

- `process_key` should match the workflow ID (`hello-world` here).
- `flow_config` points to the CUGA task config.
- `mcp_server` defines how to start the RO MCP server.

### `run_with_mcp.py`

Standalone smoke-test script that talks directly to `ro mcp`.

It registers `hello-world.ro`, runs the workflow until an external goal, completes that goal with a sample result, and prints responses.

Run it with:

```bash
python docs/examples/flow_agent_app_inline/ordo/run_with_mcp.py
```

This script is independent of the CUGA supervisor. It has its own local MCP client config because it bypasses `supervisor_ordo.yaml`.

## How the `.ro` file and input args are configured

For the normal CUGA path (`cuga start flow_agent_inline ordo`), FlowAgent loads the `.ro` source via `FlowConfig.get_ro_source()`.

Use `flow.ro_source_file` in `ordo_config.yaml`:

```yaml
flow:
  name: "Hello World"
  id: "hello-world"
  ro_source_file: "../hello-world.ro"
  input_args:
    name: "world"
```

- `ro_source_file` is the path to the `.ro` program. Relative paths are resolved from the directory containing `ordo_config.yaml`.
- `input_args` is optional. If present, it is passed into RO when calling `register_workflow`:

```python
register_workflow(
  workflow_id="hello-world",
  json={
    "source": "...",
    "input_args": {"name": "world"},
    "force": True,
  },
)
```

Runtime values provided by the user / FlowAgent override `flow.input_args`.

`FlowConfig.get_ro_source()` still has fallback conventions (`<flow.id>.ro` next to the config or in the parent directory), but new examples should prefer explicit `ro_source_file`.

## Creating a new workflow

To add a new RO workflow, create a new `.ro` file and update the YAML configs.

Example: `approval.ro`

```yaml
kind: program
name: "approval"
version: "1.0.0"

state:
  - name: applicant
    type: Str
    default: ""
  - name: decision
    type: Str
    default: ""

body:
  kind: sequence
  steps:
    - kind: goal
      name: "make-decision"
      description: >
        Decide whether to approve the applicant.
        Return only "approved" or "rejected".
      given:
        - ref: applicant
      schema:
        type: string
      result_into: decision
    - kind: halt
      status: success
```

Then update `config/ordo_config.yaml`:

```yaml
flow:
  name: "Approval"
  id: "approval"
  ro_source_file: "../approval.ro"
  input_args:
    applicant: "Jane Doe"

variables:
  applicant: ""
  decision: ""

tasks:
  - id: "make-decision"
    mode: task_agent
    output_mapping:
      decision: decision
    agent:
      name: "make-decision"
      system_instruction: >
        Decide whether to approve the applicant.
        Return only "approved" or "rejected".
      tools: []
```

Then update `config/supervisor_ordo.yaml`:

```yaml
agents:
  - name: ordo_flow_agent
    type: flow_agent_ordo
    flow_config: "ordo_config.yaml"
    process_key: "approval"
    mcp_server:
      command: "ro"
      args: ["mcp"]
```

## Adding additional goals to a workflow

For every RO goal:

```yaml
- kind: goal
  name: "some-goal"
  result_into: some_result
```

add a matching task in `ordo_config.yaml`:

```yaml
tasks:
  - id: "some-goal"
    mode: task_agent
    output_mapping:
      some_result: some_result
    agent:
      name: "some-goal"
      system_instruction: >
        Instructions for the CugaAgent that fulfills this RO goal.
      tools: []
```

The key rule is:

```text
RO goal name == ordo_config.yaml task id
```

## Running the example

### Smoke test RO directly

```bash
python docs/examples/flow_agent_app_inline/ordo/run_with_mcp.py
```

### Run through CUGA

```bash
cuga start flow_agent_inline ordo
```

Then interact with the demo UI and provide a name.

## Running the SBP-EoG example

The EOG variant uses:

```text
config/ordo_config_eog.yaml
config/supervisor_ordo_eog.yaml
```

Run it with:

```bash
cuga start flow_agent_inline ordo --supervisor-config supervisor_ordo_eog.yaml
```

### Required RO data setup

The EOG workflow expects ITBench-Lite Scenario-1 data under the `data_dir` configured in `config/ordo_config_eog.yaml`:

```yaml
flow:
  id: "sbp-eog"
  ro_source_file: "/Users/offerakrabi/Desktop/Work/RO/examples/itbench-lite/sbp-eog.ro"
  input_args:
    data_dir: "/Users/offerakrabi/Desktop/Work/RO/examples/itbench-lite/test_data/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7/Scenario-1"
```

If that directory is missing, SRE MCP tools may fail with errors such as:

```text
Architecture file not found: <data_dir>/application_architecture.json
```

Set up the data from the RO repo first, for example:

```bash
cd /Users/offerakrabi/Desktop/Work/RO/examples/itbench-lite
./download-data.sh
```

or follow the setup instructions in the RO `examples/itbench-lite/README.md` / `Makefile`.

### Required SRE MCP tools

EOG task agents need tools from the `sre_utils` MCP server. `ordo_config_eog.yaml` declares the shared MCP server once:

```yaml
mcpServers:
  sre_utils:
    command: "/Users/offerakrabi/Desktop/Work/RO/examples/itbench-lite/start_sre_mcp.sh"
    args: []
    env:
      PYTHON: "/Users/offerakrabi/Desktop/Work/Cuga/cuga-agent-external/.venv/bin/python"
```

The tools are **not** automatically exposed to every task. Each task gets only the subset explicitly listed under that task's `agent.tools`, for example:

```yaml
- id: "build-topology"
  agent:
    tools:
      - build_topology

- id: "get-alerts"
  agent:
    tools:
      - alert_summary
      - alert_analysis
```

If the SRE MCP server cannot start, initialize its environment from the RO repo. The script expects either its local `.venv` or the `PYTHON` env override shown above.

## Notes

- The `mcp_server` config lives in `supervisor_ordo.yaml`, not `ordo_config.yaml`.
- `ordo_config.yaml` is still required: it tells CUGA how to handle RO goals.
- The RO MCP binary should expose the workflow/session tools:
  - `register_workflow`
  - `get_workflows`
  - `get_sessions`
  - `run_workflow`
  - `complete_goal`
  - `start_workflow`
  - `resume_workflow`