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
