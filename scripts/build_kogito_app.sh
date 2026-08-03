#!/usr/bin/env bash
#
# Generate and build a Kogito/Quarkus service for a CUGA FLO app.
#
# An app is authored entirely under docs/examples/flow_agent_app_inline/<app-name>/:
# config/ holds its yaml, the clean BPMN, and one or more *-kogito.bpmn models;
# policies/ holds the markdown. This script turns that into a runnable Quarkus service
# by combining the app's Kogito models with the shared CUGA FLO Kogito runtime
# (CugaFlo.java, FlowRedirect.java) and project scaffolding from
# src/cuga/backend/server/kogito/.
#
# The runtime Java is deliberately shared, not per-app: it is parameterised entirely
# through the arguments the BPMN script tasks pass (task ids, hook ids, flow JSON), so
# every app would otherwise carry a byte-identical copy.
#
# Usage:
#   scripts/build_kogito_app.sh <app-name> [--out DIR] [--port N] [--no-build] [--clean]
#
# Examples:
#   scripts/build_kogito_app.sh loan_approval_kogito
#   scripts/build_kogito_app.sh loan_approval_kogito --port 8082 --out /tmp/svc
#
# Then:
#   <out>/run.sh                                          # generated; pins JAVA_HOME
#   python docs/examples/flow_agent_app_inline/run.py <app-name>

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$REPO_ROOT/src/cuga/backend/server/kogito"
APPS_DIR="$REPO_ROOT/docs/examples/flow_agent_app_inline"

die() { echo "error: $*" >&2; exit 1; }

# ── arguments ────────────────────────────────────────────────────────────────

[[ $# -ge 1 ]] || die "usage: $(basename "$0") <app-name> [--out DIR] [--port N] [--no-build] [--clean]"

APP_NAME="$1"; shift
OUT_DIR=""
HTTP_PORT=""
RUN_BUILD=1
CLEAN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out)      OUT_DIR="${2:-}"; shift 2 ;;
        --port)     HTTP_PORT="${2:-}"; shift 2 ;;
        --no-build) RUN_BUILD=0; shift ;;
        --clean)    CLEAN=1; shift ;;
        *)          die "unknown option: $1" ;;
    esac
done

APP_DIR="$APPS_DIR/$APP_NAME"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/build/kogito/$APP_NAME}"

[[ -d "$APP_DIR" ]] || die "no app '$APP_NAME' under $APPS_DIR"

# ── locate the app's Kogito models ───────────────────────────────────────────

shopt -s nullglob
MODELS=("$APP_DIR"/config/*-kogito.bpmn)
shopt -u nullglob

if [[ ${#MODELS[@]} -eq 0 ]]; then
    die "no *-kogito.bpmn in $APP_DIR/config
     The clean BPMN alone is not enough: a Kogito model needs script tasks calling
     CugaFlo/FlowRedirect. See loan_approval_kogito for a worked example."
fi

# ── config / model consistency ───────────────────────────────────────────────
# Every name in the YAML `variables:` block must be declared as a <bpmn2:property> in the
# Kogito model. Kogito's generated model is a closed typed class, so a value sent for an
# undeclared name is discarded on arrival — silently, with no error anywhere.

declared_props=$(grep -hoE '<bpmn2:property id="[^"]+"' "${MODELS[@]}" | sed 's/.*id="//; s/"$//' | sort -u)
yaml_vars=$(awk '
    /^variables:/ { inblock = 1; next }
    /^[a-zA-Z]/   { inblock = 0 }
    inblock && /^[[:space:]]+[a-zA-Z_][a-zA-Z0-9_]*:/ {
        sub(/^[[:space:]]+/, ""); sub(/:.*/, ""); print
    }
' "$APP_DIR"/config/*.yaml | sort -u)

missing=$(comm -23 <(echo "$yaml_vars") <(echo "$declared_props") | tr '\n' ' ' | sed 's/ *$//')
if [[ -n "$missing" ]]; then
    echo "  warning no <bpmn2:property> for YAML variable(s): $missing" >&2
    echo "          values for these are dropped by Kogito on arrival; declare them in" >&2
    echo "          the model or remove them from the app's variables: block" >&2
fi

