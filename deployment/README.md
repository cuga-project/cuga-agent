# CUGA Helm Chart

Deploy CUGA agent to Kubernetes.

## Quick Start

```bash
# 1. Create .env with your API key
cp .env.example .env
# Or: cp deployment/.env.example deployment/.env
# Edit and set GROQ_API_KEY or OPENAI_API_KEY

# 2. Run deploy script (cleans, builds, deploys)
./deployment/deploy-local.sh

# 3. Inspect logs until ready (wait for "Manager mode. Press Ctrl+C to stop" and services table)
kubectl logs -l app.kubernetes.io/name=cuga -f

# 4. Access (in another terminal, once Demo is listed)
kubectl port-forward svc/cuga 7860:7860
# Open http://localhost:7860
```

**Options:**
```bash
./deployment/deploy-local.sh -c settings.openai.toml   # use OpenAI instead of Groq
./deployment/deploy-local.sh --help
```

Uses `deployment/.env` or `.env`. Auto-detects kind and loads image. Default: Groq.

## Prerequisites

- Helm 3
- Kubernetes cluster (Docker Desktop, minikube, kind, or cloud)

## Image: Local vs Registry

### Option 1: Local Image

Use when running on Docker Desktop, minikube, or kind with a locally built image.

**Docker Desktop:**
```bash
docker build -t cuga-agent:latest .
helm install cuga ./deployment/helm/cuga --set image.pullPolicy=Never
```

**Minikube:**
```bash
eval $(minikube docker-env)
docker build -t cuga-agent:latest .
helm install cuga ./deployment/helm/cuga --set image.pullPolicy=Never
```

**Kind:**
```bash
docker build -t cuga-agent:latest .
kind load docker-image cuga-agent:latest
helm install cuga ./deployment/helm/cuga --set image.pullPolicy=Never
```

### Option 2: Registry Image

Use for cloud clusters (GKE, EKS, AKS) or when sharing images.

```bash
# Build and push
docker build -t your-registry.io/cuga-agent:latest .
docker push your-registry.io/cuga-agent:latest

# Install
helm install cuga ./deployment/helm/cuga \
  --set image.repository=your-registry.io/cuga-agent \
  --set image.tag=latest
```

## Secrets for API Keys

API keys should not be in values files. Use a Kubernetes Secret.

### Create the secret

```bash
kubectl create secret generic cuga-secrets \
  --from-literal=OPENAI_API_KEY=sk-your-openai-key \
  --from-literal=GROQ_API_KEY=gsk-your-groq-key
```

### Use the secret in the chart

Set `existingSecret` so the chart pulls `OPENAI_API_KEY` and `GROQ_API_KEY` from the secret:

```bash
helm install cuga ./deployment/helm/cuga \
  --set existingSecret=cuga-secrets \
  --set env.MODEL_NAME=llama-3.1-70b-versatile \
  --set env.AGENT_SETTING_CONFIG=settings.groq.toml
```

The secret can contain `OPENAI_API_KEY`, `GROQ_API_KEY`, `OPENAI_BASE_URL`, or any combination. Other env vars (`MODEL_NAME`, `AGENT_SETTING_CONFIG`) come from `values.yaml` or `--set`.

### Alternative: inline env (not recommended)

For quick testing only, you can pass keys via `--set` (they will appear in `helm get values`):

```bash
helm install cuga ./deployment/helm/cuga \
  --set env.OPENAI_API_KEY=sk-xxx \
  --set env.GROQ_API_KEY=gsk_xxx
```

## Full example (local image + secrets)

```bash
# 1. Build image
docker build -t cuga-agent:latest .

# 2. Create secret
kubectl create secret generic cuga-secrets \
  --from-literal=OPENAI_API_KEY=sk-xxx \
  --from-literal=GROQ_API_KEY=gsk-xxx

# 3. Install
helm install cuga ./deployment/helm/cuga \
  --set image.pullPolicy=Never \
  --set existingSecret=cuga-secrets \
  --set env.MODEL_NAME=llama-3.1-70b-versatile \
  --set env.AGENT_SETTING_CONFIG=settings.groq.toml

# 4. Access
kubectl port-forward svc/cuga 7860:7860
# Open http://localhost:7860
```

