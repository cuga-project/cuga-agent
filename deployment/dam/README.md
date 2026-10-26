# CUGA custom image for DAM

This directory builds a general-purpose CUGA agent image for DAM's text ACP
interface. It starts from public CUGA `main` and adds only the integration
needed for DAM chat, persistent sessions, DAM-managed tools and skills, and the
separate terminal attachment. The Dockerfile pins a DAM platform-base image;
DAM's server and credential gateway remain outside this image.

## Supported paths

| Capability | How it works |
| --- | --- |
| DAM web chat and `dam run` | DAM starts `harness-chat`, which runs `cuga-acp` over stdio. |
| Saved chat | ACP `session/list` and `session/load` use `$CUGA_FOLDER/acp-sessions.sqlite3`; LangGraph uses `$CUGA_FOLDER/acp-checkpoints.sqlite3`. Both are on DAM's persistent work volume. |
| DAM tools and connections | The ACP adapter accepts HTTP/SSE MCP servers from the session and merges DAM's `~/.mcp.json`. It exposes every tool the granted servers advertise under a Python-safe name. DAM controls connection grants and server-side authorization. |
| Schedules | DAM's scheduler delivers a task as an ACP turn. Its advertised schedule-management MCP tools are available to CUGA. A schedule precheck runs in the container before the ACP turn. |
| Skills | DAM installs skills in `/home/agent/.agents/skills`. The launcher links that tree into CUGA's configured skills root on the persistent work volume. |
| Native tools | Shell and filesystem tools are enabled in CUGA with the local execution backend. The shell approval policy requires a one-use human decision for each `run_command`; the ACP request shows the pending code. |
| `dam chat` | DAM's PTY runs `harness-terminal`, which opens a CUGA text console with `:new` and `:quit`. This path does not speak ACP JSON-RPC over the terminal. |

This is the proven text-chat scope. Image, audio, browser, and ACP-to-ACP MCP
features are not advertised. Shell approval covers `run_command` only; it does
not authorize filesystem tools or DAM MCP side effects. DAM's connection and
network policies still apply to all outbound access.

## Image contents and defaults

The two-stage [Dockerfile](Dockerfile) extends a pinned DAM base, installs CUGA
and its `acp` extra into `/opt/cuga/.venv`, and omits the large packages listed
in CUGA's slim Dockerfile. It caches the local policy embedding model at build
time and runs as UID 65532. DAM's base entry point remains in charge of the
container. No connection ID, Slack token, GitHub token, or usable model key is
baked in. `OPENAI_API_KEY=dummy-placeholder` is a literal placeholder; a DAM
model-provider connection supplies runtime access.

The image defaults to `MODEL_NAME=azure/gpt-5.6-sol`. Override it in the DAM
agent environment if the selected model provider uses another route. Shell,
filesystem, and skills are enabled by default through CUGA settings in the
Dockerfile. CUGA Lite binds the complete tool provider and retries an unsupported
"tools unavailable" claim before ending a turn. The launchers seed
[shell-command-approval.md](shell-command-approval.md)
only if the workspace policy file does not yet exist, preserving later edits.

The launcher expects `/home/agent/work/.agents` to be absent or a symlink to
`/home/agent/.agents`. It creates that link on first start and fails with a
clear error if another file or directory occupies the path. A user migrating
an older workspace should inspect that path before switching images.

## Build and local ACP check

Build from the repository root after committing the source. Use the required
source-commit label, a readable UTC-date/short-commit tag, and an immutable
digest for deployment. The currently deployed CUGA DAM images use AMD64; verify
the target cluster architecture before adding another platform.

```sh
revision=$(git rev-parse HEAD)
short_revision=$(git rev-parse --short=8 HEAD)
image_tag="$(date -u +%Y%m%d)-$short_revision"
image="docker.io/vinodmut/cuga-dam:$image_tag"
docker buildx build --platform linux/amd64 \
  --file deployment/dam/Dockerfile \
  --build-arg "CUGA_SOURCE_COMMIT=$revision" \
  --tag "$image" --load .

python deployment/dam/local_acp_client.py --image "$image" --check
```

The handshake check needs Docker and the `acp` Python extra in the local
checkout; it does not call an LLM. To send a model prompt, supply a real local
`OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `MODEL_NAME` and pass `--prompt`.
The client passes these values at container start and asks before granting an
ACP permission request. `--volume <name>` retains ACP state between local runs;
`--session <id>` loads an existing session from that volume.

After the local and DAM checks pass, publish the same source revision:

```sh
docker buildx build --platform linux/amd64 \
  --file deployment/dam/Dockerfile \
  --build-arg "CUGA_SOURCE_COMMIT=$revision" \
  --tag "$image" --push .
docker buildx imagetools inspect "$image"
```

## Configure an agent in DAM

Create a new custom-image agent using the image digest and grant a model
provider. The current DAM CLI's `dam agent create` command accepts templates
but has no `--image` option; use a custom-image creation flow in the UI or API
for this one step. The CLI can manage the resulting agent, connections,
channels, schedules, skills, files, sessions, and approvals.

Grant only the connections the agent should use and configure DAM network
access separately. DAM injects connection credentials at runtime. For a Slack
agent, bind a channel through DAM; the `platform-outbound` MCP server supplies
reply and top-level post tools. For a named channel, CUGA can use
`describe_channel` to find its ID before posting.

Useful checks after creation:

```sh
dam run <agent> --prompt 'Reply with one short sentence.'
dam session list <agent> --json
dam chat <agent>
dam skill list <agent> --json
dam schedule create <agent> --name smoke --task 'Reply with OK.' --every 1m
dam schedule list <agent> --json
```

Delete the smoke schedule after one observed fire. A scheduled task using a
CUGA shell command pauses until someone approves that command. A task using
only DAM MCP tools can complete unattended. Prechecks run before CUGA under
DAM's scheduler and are outside the CUGA shell approval policy; DAM CLI 0.2.23
documents exit 0 as run, exit 1 as skip, and other exits as running the task
despite a broken check.

## Validation record

The focused local ACP and tool suite passed 240 tests on this branch. This
includes MCP server merging and aliasing, dictionary-shaped tool schemas,
session list/load, durable checkpoints, permission handling, and the terminal
console. A published image should additionally pass the local Docker ACP check
and live DAM checks above. Record its digest and those results with the source
commit before sharing it.
