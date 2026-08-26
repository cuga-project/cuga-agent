#!/usr/bin/env bash
set -euo pipefail

# Point a REAL, operator-managed CugaAgent at the PoC image and turn on the
# Context Forge tool browser — keeping its real IBM Verify auth, registered
# redirect URI and account-UI role assignments intact.
#
# This is the alternative to 10-clone-agent.sh. The clone can never use real
# Verify (new hostname, no registered redirect URI, operator DCR doesn't run
# for a Deployment it doesn't own), so roles — ServiceOwner / ServiceAdmin /
# ServiceUser — can only be exercised this way.
#
# NOTE: poc-plan.md claims an image change "would be reverted or blocked by
# the operator". That is not so: patching spec.patches[0] is the supported
# path — see .claude/skills/cuga-sovereign/references/change-agent.md.
#
# Usage:
#   ./11-patch-agent-image.sh <instance-id> [path/to/forge.env]
#   ./11-patch-agent-image.sh --rollback <instance-id> [path/to/forge.env]
#
# Requires KUBECONFIG pointed at the HUB (platform cluster) for the patch, and
# reads the spoke via SPOKE_KUBECONFIG for verification.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ROLLBACK=false
if [[ "${1:-}" == "--rollback" ]]; then ROLLBACK=true; shift; fi
INSTANCE_ID="${1:-}"
ENV_FILE="${2:-${SCRIPT_DIR}/forge.env}"

if [[ -z "$INSTANCE_ID" ]]; then
  echo "Usage: $0 [--rollback] <instance-id> [path/to/forge.env]"
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file not found: $ENV_FILE"
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

AGENT="agent-${INSTANCE_ID}"
HUB_NS=agent-service-broker
BASE="/spec/patches/0/spec/template/spec/containers/0"
ROLLBACK_DIR="${SCRIPT_DIR}/.rollback"
mkdir -p "$ROLLBACK_DIR"

if ! oc whoami &>/dev/null; then
  echo "ERROR: not logged in. KUBECONFIG must point at the HUB (platform cluster)."
  exit 1
fi

# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------
if [[ "$ROLLBACK" == true ]]; then
  SNAP="${ROLLBACK_DIR}/${AGENT}.json"
  if [[ ! -f "$SNAP" ]]; then
    echo "ERROR: no preflight snapshot at $SNAP — nothing to roll back to."
    echo "       Recover the previous image from the spoke's ReplicaSets instead:"
    echo "         oc get rs -n tenant-\$TENANT_ID -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image"
    exit 1
  fi
  python3 - "$SNAP" "$BASE" > /tmp/.rollback-patch.json <<'PY'
import json, sys
snap, base = sys.argv[1], sys.argv[2]
c = json.load(open(snap))["spec"]["patches"][0]["spec"]["template"]["spec"]["containers"][0]
json.dump([{"op": "replace", "path": base + "/image", "value": c["image"]},
           {"op": "replace", "path": base + "/env", "value": c.get("env", [])}], sys.stdout)
PY
  echo "==> Rolling $AGENT back to its pre-PoC image + env"
  oc patch cugaagent "$AGENT" -n "$HUB_NS" --type=json --patch-file=/tmp/.rollback-patch.json
  rm -f /tmp/.rollback-patch.json
  echo "Done. Verify on the spoke: oc get deploy $AGENT -n tenant-\$TENANT_ID -o jsonpath='{.spec.template.spec.containers[0].image}'"
  exit 0
fi

# ---------------------------------------------------------------------------
# Preflight — the snapshot IS the rollback plan
# ---------------------------------------------------------------------------
REQUIRED_VARS=(NAMESPACE TENANT_ID CLONE_IMAGE MCP_AUDIENCE WORKSPACE_ID)
MISSING=()
for v in "${REQUIRED_VARS[@]}"; do [[ -z "${!v:-}" ]] && MISSING+=("$v"); done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "ERROR: not set in $ENV_FILE: ${MISSING[*]}"
  exit 1
fi

echo "==> Preflight: snapshotting current CR (this is the rollback plan)"
oc get cugaagent "$AGENT" -n "$HUB_NS" -o json > "${ROLLBACK_DIR}/${AGENT}.json"
python3 - "${ROLLBACK_DIR}/${AGENT}.json" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))["spec"]["patches"][0]["spec"]["template"]["spec"]["containers"][0]
print("    merge key (must survive):", c.get("name"))
print("    current image           :", c.get("image"))
print("    env vars carried by CR  :", [e["name"] for e in c.get("env", [])])
PY

echo
echo "==> Strategy check (Recreate means a bad image is downtime, not a stuck rollout)"
STRAT=$(KUBECONFIG="${SPOKE_KUBECONFIG:-$KUBECONFIG}" oc get deploy "$AGENT" -n "tenant-${TENANT_ID}" \
  -o jsonpath='{.spec.strategy.type}' 2>/dev/null || echo "unknown")
echo "    $STRAT"