## Viewing logs and pods

```bash
# List pods
kubectl get pods -l app.kubernetes.io/name=cuga

# Stream logs (follow)
kubectl logs -l app.kubernetes.io/name=cuga -f

# Logs from a specific pod
kubectl logs <pod-name> -f

# Pod details (events, state, restarts)
kubectl describe pod -l app.kubernetes.io/name=cuga

# Exec into a pod
kubectl exec -it <pod-name> -- /bin/sh
```

## Stop and uninstall

```bash
# Stop port-forward (if running): Ctrl+C in the terminal

# Uninstall the release (removes deployment, service, etc.)
helm uninstall cuga

# Optional: delete the secret
kubectl delete secret cuga-secrets
```

## Stop, update secrets, and rerun

```bash
# 1. Uninstall
helm uninstall cuga

# 2. Update or recreate the secret
kubectl delete secret cuga-secrets 2>/dev/null || true
kubectl create secret generic cuga-secrets \
  --from-literal=OPENAI_API_KEY=sk-new-key \
  --from-literal=GROQ_API_KEY=gsk-new-key

# 3. Reinstall
helm install cuga ./deployment/helm/cuga \
  --set image.pullPolicy=Never \
  --set existingSecret=cuga-secrets \
  --set env.MODEL_NAME=llama-3.1-70b-versatile \
  --set env.AGENT_SETTING_CONFIG=settings.groq.toml
```

## Upgrade

```bash
# After changing values or chart
helm upgrade cuga ./deployment/helm/cuga

# With new values
helm upgrade cuga ./deployment/helm/cuga \
  --set env.MODEL_NAME=gpt-4o \
  --set env.AGENT_SETTING_CONFIG=settings.openai.toml

# List releases
helm list
```

## Persistence

The `dbs` directory stores config, policies, conversation history, and memory. A PersistentVolumeClaim is enabled by default so data survives pod restarts.

```yaml
# values.yaml
persistence:
  dbs:
    enabled: true
    size: 1Gi
```

To disable (ephemeral storage): `--set persistence.dbs.enabled=false`

## Troubleshooting

**CreateContainerConfigError** – Usually means the secret doesn't exist. Create it with at least one key (OpenAI or Groq):
```bash
# Groq only
kubectl create secret generic cuga-secrets --from-literal=GROQ_API_KEY=gsk-xxx

# OpenAI only
kubectl create secret generic cuga-secrets --from-literal=OPENAI_API_KEY=sk-xxx

# Both + custom base URL
kubectl create secret generic cuga-secrets \
  --from-literal=OPENAI_API_KEY=sk-xxx \
  --from-literal=GROQ_API_KEY=gsk-xxx \
  --from-literal=OPENAI_BASE_URL=https://your-api.example.com/v1
```

**ImagePullBackOff** – For local images, add `--set image.pullPolicy=Never`. For registry images, ensure the image exists and you're logged in.

