#!/usr/bin/env bash
# Build cuga wheel and install it with uv pip from an empty /tmp directory (no project context).
# This approximates PyPI / uvx resolution — [tool.uv] overrides in the repo are NOT applied.
#
# For "cuga start demo_crm" smoke, set the same env as CI (see .github/workflows/smoke-pip-install.yml),
# at minimum: GROQ_API_KEY. Optional: MODEL_NAME, AGENT_SETTING_CONFIG, OPENAI_API_KEY, OPENAI_BASE_URL.
# Success: GET OPENAPI_URL (default http://localhost:7860/openapi.json) returns HTTP 200.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${PYTHON:-3.12}"
SKIP_BUILD=0

usage() {
  echo "Usage: $0 [--no-build] [--python X.Y]"
  echo "  --no-build   Use existing dist/*.whl (skip uv build)"
  echo "  --python     Python version for venv (default: 3.12)"
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build)
      SKIP_BUILD=1
      shift
      ;;
    --python)
      shift
      PYTHON="${1:?--python requires a version}"
      shift
      ;;
    -h | --help) usage ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done

cd "$REPO_ROOT"

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "==> Building wheel in $REPO_ROOT/dist"
  uv build -o dist
fi

shopt -s nullglob
wheels=( "$REPO_ROOT"/dist/cuga-*-py3-none-any.whl )
shopt -u nullglob

if [[ ${#wheels[@]} -ne 1 ]]; then
  echo "error: expected exactly one dist/cuga-*-py3-none-any.whl, found ${#wheels[@]}" >&2
  exit 1
fi
WHEEL="${wheels[0]}"
echo "==> Wheel: $WHEEL"

WORK_DIR="$(mktemp -d /tmp/cuga-pip-smoke.XXXXXX)"
cleanup() {
  if [[ "${KEEP_WORK_DIR:-0}" == "1" ]]; then
    echo "==> Kept work dir: $WORK_DIR"
  else
    rm -rf "$WORK_DIR"
  fi
}
trap cleanup EXIT

cd "$WORK_DIR"
echo "==> Isolated install dir (no pyproject): $WORK_DIR"

echo "==> Creating venv (python $PYTHON)"
uv venv .venv --python "$PYTHON"

echo "==> uv pip install (PyPI-style; no --project)"
cd "$WORK_DIR"
uv pip install --python "$WORK_DIR/.venv/bin/python" "$WHEEL"

echo "==> Smoke import"
"$WORK_DIR/.venv/bin/python" -c "import cuga; from importlib.metadata import version; print('import ok, cuga', version('cuga'))"

echo "==> Smoke: cuga start demo_crm (OpenAPI must become reachable, then stop)"
# Env is expected from the caller (e.g. GitHub Actions secrets → env), same pattern as .github/workflows/tests.yml
if [[ -z "${GROQ_API_KEY:-}" ]]; then
  echo "error: GROQ_API_KEY must be set for this smoke test" >&2
  exit 1
fi
command -v curl >/dev/null 2>&1 || {
  echo "error: curl is required to probe OpenAPI" >&2
  exit 1
}

# Stop must be the same process as start (direct_processes is in-memory), so we signal the CLI.
CUGA_BIN="$WORK_DIR/.venv/bin/cuga"
export PATH="$WORK_DIR/.venv/bin:${PATH}"
cd "$WORK_DIR"

OPENAPI_URL="${OPENAPI_URL:-http://localhost:7860/openapi.json}"
OPENAPI_MAX_WAIT="${OPENAPI_MAX_WAIT:-600}"

"$CUGA_BIN" start demo_crm &
_cuga_pid=$!

_deadline=$(( $(date +%s) + ${OPENAPI_MAX_WAIT} ))
_ok=0
while (( $(date +%s) < _deadline )); do
  if ! kill -0 "${_cuga_pid}" 2>/dev/null; then
    echo "error: cuga start demo_crm exited before ${OPENAPI_URL} became ready (see logs above)" >&2
    wait "${_cuga_pid}" 2>/dev/null || true
    exit 1
  fi
  if curl -sfS --connect-timeout 2 --max-time 15 "${OPENAPI_URL}" >/dev/null 2>&1; then
    echo "==> OpenAPI reachable: ${OPENAPI_URL} (success)"
    _ok=1
    break
  fi
  sleep 3
done

if [[ "${_ok}" -ne 1 ]]; then
  echo "error: OpenAPI not reachable at ${OPENAPI_URL} within ${OPENAPI_MAX_WAIT}s" >&2
  kill -TERM "${_cuga_pid}" 2>/dev/null || true
  wait "${_cuga_pid}" 2>/dev/null || true
  exit 1
fi

if kill -0 "${_cuga_pid}" 2>/dev/null; then
  kill -TERM "${_cuga_pid}" 2>/dev/null || true
  wait "${_cuga_pid}" 2>/dev/null || true
fi

echo "==> Done. Set KEEP_WORK_DIR=1 to preserve $WORK_DIR"
