# CUGA in DAM

This directory makes CUGA a DAM agent. The repository root carries a DAM
starter kit, [`kit.yaml`](../../kit.yaml), and this directory builds the image
it names. A DAM user creates a CUGA agent from the kit, by its catalog entry or
by pasting this repository's link into DAM's Starter kits page.

## What the agent does

| Capability | How it works |
| --- | --- |
| Web chat, Slack, `dam run` | DAM starts `harness-chat`, which runs the DAM integration's `cuga_dam.entrypoint` through CUGA's shared ACP stdio host. |
| Saved conversations | ACP `session/list` and `session/load` read `~/.cuga/acp-sessions.sqlite3`; LangGraph checkpoints are in `~/.cuga/acp-checkpoints.sqlite3`. Both live in the agent's home, so they survive a restart. |
| Models | Every call goes to DAM's Model Router on the agent's loopback, which holds the provider credentials. The agent runs on one model: `CUGA_MODEL` in the agent's environment, a provider-qualified name such as `cb/<model>`. Left empty, CUGA takes the first Chat Completions model the agent's providers list. DAM offers no model picker for CUGA. |
| Spend | CUGA sends the ACP session id in `x-platform-session` on each model call (`CUGA_SESSION_HEADER`), so DAM books every call to its session. |
| Tools and connections | The DAM integration takes the HTTP/SSE MCP servers of the session and DAM's `~/.mcp.json`, and exposes each tool under a Python-safe name. |
| Instructions | At the start of each session CUGA reads the files `CUGA_INSTRUCTION_FILES` lists: DAM's `/etc/AGENTS.md`, the owner's `~/AGENTS.md` and `~/work/AGENTS.md`. An edit applies from the next session. |
| Skills | CUGA's skills root is `~/.agents/skills`, where DAM installs the agent's skills. |
| Shell approvals | A session setting, **Approvals**: *Auto-approve* (default) runs shell commands, *Ask every time* asks before each `run_command`. An asking session runs in a CUGA process of its own, with its own policy folder; nobody answers on a schedule or Slack turn, so DAM refuses *Ask* there. |
| `dam chat` | DAM's terminal runs `harness-terminal`, the DAM integration's CUGA text console with `:new` and `:quit`. |

Image, audio and browser input are not advertised.

## The image

The [Dockerfile](Dockerfile) starts from DAM's default agent image, pinned by
digest, and installs CUGA with its `acp` extra into `/opt/cuga/.venv` on that
image's Python. It leaves out the large packages `Dockerfile.ubi.slim` leaves
out, adds the DAM integration's SQLite packages ([requirements.txt](requirements.txt)),
and caches the policy embedding model at build time. `rootfs/` holds what it
adds: the runtime manifest at `/app/runtime-manifest.yaml`, the launchers and
the default shell approval policy. `PLATFORM_HARNESS=cuga`, so DAM's runtime
runs CUGA as the agent's one harness. No credential is baked in.

The DAM integration itself, [`cuga_dam/`](cuga_dam/), is copied to
`/opt/cuga/dam-integration`: the session store and checkpoints, DAM's MCP
servers and Slack instructions, and the terminal console. CUGA's own
`cuga-acp` keeps its default capabilities unless a host supplies these.

Bump the base digest when DAM releases a new default image, so the agent gets
the current DAM runtime.

## Build and check locally

```sh
revision=$(git rev-parse HEAD)
docker buildx build --platform linux/amd64 \
  --file deployment/dam/Dockerfile \
  --build-arg "CUGA_SOURCE_COMMIT=$revision" \
  --tag cuga-dam:dev --load .

python deployment/dam/local_acp_client.py --image cuga-dam:dev --check
```

The check needs Docker and the `acp` extra in the local checkout; it calls no
model. To send a prompt, pass a real `OPENAI_BASE_URL`, `OPENAI_API_KEY` and
`CUGA_MODEL` and `--prompt`.

## Release

A release publishes `ghcr.io/cuga-project/cuga:<version>-dam` next to the full
and slim images, and `kit.yaml` names that tag. Bump the tag in `kit.yaml` in
the release that publishes it.