**Check pod events:**
```bash
kubectl describe pod -l app.kubernetes.io/name=cuga
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| OPENAI_API_KEY | OpenAI API key (for settings.openai.toml) |
| GROQ_API_KEY | Groq API key (for settings.groq.toml) |
| OPENAI_BASE_URL | Optional. Custom OpenAI-compatible API base URL |
| MODEL_NAME | Model name (e.g. gpt-4o, llama-3.1-70b-versatile) |
| AGENT_SETTING_CONFIG | Config file (settings.groq.toml, settings.openai.toml) |

---

## ☁️ OpenShift Deployment

Deploy CUGA to an OpenShift cluster using the provided Helm chart and deployment script. Each deployment is scoped to an `INSTANCE_ID`, so multiple independent agent instances can coexist in the same namespace.

### Architecture (example: pepsi and coke in one namespace)

One namespace, one shared PostgreSQL, two agent instances (pepsi, coke). Each agent has its own secrets, route (HTTPS), and PVC; both use the same postgres and can use the same OIDC provider for auth.

**View diagram:** [architecture.html](helm/architecture.html) (open in browser)

| Resource | Purpose |
|----------|---------|
| `postgres-secret` | DB password for postgres-pgvector (shared) |
| `postgres-pgvector` | One PostgreSQL + pgvector per namespace |
| `*-icr-pull-secret` | Image pull for `us.icr.io` per instance |
| `*-env-secret` | GROQ_API_KEY, OIDC_*, DYNACONF_STORAGE__POSTGRES_URL, etc. |
| Route | Edge TLS (HTTPS), serves `/`, `/chat`, `/manage` |
| OIDC | Optional; set `DYNACONF_AUTH__ENABLED=true` and OIDC_* in env |

### Prerequisites

- `oc` or `kubectl` CLI, logged in to your cluster (`oc login ...`)
- `helm` 3 installed
- IBM Container Registry API key (for pulling `us.icr.io/nocodeui-automation/cuga`)

### Quick Start

```bash
# 1. Copy the env template and fill in your values
cp deployment/helm/openshift.example.env deployment/helm/my-instance.env
# Edit my-instance.env — set INSTANCE_ID, ICR_API_KEY, GROQ_API_KEY, etc.

# 2. Run the deploy script (local SQLite storage)
./deployment/helm/deploy-openshift.sh deployment/helm/my-instance.env

# Or deploy with PostgreSQL (one shared postgres per namespace, prod storage)
./deployment/helm/deploy-openshift.sh deployment/helm/my-instance.env --with-postgres
```

When using `--with-postgres`, set `POSTGRES_PASSWORD` in your env file. The script will create a `postgres-secret`, deploy the `postgres-pgvector` Helm chart once per namespace, and set `DYNACONF_STORAGE__MODE=prod` and the postgres URL for each cuga instance.

The script will:
1. Create the namespace (idempotent)
2. If `--with-postgres`: create `postgres-secret` and deploy `postgres-pgvector` (shared per namespace)
3. Create an image pull secret for `us.icr.io` using your `ICR_API_KEY`
4. Create a Kubernetes secret with all sensitive env vars (including postgres URL when `--with-postgres`)
5. Deploy the Helm chart (`cuga-$INSTANCE_ID`) with the correct image and config
6. Create an OpenShift Route with edge TLS (HTTPS) and print the access URLs

### Status & inspection

Use the status script to inspect pods, logs, and routes (based on your `openshift.env`):

```bash
./deployment/helm/status-openshift.sh [path/to/openshift.env] [command]
```

| Command    | Description                          |
|------------|--------------------------------------|
| `pods`     | List CUGA pods (default)              |
| `all`      | List cuga + postgres + vault pods     |
| `describe` | Describe CUGA pods                   |
| `logs`     | Pod logs (add `-f` to follow)        |
| `route`    | Show route URL and access links       |
| `events`   | Recent namespace events               |
| `status`   | Helm release status + pod summary     |

```bash
./deployment/helm/status-openshift.sh
./deployment/helm/status-openshift.sh openshift.env logs -f
./deployment/helm/status-openshift.sh openshift.env describe
```

### Access URLs

After deploy, the agent is available at:

```
https://<route-host>/        # Agent chat UI
https://<route-host>/chat    # Chat (client-side route)
https://<route-host>/manage  # Management dashboard
```

HTTP is automatically redirected to HTTPS.

### Multi-instance example

```bash
# Deploy two independent instances into the same namespace
./deployment/helm/deploy-openshift.sh deployment/helm/acme.env    # release: cuga-acme
./deployment/helm/deploy-openshift.sh deployment/helm/ibm.env     # release: cuga-ibm
```

Each instance has its own secrets, PVC, and Route — re-running a script is safe and will perform a rolling upgrade.

### PostgreSQL (shared per namespace)

Use the `--with-postgres` flag to deploy one PostgreSQL (pgvector) instance per namespace, shared by all cuga instances in that namespace. Set `POSTGRES_PASSWORD` in your env file; the script creates `postgres-secret`, deploys `postgres-pgvector`, and sets `DYNACONF_STORAGE__MODE=prod` and the connection URL for each cuga instance. The URL is built as:

`postgresql://<POSTGRES_USER>:<POSTGRES_PASSWORD>@postgres-pgvector.<NAMESPACE>.svc.cluster.local:5432/<POSTGRES_DB>`

