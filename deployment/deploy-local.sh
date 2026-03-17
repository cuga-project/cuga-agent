#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

AGENT_CONFIG=""
while [[ $# -gt 0 ]]; do
  case $1 in
    -c|--agent-config)
      AGENT_CONFIG="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [-c|--agent-config CONFIG]"
      echo "  -c, --agent-config  Override AGENT_SETTING_CONFIG (e.g. settings.groq.toml, settings.openai.toml)"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  ENV_FILE="$SCRIPT_DIR/.env"
else
  ENV_FILE="${PROJECT_ROOT}/.env"
fi
SECRET_NAME="cuga-secrets"
RELEASE_NAME="cuga"

cd "$PROJECT_ROOT"

echo "==> Cleaning up..."
helm uninstall "$RELEASE_NAME" 2>/dev/null || true
kubectl delete secret "$SECRET_NAME" 2>/dev/null || true
kubectl delete pvc "${RELEASE_NAME}-dbs" 2>/dev/null || true

echo "==> Loading .env from $ENV_FILE..."
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: .env not found. Create deployment/.env or .env with GROQ_API_KEY or OPENAI_API_KEY"
  echo "  cp .env.example .env   # or  cp deployment/.env.example deployment/.env"
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

GROQ_KEY="${GROQ_API_KEY:-}"
OPENAI_KEY="${OPENAI_API_KEY:-}"

if [[ -z "$GROQ_KEY" && -z "$OPENAI_KEY" ]]; then
  echo "Error: Need GROQ_API_KEY (default) or OPENAI_API_KEY in .env"
  exit 1
fi

[[ -n "$GROQ_KEY" ]] && DEFAULT_AGENT_CONFIG="settings.groq.toml" || DEFAULT_AGENT_CONFIG="settings.openai.toml"

echo "==> Building image..."
docker build -t cuga-agent:latest .

if command -v kind &>/dev/null && kubectl config current-context 2>/dev/null | grep -q kind; then
  echo "==> Loading image into kind..."
  kind load docker-image cuga-agent:latest
fi

echo "==> Creating secret..."
SECRET_ARGS=()
[[ -n "$GROQ_KEY" ]] && SECRET_ARGS+=(--from-literal=GROQ_API_KEY="$GROQ_KEY")
[[ -n "$OPENAI_KEY" ]] && SECRET_ARGS+=(--from-literal=OPENAI_API_KEY="$OPENAI_KEY")
[[ -n "$OPENAI_BASE_URL" ]] && SECRET_ARGS+=(--from-literal=OPENAI_BASE_URL="$OPENAI_BASE_URL")

kubectl create secret generic "$SECRET_NAME" "${SECRET_ARGS[@]}"

AGENT_SETTING="${AGENT_CONFIG:-${AGENT_SETTING_CONFIG:-$DEFAULT_AGENT_CONFIG}}"
echo "==> Deploying (AGENT_SETTING_CONFIG=$AGENT_SETTING)..."
helm install "$RELEASE_NAME" "$SCRIPT_DIR/helm/cuga" \
  --set image.pullPolicy=Never \
  --set existingSecret="$SECRET_NAME" \
  --set env.MODEL_NAME="${MODEL_NAME:-openai/gpt-oss-120b}" \
  --set env.AGENT_SETTING_CONFIG="$AGENT_SETTING" \
  --set persistence.dbs.enabled=true

echo ""
echo "==> Done. Access with: kubectl port-forward svc/$RELEASE_NAME 7860:7860"
echo "    Then open http://localhost:7860"