# Port: --port wins, else the app's workflow_engine.url, else 8081.
if [[ -z "$HTTP_PORT" ]]; then
    HTTP_PORT="$(grep -hoE '^[[:space:]]*url:[[:space:]]*https?://[^:]+:[0-9]+' "$APP_DIR"/config/*.yaml 2>/dev/null \
                 | grep -oE '[0-9]+$' | head -1 || true)"
    HTTP_PORT="${HTTP_PORT:-8081}"
fi

# ── java toolchain ───────────────────────────────────────────────────────────
# JDK 17+ is required. On macOS the Homebrew openjdk@17 formula is keg-only, so it is
# invisible to /usr/libexec/java_home and has to be found by path.

if [[ -z "${JAVA_HOME:-}" ]] || ! "${JAVA_HOME}/bin/java" -version 2>&1 | grep -qE '"(1[7-9]|[2-9][0-9])'; then
    for candidate in /opt/homebrew/opt/openjdk@17 /usr/local/opt/openjdk@17 \
                     /Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home; do
        if [[ -x "$candidate/bin/java" ]]; then
            export JAVA_HOME="$candidate"
            break
        fi
    done
fi
[[ -n "${JAVA_HOME:-}" && -x "$JAVA_HOME/bin/java" ]] || die "no JDK 17+ found; set JAVA_HOME"
export PATH="$JAVA_HOME/bin:$PATH"

command -v mvn >/dev/null || die "maven not on PATH"

# ── scaffold ─────────────────────────────────────────────────────────────────

[[ $CLEAN -eq 1 ]] && rm -rf "$OUT_DIR"

JAVA_DIR="$OUT_DIR/src/main/java/org/cuga"
# Kogito codegen only accepts resources physically under src/main/resources — it
# resolves symlinks and rejects outside paths — so the models are copied, not linked.
RES_DIR="$OUT_DIR/src/main/resources/org/cuga"
mkdir -p "$JAVA_DIR" "$RES_DIR"

sed "s|@ARTIFACT_ID@|cuga-kogito-${APP_NAME//_/-}|g" \
    "$RUNTIME_DIR/pom.xml.template" > "$OUT_DIR/pom.xml"
sed "s|@HTTP_PORT@|$HTTP_PORT|g" \
    "$RUNTIME_DIR/application.properties.template" > "$OUT_DIR/src/main/resources/application.properties"

cp "$RUNTIME_DIR/CugaFlo.java" "$RUNTIME_DIR/FlowRedirect.java" "$JAVA_DIR/"

for model in "${MODELS[@]}"; do
    cp "$model" "$RES_DIR/$(basename "$model")"
    echo "  model   $(basename "$model")"
done

# Pin JAVA_HOME into a run script. Without it `java -jar` picks up whatever java is on
# PATH — commonly a system JDK 11 that fails with UnsupportedClassVersionError 61.0,
# since JAVA_HOME exported here does not survive into the caller's shell.
cat > "$OUT_DIR/run.sh" <<RUNSH
#!/usr/bin/env bash
# Generated by scripts/build_kogito_app.sh — starts the $APP_NAME Kogito service.
set -euo pipefail
export JAVA_HOME="$JAVA_HOME"
export PATH="\$JAVA_HOME/bin:\$PATH"
exec java -jar "$OUT_DIR/target/quarkus-app/quarkus-run.jar" "\$@"
RUNSH
chmod +x "$OUT_DIR/run.sh"

echo "  app     $APP_NAME"
echo "  out     $OUT_DIR"
echo "  port    $HTTP_PORT"
echo "  java    $JAVA_HOME"

# ── build ────────────────────────────────────────────────────────────────────

if [[ $RUN_BUILD -eq 0 ]]; then
    echo "scaffolded (--no-build); run: mvn -f $OUT_DIR/pom.xml package"
    exit 0
fi

echo "building..."
if ! mvn -B -f "$OUT_DIR/pom.xml" clean package -DskipTests > "$OUT_DIR/build.log" 2>&1; then
    echo "build FAILED — last errors:" >&2
    grep -E "^\[ERROR\]" "$OUT_DIR/build.log" | head -10 >&2
    echo "full log: $OUT_DIR/build.log" >&2
    exit 1
fi

cat <<EOF

built. run it with:

  $OUT_DIR/run.sh

then, with the service up:

  python docs/examples/flow_agent_app_inline/run.py $APP_NAME
EOF
