#!/usr/bin/env bash
# memray_profile.sh — Lens C: native memory flamegraph driver for the CUGA
# memory-profiling harness.
#
# Wraps `memray run [--native]` for two targets:
#   Target 1 — SDK worker: import + construct CugaAgent with GenericFakeChatModel
#   Target 2 — uvicorn server: cuga.backend.server.main:app on an ephemeral port
#
# Output files go to results/ with timestamped names:
#   memray-sdk-<ts>.bin       raw allocation trace
#   memray-sdk-<ts>.html      flamegraph
#   memray-server-<ts>.bin
#   memray-server-<ts>.html
#
# If memray is not installed, prints an actionable install hint and exits 0.
# The test suite must stay green without the optional dep.
#
# Usage:
#   bash src/system_tests/profiling/memory/memray_profile.sh
#   bash src/system_tests/profiling/memory/memray_profile.sh --sdk-only
#   bash src/system_tests/profiling/memory/memray_profile.sh --server-only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results"
TS=$(date +%Y%m%dT%H%M%S)-$$

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
RUN_SDK=true
RUN_SERVER=true

for arg in "$@"; do
    case "$arg" in
        --sdk-only)    RUN_SERVER=false ;;
        --server-only) RUN_SDK=false ;;
        --help|-h)
            echo "Usage: $0 [--sdk-only | --server-only]"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            echo "Usage: $0 [--sdk-only | --server-only]" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Graceful exit if memray is not installed
# ---------------------------------------------------------------------------
if ! uv run python -c "import memray" 2>/dev/null; then
    echo "memray not installed. Run: uv sync --extra profiling" >&2
    exit 0
fi

mkdir -p "$RESULTS_DIR"

# ---------------------------------------------------------------------------
# Native-mode probe: try --native once; fall back if it errors or is unsupported
# ---------------------------------------------------------------------------
NATIVE_FLAG="--native"
NATIVE_BANNER=""

_probe_file="$RESULTS_DIR/.memray-native-probe-$TS.bin"
if ! uv run memray run --native --output "$_probe_file" \
        -- python -c "pass" 2>/dev/null; then
    NATIVE_FLAG=""
    NATIVE_BANNER="[WARNING] memray --native is not supported on this platform/OS.
  Native C-extension stack frames will be MISSING from the flamegraph.
  Attribution for C allocations (e.g. torch, onnxruntime) is incomplete.
  On macOS/arm64, --native requires macOS >=12 and a native-code memray wheel.
  Re-run on Linux/x86_64 for full native symbolication."
fi
rm -f "$_probe_file"

if [[ -n "$NATIVE_BANNER" ]]; then
    echo "" >&2
    echo "================================================================" >&2
    echo "$NATIVE_BANNER" >&2
    echo "================================================================" >&2
    echo "" >&2
fi

# ---------------------------------------------------------------------------
# Helper: run memray + generate flamegraph + print summary
# ---------------------------------------------------------------------------
_run_target() {
    local label="$1"      # "sdk" or "server"
    local bin_file="$2"   # path to .bin output
    local html_file="$3"  # path to .html output
    shift 3
    # Remaining args: the command to profile

    echo "" >&2
    echo "=== memray: profiling $label ===" >&2
    echo "  output: $bin_file" >&2

    # Run memray; pass through stderr so failures are visible.
    # shellcheck disable=SC2086
    uv run memray run $NATIVE_FLAG --output "$bin_file" -- "$@"

    echo "" >&2
    echo "=== memray: generating flamegraph for $label ===" >&2
    uv run memray flamegraph --output "$html_file" "$bin_file"
    echo "  flamegraph: $html_file" >&2

    echo "" >&2
    echo "=== memray: summary for $label ===" >&2
    uv run memray summary "$bin_file"
}

# ---------------------------------------------------------------------------
# Target 1: SDK worker
# ---------------------------------------------------------------------------
if [[ "$RUN_SDK" == "true" ]]; then
    SDK_BIN="$RESULTS_DIR/memray-sdk-${TS}.bin"
    SDK_HTML="$RESULTS_DIR/memray-sdk-${TS}.html"

    # Inline worker script: import + construct CugaAgent; no real LLM calls.
    SDK_SCRIPT=$(cat <<'PYEOF'
import itertools
from cuga import CugaAgent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

_canned = itertools.cycle([AIMessage(content="ok")])
model = GenericFakeChatModel(messages=_canned)
agent = CugaAgent(tools=[], model=model)
PYEOF
)

    _run_target "sdk" "$SDK_BIN" "$SDK_HTML" \
        python -c "$SDK_SCRIPT"
fi

# ---------------------------------------------------------------------------
# Target 2: uvicorn server — run for 5 seconds then kill
# ---------------------------------------------------------------------------
if [[ "$RUN_SERVER" == "true" ]]; then
    SERVER_BIN="$RESULTS_DIR/memray-server-${TS}.bin"
    SERVER_HTML="$RESULTS_DIR/memray-server-${TS}.html"

    # Pick an ephemeral port (Python chooses one then releases it — close enough
    # for this purpose; a collision would just fail the profiling run, not the suite).
    EPHEMERAL_PORT=$(uv run python -c "
import socket
s = socket.socket()
s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
")

    echo "" >&2
    echo "=== memray: profiling server on port $EPHEMERAL_PORT ===" >&2

    # Run uvicorn under memray; time-box at 15 s with a background kill.
    SERVER_BIN_TMP="$SERVER_BIN"
    # shellcheck disable=SC2086
    uv run memray run $NATIVE_FLAG --output "$SERVER_BIN_TMP" \
        -- python -m uvicorn cuga.backend.server.main:app \
        --host 127.0.0.1 --port "$EPHEMERAL_PORT" \
        --timeout-graceful-shutdown 2 2>&1 &
    MEMRAY_PID=$!
    sleep 10
    # Kill the server; ignore "already exited" but propagate unexpected failures.
    kill "$MEMRAY_PID" 2>/dev/null || true
    wait "$MEMRAY_PID"
    _exit=$?
    # 143 = SIGTERM (expected); 0 = clean exit (also fine); anything else = failure.
    if [[ $_exit -ne 0 && $_exit -ne 143 ]]; then
        echo "[WARNING] memray/uvicorn exited with unexpected status $_exit" >&2
    fi

    if [[ -f "$SERVER_BIN" ]]; then
        echo "" >&2
        echo "=== memray: generating flamegraph for server ===" >&2
        uv run memray flamegraph --output "$SERVER_HTML" "$SERVER_BIN"
        echo "  flamegraph: $SERVER_HTML" >&2

        echo "" >&2
        echo "=== memray: summary for server ===" >&2
        uv run memray summary "$SERVER_BIN"
    else
        echo "[WARNING] Server .bin not produced — server may have failed to start." >&2
    fi
fi

echo "" >&2
echo "=== memray_profile.sh complete. Results in: $RESULTS_DIR ===" >&2
