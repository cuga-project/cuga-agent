#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RELEASE_NAME="postgres-pgvector"
SECRET_NAME="postgres-pgvector-secrets"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  ENV_FILE="$SCRIPT_DIR/.env"
else
  ENV_FILE="${PROJECT_ROOT}/.env"
fi

cd "$PROJECT_ROOT"

echo "==> Cleaning up..."
helm uninstall "$RELEASE_NAME" 2>/dev/null || true
kubectl delete secret "$SECRET_NAME" 2>/dev/null || true
kubectl delete pvc "$RELEASE_NAME" 2>/dev/null || true

echo "==> Loading .env from $ENV_FILE..."
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: .env not found. Create deployment/.env or .env with POSTGRES_PASSWORD"
  echo "  cp deployment/.env.example deployment/.env"
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

PASSWORD="${POSTGRES_PASSWORD:-}"

if [[ -z "$PASSWORD" ]]; then
  echo "Error: Need POSTGRES_PASSWORD in .env"
  exit 1
fi

echo "==> Creating secret..."
kubectl create secret generic "$SECRET_NAME" --from-literal=password="$PASSWORD"

echo "==> Deploying postgres-pgvector..."
helm install "$RELEASE_NAME" "$SCRIPT_DIR/helm/postgres-pgvector" \
  --set image.pullPolicy=IfNotPresent \
  --set auth.existingSecret="$SECRET_NAME" \
  --set persistence.enabled=true

echo ""
echo "==> Done. Port-forward: kubectl port-forward svc/$RELEASE_NAME 5432:5432"
echo "    postgres_url: postgresql://cuga:\$POSTGRES_PASSWORD@localhost:5432/cuga"
