# CUGA over Agent Client Protocol stdio

CUGA supports both local sides of [Agent Client Protocol (ACP)](https://agentclientprotocol.com/):

- an ACP editor can start CUGA as an agent with `cuga-acp`;
- a CUGA supervisor can start an ACP coding agent as a subprocess.

Both integrations use newline-delimited JSON-RPC over stdin/stdout. They do not open a port.

## Install

From PyPI, install the ACP extra:

```bash
pip install "cuga[acp]"
```

From a source checkout, use `uv sync --extra acp`. Configure CUGA's model and provider as usual before launching the inbound agent.

## Use CUGA from an ACP editor

ACP editors (such as Zed) can launch `cuga-acp` as a custom external agent. Copy [`editor-settings.json`](editor-settings.json) into your editor's agent configuration and replace `/absolute/path/to/cuga-acp` with the absolute path reported by `command -v cuga-acp`.

Send a normal text prompt, then start a second long-running prompt and cancel it from the thread UI. For protocol diagnostics, consult your editor's ACP log viewer.

`cuga-acp` reserves stdout for ACP frames. Library output and unsafe diagnostics are discarded; bounded adapter diagnostics go to stderr. Do not wrap the command with a program that prints banners to stdout.

## Delegate from a CUGA supervisor

[`consumer.supervisor.yaml`](consumer.supervisor.yaml) shows a generic ACP subprocess configuration. Replace `your-acp-agent` and `--acp` with the command and flags for the coding agent you installed (for example, `gemini --acp`). Replace `YOUR_API_KEY` with the environment variable name your agent expects. `env` is an allowlist of variable names inherited from the CUGA process; never put secret values in YAML. `cwd` must resolve inside the configured CUGA workspace.

```bash
export YOUR_API_KEY=your-provider-value
export DYNACONF_SUPERVISOR__ENABLED=true
export DYNACONF_SUPERVISOR__CONFIG_PATH="docs/examples/acp_stdio/consumer.supervisor.yaml"
cuga start demo_supervisor
```

Run those commands from the repository root. For an installed package, set
`DYNACONF_SUPERVISOR__CONFIG_PATH` to the absolute path of your copied YAML file.

## Permissions and limits

Supervisor plan approval and an ACP agent's operation permission are separate decisions. Approving a plan does not approve a later file, terminal, or other operation. Interactive supervisor runs pause with the existing CUGA tool-approval action; headless runs deny the operation and clean up the child process.

The stable integration currently supports text prompts and text response chunks. CUGA does not advertise image, audio, embedded-context, filesystem, terminal, or ACP-over-MCP capabilities. Session load/resume and remote HTTP/WebSocket transports are not supported. Each outbound delegation owns a bounded subprocess lifecycle with startup, prompt, and shutdown timeouts.

## Troubleshooting

- **Agent will not start:** use an absolute command path, install `cuga[acp]`, and confirm the command runs in the same environment as the editor or CUGA process.
- **Protocol parse error:** make sure neither the agent nor a wrapper prints logs or a startup banner to stdout. Logs belong on stderr.
- **Timeout:** increase `startup_timeout` only for slow initialization and `prompt_timeout` only for long tasks; first verify the child is not waiting for interactive login.
- **Stale permission resume:** the original process exited, its permission expired, or another resume already consumed it. Start a new delegation; CUGA intentionally fails closed.
- **Missing credential:** add only its variable name to `env`, export the value in the parent process, and restart CUGA.

For current editor configuration details, consult your editor's ACP documentation. For SDK examples, see the [official ACP Python SDK](https://github.com/agentclientprotocol/python-sdk).
