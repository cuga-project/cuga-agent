# Single-image compliance PoC

`Dockerfile.compliance-poc` packages CUGA, its tool registry, Evolve MCP,
and Activepieces on Red Hat UBI 9 minimal. It is intended for a stakeholder PoC
deployed as one Kubernetes pod and one replica.

The image is rootless. A small PID 1 supervisor (`cuga-poc-supervisor`)
starts the six services in dependency order, gates each on the readiness
probe its predecessor used, restarts long-running services on failure, and
reaps orphans. Every service shares the container UID and group 0, so the
image runs unchanged under an arbitrary assigned UID.

Activepieces normally resolves its catalog from `cloud.activepieces.com`
and pulls each package from npm; both are baked in at build time instead, so
the piece catalog needs no egress. See "Offline pieces" below. The pod does
still reach out for other things — see "Egress" for the full list.

Activepieces uses its supported PGLite database mode and a Redis process
inside the same image. That keeps the PoC in one pod, but it is not the
production Activepieces topology and must not be scaled horizontally.

The image preloads the FastEmbed model used by the compliance experience,
but omits Docling's offline document-processing models. Document ingestion
is outside this PoC's deployment profile.

## Build

```sh
docker build -f Dockerfile.compliance-poc -t cuga-compliance-poc:dev .
```

The image supports `linux/amd64` (x86_64) and `linux/arm64`. Publish both
under one image tag with a Buildx manifest:

```sh
make compliance-poc-image-multiarch \
  IMAGE=registry.example.com/cuga-compliance-poc:VERSION
```

The build pins Activepieces `0.82.0` and Evolve commit
`af3e1310134b3397302e260cb3504b23a732fead`. Override either source with the
`ACTIVEPIECES_IMAGE`, `EVOLVE_REPO`, or `EVOLVE_REF` build arguments.

## Run locally

```sh
docker run --rm --name cuga-compliance-poc \
  -p 7860:7860 -p 8081:8081 -p 8201:8201 \
  -v cuga-compliance-poc-data:/data \
  --env-file .env \
  cuga-compliance-poc:dev
```

CUGA is available at `http://localhost:7860/chat`; the event studio is at
`http://localhost:7860/studio`. Activepieces is at `http://localhost:8081`.
The tool registry listens internally on port `8001`.

Generated secrets are written once to `/data/cuga-poc/secrets.env`. The
default Activepieces email is `admin@cuga.local`; retrieve its generated
password with:

```sh
docker exec cuga-compliance-poc \
  sed -n 's/^AP_PASSWORD=//p' /data/cuga-poc/secrets.env
```

Pass explicit `AP_PASSWORD`, `AP_ENCRYPTION_KEY`, `AP_JWT_SECRET`,
`EVENTS_WEBHOOK_KEY`, and `GATEWAY_TOKEN` values to use Kubernetes-managed
secrets instead. `EVENTS_WEBHOOK_KEY` is generated rather than left empty
because an empty value makes `POST /api/events/hook/<name>` accept any
request; retrieve it the same way as the Activepieces password when calling a
hook by hand.

Health does not cover the model endpoint, so the pod reports healthy with a
wrong or missing one and fails at the first chat instead. Set `MODEL_NAME`
and `OPENAI_BASE_URL` together — with no base URL the client defaults to
`api.openai.com`, which will not serve a gateway-hosted model.

A fresh volume no longer waits on the cloud piece catalog, so start-up is
bounded by Activepieces initialising its PGLite database and the baked piece
archives installing locally. The health budgets keep their old headroom
regardless. Docker reports the container as `healthy` only after the tool
registry, the tools-list API, Evolve, CUGA, Redis, and the published retention
schedule all pass the bundled `cuga-poc-health` check.

## Offline pieces

`AP_PIECES_SYNC_MODE=NONE` stops Activepieces contacting
`cloud.activepieces.com`. The pieces are baked into `/etc/cuga-poc/pieces` as
npm tarballs and installed by `activepieces-bootstrap` through the local API
as `ARCHIVE` packages, so neither the cloud catalog nor npm is
reachable-or-required at run time.