To use an external PostgreSQL instead, leave `POSTGRES_PASSWORD` empty, do not use `--with-postgres`, set `DYNACONF_STORAGE__MODE=prod` and `DYNACONF_STORAGE__POSTGRES_URL` in your env file, and add the URL to the secret (e.g. via the existing secret flow).

### Cleanup

```bash
# Remove a specific instance (keeps namespace and postgres intact)
./deployment/helm/cleanup-openshift.sh deployment/helm/my-instance.env

# Also remove the shared postgres (postgres-pgvector + postgres-secret)
./deployment/helm/cleanup-openshift.sh deployment/helm/my-instance.env --with-postgres

# Remove instance and delete the entire namespace
./deployment/helm/cleanup-openshift.sh deployment/helm/my-instance.env --delete-namespace
```

### Configuration reference (`openshift.example.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `INSTANCE_ID` | yes | Unique name for this instance (e.g. `acme`). Names the Helm release `cuga-$INSTANCE_ID` |
| `NAMESPACE` | yes | Kubernetes namespace (default: `cuga`) |
| `ICR_API_KEY` | yes | IBM Container Registry API key for image pull secret |
| `IMAGE_REPOSITORY` | yes | Image repo (default: `us.icr.io/nocodeui-automation/cuga`) |
| `IMAGE_TAG` | yes | Image tag (default: `latest`) |
| `ROUTE_HOSTNAME` | no | Custom hostname — leave empty for OpenShift auto-assign |
| `GROQ_API_KEY` | yes | Groq API key |
| `MODEL_NAME` | yes | LLM model name |
| `AGENT_SETTING_CONFIG` | yes | Settings TOML file (e.g. `settings.groq.toml`) |
| `DYNACONF_AUTH__ENABLED` | no | Enable OIDC auth (default: `true`) |
| `DYNACONF_AUTH__REQUIRE_HTTPS` | no | Enforce HTTPS on cookies and routes (default: `false`) |
| `DYNACONF_AUTH__AUTHORIZATION_ENABLED` | no | Enable role-based authorization (default: `false`) |
| `DYNACONF_AUTH__ROLE_TOKEN_SOURCE` | no | Token used for role checks: `auto` (default), `id_token`, `access_token`, `iam_proxy` |
| `OIDC_CLIENT_ID` | no | OIDC client ID |
| `OIDC_CLIENT_SECRET` | no | OIDC client secret |
| `OIDC_DISCOVERY_URL` | no | OIDC discovery URL |
| `OIDC_REDIRECT_URI` | no | OIDC redirect URI (e.g. `https://<route-host>/manage`) |
| `DYNACONF_AUTH__IAM_PROXY_URL` | no | XPM IAM proxy base URL for service-scoped token exchange (e.g. `https://xpm.apps.example.com/api/v1/iam-proxy`) |
| `DYNACONF_AUTH__IAM_PROXY_SKIP_VERIFY` | no | Skip TLS verification for IAM proxy (dev/fyre only, default: `false`) |
| `DYNACONF_STORAGE__MODE` | no | Storage mode: `local` (default) or `prod`. Set to `prod` automatically when using `--with-postgres` |
| `POSTGRES_PASSWORD` | when `--with-postgres` | Password for the PostgreSQL DB user. Required when using `--with-postgres` |
| `POSTGRES_USER` | no | PostgreSQL username (default: `cuga`) |
| `POSTGRES_DB` | no | PostgreSQL database name (default: `cuga`) |
| `DYNACONF_STORAGE__POSTGRES_URL` | no | PostgreSQL URL. Auto-built when using `--with-postgres`; set only for an external postgres |
