# Single-image compliance PoC

`Dockerfile.compliance-poc` packages CUGA, Evolve MCP, and Activepieces on
Red Hat UBI 9 init. It is intended for a stakeholder PoC deployed as one
Kubernetes pod and one replica.

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

Systemd needs a writable cgroup hierarchy. Docker Desktop can run it with:

```sh
docker run --rm --name cuga-compliance-poc \
  --privileged --cgroupns=host \
  -p 7860:7860 -p 8081:8081 -p 8201:8201 \
  -v cuga-compliance-poc-data:/data \
  --env-file .env \
  cuga-compliance-poc:dev
```

CUGA is available at `http://localhost:7860/chat`; the event studio is at
`http://localhost:7860/studio`. Activepieces is at `http://localhost:8081`.

Generated secrets are written once to `/data/cuga-poc/secrets.env`. The
default Activepieces email is `admin@cuga.local`; retrieve its generated
password with:

```sh
docker exec cuga-compliance-poc \
  sed -n 's/^AP_PASSWORD=//p' /data/cuga-poc/secrets.env
```

Pass explicit `AP_PASSWORD`, `AP_ENCRYPTION_KEY`, `AP_JWT_SECRET`, and
`GATEWAY_TOKEN` values to use Kubernetes-managed secrets instead.

## Kubernetes contract

- Run exactly one replica with a recreate deployment strategy.
- Mount a persistent volume at `/data`. It holds CUGA databases, Evolve
  memory, Activepieces PGLite data, and generated secrets.
- Expose port `7860` for the product UI. Ports `8081` and `8201` are only
  needed for direct Activepieces or Evolve diagnostics.
- Supply model credentials through pod environment variables or a Secret.
- Use a systemd-capable security context and writable cgroup mount. The image
  intentionally uses `ubi9/ubi-init` as PID 1.
- Probe the image with its built-in health command:
  `/usr/local/bin/cuga-poc-health`.

Deleting the `/data` volume intentionally creates a fresh PoC. On the next
start the image regenerates credentials, creates the Activepieces owner,
materializes required pieces, starts Evolve with PII hooks, and lets CUGA
seed the same deterministic demonstration conversations and memories.