Versions come from the `PINNED` table in `scripts/ap_pieces.py`, read at
build time by `fetch-piece-archives.py`, so the image and the local
development flow cannot drift apart. Adding a piece means adding it to
`PINNED` and rebuilding.

`PINNED` must list every piece the runtime can reference, not just the ones
the events layer installs explicitly. With the cloud sync on, the full
catalog arrived and covered the difference; with it off, a missing piece
fails when a flow is built — `AP piece '<name>' has no resolvable version` —
rather than at start-up. `piece-http` backs every flow's callback action, so
the retention schedule cannot publish without it, and an unpublished
schedule fails the health check and therefore the liveness probe.

Archive installs register as `CUSTOM` rather than `OFFICIAL` pieces. That is
deliberate: the cloud sync's reaper only deletes `OFFICIAL` pieces missing
from the cloud registry, so the baked pieces survive even if syncing is
turned back on.

Note that `AP_PIECES_SOURCE` is not read by Activepieces 0.82 — only
`AP_PIECES_SYNC_MODE` is. The image previously set the former, which had no
effect.

## Egress

The piece catalog is baked in, but the pod is not fully self-contained. It
reaches these hosts at run time:

- The model endpoint that `LLM_PROVIDER` / `OPENAI_BASE_URL` point at. With no
  `OPENAI_BASE_URL` set, the client defaults to `api.openai.com`, which is
  wrong for a gateway-hosted model and fails only when a chat is attempted,
  not at startup.
- Seven CUGA tool MCP servers on `*.codeengine.appdomain.cloud`, listed in
  `mcp_servers_cuga_apps.yaml`. The registry starts either way, but a blocked
  cluster gets a registry with no tools, and the health check fails because
  `/api/tools/list` returns nothing usable.

Nothing else leaves the pod. In particular, no request reaches
`cloud.activepieces.com` or `registry.npmjs.org`.

## Kubernetes contract

- Run exactly one replica with a recreate deployment strategy.
- Mount a persistent volume at `/data`. It holds CUGA databases, Evolve
  memory, Activepieces PGLite data, and generated secrets.
- Expose port `7860` for the product UI. Ports `8081` and `8201` are only
  needed for direct Activepieces or Evolve diagnostics.
- Supply model credentials through pod environment variables or a Secret.
- Allow the egress listed below, or expect degraded tool availability.
- No security context is required. The image runs as any non-root UID with
  group 0 as its primary group and never writes outside `/data`.
- Probe the image with its built-in health command:
  `/usr/local/bin/cuga-poc-health`. It names the failing dependency on stdout
  (`unhealthy: retention-scheduler(...)`), and Kubernetes copies that into the
  probe's pod event, so the OpenShift console shows which service is down.
  Its curl budgets total roughly 33s, so keep `timeoutSeconds` above that.

## OpenShift

`deployment/compliance-poc/openshift.yaml` deploys under the default
`restricted-v2` SCC. No SCC binding, no dedicated service account, and no
cluster-admin involvement:

```sh
oc apply -f deployment/compliance-poc/openshift.yaml
```

Three properties make that work, and breaking any of them reintroduces the
root requirement:

- No service users. Every process runs as the assigned UID. CUGA and
  Activepieces are not isolated from each other — an acceptable trade for a
  single-pod PoC, and the reason this image must not be treated as a
  production topology.
- Group 0 owns everything writable. The build mirrors owner permissions onto
  the group (`chmod -R g=u`) and `cuga-poc-prepare` creates `/data`
  directories setgid, so state stays shared no matter which UID is assigned.
- The entrypoint appends its own `/etc/passwd` entry. OpenShift's UID has no
  account in the image, which breaks tools that resolve the user by uid.

Do not pin `runAsUser` or `fsGroup` in the manifest. OpenShift fills both from
the namespace range, and hardcoding either is what breaks portability between
clusters. Secrets in `/data/cuga-poc/secrets.env` are written `0660` so a pod
that comes back under a different UID can still read them through group 0.

Deleting the `/data` volume intentionally creates a fresh PoC. On the next
start the image regenerates credentials, creates the Activepieces owner,
materializes required pieces, starts Evolve with PII hooks, and lets CUGA
seed the same deterministic demonstration conversations and memories.