# ---------------------------------------------------------------------------
# Build the patch. Env is replaced wholesale, so identity vars must be carried
# forward explicitly — dropping INSTANCE_ID/TENANT_ID breaks the agent.
# ---------------------------------------------------------------------------
echo
echo "==> Minting a Forge token for the agent"
# Must run against the SPOKE: the mock-broker pod lives in $NAMESPACE on the
# agent cluster, while this script's own KUBECONFIG points at the hub (that is
# where the CugaAgent CR lives). Without this the mint fails with an
# out-of-bounds jsonpath because `oc get pods -l app=mock-broker` matches
# nothing on the hub.
if [[ -z "${SPOKE_KUBECONFIG:-}" ]]; then
  echo "ERROR: SPOKE_KUBECONFIG must be set — the Forge mock broker runs on the agent cluster,"
  echo "       not the hub this script patches. Example:"
  echo "         export SPOKE_KUBECONFIG=~/dev/sov-core/cuga_mcpcf_poc/.scratch-kube/agent-kubeconfig"
  exit 1
fi
KUBECONFIG="$SPOKE_KUBECONFIG" "${SCRIPT_DIR}/04-mint-token.sh" "$ENV_FILE" >/dev/null
FORGE_TOKEN=$(cat "${SCRIPT_DIR}/.mcpcf-token")
if [[ -z "$FORGE_TOKEN" ]]; then
  echo "ERROR: token mint produced nothing — check the mock-broker pod in $NAMESPACE on the spoke."
  exit 1
fi

# user = forward the caller's own token (per-user identity, needs auth enabled)
# env  = one shared workspace credential
TOKEN_SOURCE="${FORGE_TOKEN_SOURCE:-env}"
echo "==> context_forge.token_source = $TOKEN_SOURCE"

PATCH_FILE=$(mktemp)
trap 'rm -f "$PATCH_FILE"' EXIT
python3 - "${ROLLBACK_DIR}/${AGENT}.json" "$BASE" "$CLONE_IMAGE" "$FORGE_TOKEN" \
         "$NAMESPACE" "$MCP_AUDIENCE" "$WORKSPACE_ID" "$TOKEN_SOURCE" > "$PATCH_FILE" <<'PY'
import json, sys
snap, base, img, tok, ns, aud, ws, ts = sys.argv[1:9]
cur = json.load(open(snap))["spec"]["patches"][0]["spec"]["template"]["spec"]["containers"][0]

# Carry every existing env entry forward, then add/overwrite the Forge ones.
env = {e["name"]: e for e in cur.get("env", [])}
for name, value in [
    ("DYNACONF_CONTEXT_FORGE__ENABLED", "true"),
    # In-cluster Service, not the public route: attaching a tool makes the CUGA
    # registry open its own MCP connection via fastmcp, whose httpx client does
    # real cert verification and never sees context_forge.verify_ssl.
    ("DYNACONF_CONTEXT_FORGE__URL", f"http://forge.{ns}.svc.cluster.local"),
    ("DYNACONF_CONTEXT_FORGE__AUDIENCE", aud),
    ("DYNACONF_CONTEXT_FORGE__WORKSPACE_GROUP", f"ws-{ws}"),
    ("DYNACONF_CONTEXT_FORGE__TOKEN_SOURCE", ts),
    ("DYNACONF_CONTEXT_FORGE__VERIFY_SSL", "false"),
    ("CONTEXT_FORGE_TOKEN", tok),
]:
    env[name] = {"name": name, "value": value}

# --type=json with paths INSIDE the container object keeps `name: cuga`, the
# strategic-merge key. A merge patch that writes a bare {image:...} container
# drops it and the operator fails with "does not contain declared merge key:
# name" while the CR write still reports success. See traps.md.
json.dump([{"op": "replace", "path": base + "/image", "value": img},
           {"op": "replace", "path": base + "/env", "value": list(env.values())}], sys.stdout)
PY

echo "==> Patching $AGENT"
oc patch cugaagent "$AGENT" -n "$HUB_NS" --type=json --patch-file="$PATCH_FILE"

# ---------------------------------------------------------------------------
# Verify the whole ladder. A patched CR proves nothing.
# ---------------------------------------------------------------------------
SPOKE_KC="${SPOKE_KUBECONFIG:-}"
if [[ -z "$SPOKE_KC" ]]; then
  echo
  echo "SPOKE_KUBECONFIG not set — skipping spoke-side verification."
  echo "Verify manually (a patched CR is NOT a changed workload):"
  echo "  oc get deploy $AGENT -n tenant-$TENANT_ID -o jsonpath='{.spec.template.spec.containers[0].image}'"
  echo "  oc get events -n tenant-$TENANT_ID --sort-by=.lastTimestamp | grep -i InternalError"
  exit 0
fi

echo
echo "==> Verifying on the spoke"
sleep 5
echo -n "    deployment image: "
KUBECONFIG="$SPOKE_KC" oc get deploy "$AGENT" -n "tenant-${TENANT_ID}" \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
echo "    InternalError events (the merge-key trap):"
KUBECONFIG="$SPOKE_KC" oc get events -n "tenant-${TENANT_ID}" --sort-by=.lastTimestamp 2>/dev/null \
  | grep -i InternalError | tail -3 || echo "      none"
KUBECONFIG="$SPOKE_KC" oc rollout status "deployment/$AGENT" -n "tenant-${TENANT_ID}" --timeout=300s || true
echo
echo "Rollback if needed:  $0 --rollback $INSTANCE_ID $ENV_FILE"
